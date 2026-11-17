"""Build the inferred edges (stage 3) — the bespoke, non-mechanical half.

(a) correlatesWith — event-study statistics over real price history.
    For every curated theme, take the distinct *dates* on which theme-tagged events
    fired, measure each market asset's realised move over 1/3/5/10 trading days
    from the pre-event close, and attach the resulting *distribution* as a weighted
    edge: mean/median/range, n, consistency, t-stat. Never a point forecast: the
    answer is a distribution, and confidence is tied to analog count and
    consistency.

    Aggregation is per distinct **date**, not per event. Weighting by event would
    let one noisy news day contribute 8,000 observations, inflating n and making
    `consistency` meaningless.

(b) affects — second-order propagation: commodity/macro -> sector, as the
    correlation of daily returns over the price window. This is what carries an oil
    shock (E2) through to sector exposure.

(c) similarTo — analog search. Hybrid and ArangoSearch-first:
    a faceted, explainable score over cameo + actor pair + theme + geo + Goldstein
    bucket, with vector re-rank reserved for the free-text/tone cases. Requires a
    live database (it creates a view), so it is the one part --dry-run cannot
    validate; --dry-run prints the view definition and the scoring AQL instead.

Examples:
  python scripts/build_inferred_edges.py --dry-run     # (a) + (b) offline, no DB
  python scripts/build_inferred_edges.py               # build + write all three
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import json
import pathlib
import statistics

from gdelt_vocab import EPISODES
from build_graph import (
    BENCHMARK_INDEX,
    SECTOR_ETF,
    edge_key,
    iso_date,
    key_safe,
    JsonlSource,
    load_jsonl,
)

# Trading-day horizons for the event study. Short end catches the announcement
# reaction; 10d catches follow-through without drowning in unrelated news.
HORIZONS = (1, 3, 5, 10)

# Below this many observations the cell is reported but flagged: low-confidence
# answers must be visibly low-confidence.
MIN_CONFIDENT_N = 10
# Corpus window (Event.dateAdded min..max). Baselines are measured over this so
# every symbol's reference distribution covers the same period as the analogs.
CORPUS_START = "2018-02-27"
CORPUS_END = "2022-03-10"

# Named measurement windows. `delivered` is the span every published number was
# computed over; it keeps the bare `{symbol}_{horizon}` key so an extended build
# does not invalidate the published figures or the four demo presets. Any other
# window suffixes its key, so both can live in one database and an answer can say
# which span it used.
# `None` means "as far as the data reaches" at that end. Hardcoding the full
# window's bounds means editing this every time the corpus is extended, and
# forgetting to is silent — the baselines just quietly cover less than the corpus.
WINDOWS = {
    "delivered": (CORPUS_START, CORPUS_END),
    "full": (None, None),
}
WINDOW_SUFFIX = {"delivered": ""}          # everything else suffixes with its name

# Fraction of a theme's days, ranked by article volume, treated as "the theme
# actually fired". See theme_day_articles() for why intensity beats presence.
INTENSITY_QUANTILE = 0.25

# Onset anchoring. A first-mention onset is a covered day with no
# coverage in the preceding BURST_GAP_DAYS; the intensity filter looks
# INTENSITY_LOOKAHEAD days past the onset for the burst's peak, because the
# coverage peak trails the event that caused it.
BURST_GAP_DAYS = 3
INTENSITY_LOOKAHEAD = 3

ANALOG_VIEW = "event_analog"


# --- Price handling ----------------------------------------------------------
def price_series(prices: list[dict]) -> dict[str, tuple[list[str], list[float]]]:
    """{symbol: (sorted dates, closes)} using adjusted close where available."""
    by_symbol: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for row in prices:
        px = row.get("adjClose") or row.get("close")
        if px is None:
            continue
        by_symbol[row["symbol"]][row["date"]] = float(px)
    out = {}
    for symbol, series in by_symbol.items():
        dates = sorted(series)
        out[symbol] = (dates, [series[d] for d in dates])
    return out


def forward_return(series: tuple[list[str], list[float]], date: str, horizon: int):
    """Return from the last close STRICTLY BEFORE `date` to `horizon` days later.

    The base has to be the close before the event, so that the event day's own
    reaction falls inside the window — that is where announcement effects land.
    `bisect_right` looked like it did this and only did it for events on days the
    market was shut. For a weekday event it returned that same day's close, so
    the window began *after* the reaction and measured the following days
    instead. The two readings can have opposite signs, and did: the Abqaiq strike
    (Sat 2019-09-14) correctly gave Brent +14.61% because the market was closed,
    while the US ban on Russian oil (Tue 2022-03-08) gave -13.16% — which is not
    the ban at all, but the UAE output announcement that broke the rally the next
    day. `bisect_left` takes the strictly-earlier close in both cases.

    Returns None when the window falls outside the price history.
    """
    dates, closes = series
    i = bisect.bisect_left(dates, date) - 1
    if i < 0 or i + horizon >= len(closes):
        return None
    base, end = closes[i], closes[i + horizon]
    # A percentage return off a non-positive base is undefined, not merely extreme.
    # WTI settled at -$37.63 on 2020-04-20 — inside the corpus window — and the
    # naive formula turns that into a -306% "daily move", which inflated CL=F's
    # baseline volatility to 10.97% against Brent's 2.84% and quietly degraded
    # every statistic touching it (WTI->Energy correlation read 0.25 where Brent
    # read 0.59; the two should be near-identical). Windows touching a
    # non-positive price are dropped rather than clamped: the contract genuinely
    # stopped behaving like a price that day.
    if base <= 0 or end <= 0:
        return None
    return end / base - 1.0


# --- Theme -> event dates ----------------------------------------------------
def theme_day_articles(gkg) -> dict[str, collections.Counter]:
    """{theme: Counter(iso date -> article count)} — daily news *intensity*.

    Intensity, not mere presence, is what discriminates themes. Binary presence
    does not: measured on this corpus, every curated theme fires on 96-111 of the
    112 ingested days, so a presence-keyed study hands every theme almost the same
    date set and therefore almost the same answer. Article volume separates a real
    tariff-news day (hundreds of ECON_TRADE_DISPUTE articles) from a day that
    merely mentions trade in passing.

    Keyed on the article's own publication date — the date a market could react.
    """
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for art in gkg:
        day = iso_date(art.get("DATE"))
        if not day:
            continue
        for theme in art.get("_matchedThemes") or []:
            out[theme][day] += 1
    return out


def high_intensity_days(counts: collections.Counter, quantile: float,
                        floor: int) -> list[str]:
    """The theme's top-`quantile` days by article volume (at least `floor` days).

    Retained for `--anchor peak`, which reproduces the pre-fix numbers so the
    defect below can be demonstrated side by side. Do not use it to anchor.
    """
    if len(counts) < floor:
        return sorted(counts)
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    take = max(floor, int(len(ranked) * quantile))
    return sorted(day for day, _ in ranked[:take])


def intensity_cutoff(counts: collections.Counter, quantile: float, floor: int) -> int:
    """Article-count threshold separating a real burst from passing mention."""
    if not counts:
        return 0
    ranked = sorted(counts.values(), reverse=True)
    take = min(max(floor, int(len(ranked) * quantile)), len(ranked))
    return ranked[take - 1]


def episode_onsets() -> list[str]:
    """Documented onset dates for every scenario in the EPISODES calendar."""
    return sorted({iso for episodes in EPISODES.values() for iso, _ in episodes})


def onset_anchor_days(counts: collections.Counter, quantile: float,
                      floor: int) -> tuple[list[str], int]:
    """Anchor days for the event window: episode onset / first-mention.

    **Why not peak coverage.** News volume is a *lagging* indicator
    of a shock: the wires peak a day or two after the market has already repriced.
    Anchoring there bases the return on an already-moved close and therefore
    measures the decay, inverting the sign on the biggest shocks in the corpus.
    Measured on WTI at the 5-day horizon: the Abqaiq attack reads **+5.91%**
    anchored at its 2019-09-14 onset and **-6.77%** anchored at the 2019-09-16
    coverage peak — the same event, opposite direction.

    So onset locates the window and peak coverage only *filters* it: a candidate
    is kept only if the theme's coverage within `INTENSITY_LOOKAHEAD` days of the
    onset clears the top-quantile cutoff, which is what separates a genuine
    episode from a day that merely mentioned the theme.

    Candidates are the union of two onset definitions:
      * **documented** — dates from the `EPISODES` calendar, the most defensible
        anchor because it is an external fact, not something inferred from the
        data being studied;
      * **first-mention** — the first covered day of a coverage burst, so themes
        the calendar does not enumerate still contribute.

    `forward_return` bases on the last close at or before the anchor, so an onset
    falling on a weekend (Abqaiq was a Saturday) correctly bases on the prior
    close and captures the reaction gap rather than skipping it.
    """
    if not counts:
        return [], 0
    cutoff = intensity_cutoff(counts, quantile, floor)
    covered = set(counts)

    def burst_peak(day: str) -> int:
        base = dt.date.fromisoformat(day)
        return max((counts.get((base + dt.timedelta(days=k)).isoformat(), 0)
                    for k in range(0, INTENSITY_LOOKAHEAD + 1)), default=0)

    candidates: set[str] = set()
    # First-mention: a covered day with no coverage in the preceding BURST_GAP_DAYS.
    for day in counts:
        base = dt.date.fromisoformat(day)
        prior = ((base - dt.timedelta(days=k)).isoformat()
                 for k in range(1, BURST_GAP_DAYS + 1))
        if not any(p in covered for p in prior):
            candidates.add(day)
    # Documented onsets, whether or not that exact day was ingested.
    candidates.update(episode_onsets())

    anchors = sorted(d for d in candidates if burst_peak(d) >= cutoff)
    # Never hand back an empty sample just because the filter was strict.
    if not anchors:
        anchors = sorted(candidates)
    return anchors, cutoff


def trading_days(series: tuple[list[str], list[float]], start: str, end: str) -> list[str]:
    dates, _ = series
    lo = bisect.bisect_left(dates, start)
    hi = bisect.bisect_right(dates, end)
    return dates[lo:hi]


def sample_moves(series, benchmark, days: list[str], horizon: int,
                 use_abnormal: bool) -> tuple[list[float], list[str]]:
    moves, kept = [], []
    for day in days:
        raw = forward_return(series, day, horizon)
        if raw is None:
            continue
        move = raw
        if use_abnormal and benchmark:
            bench = forward_return(benchmark, day, horizon)
            if bench is None:
                continue
            move = raw - bench
        moves.append(move)
        kept.append(day)
    return moves, kept


# --- (a) correlatesWith -----------------------------------------------------
def study_targets(prices: list[dict]) -> list[tuple[str, str, bool]]:
    """(symbol, target _id, use_abnormal) for each studied asset.

    Sector ETFs resolve to their Sector node — the ETF is the tradeable proxy whose
    move we attribute to the sector (docs/schema.md). Companies are skipped: the
    what-if reaches them through partOf/holds, and correlatesWith targets
    Sector/Index/Commodity per the schema.
    """
    classes = {}
    for row in prices:
        classes.setdefault(row["symbol"], row.get("assetClass", "company"))
    targets = []
    for symbol, asset_class in sorted(classes.items()):
        if asset_class == "sector_etf" and symbol in SECTOR_ETF:
            targets.append((symbol, f"Sector/{key_safe(SECTOR_ETF[symbol])}", True))
        elif asset_class == "index":
            # Abnormal return against the benchmark is meaningless for the
            # benchmark itself.
            targets.append((symbol, f"Index/{key_safe(symbol)}",
                            symbol != BENCHMARK_INDEX))
        elif asset_class == "commodity":
            # Commodities do not trade off equity beta; excess-vs-S&P would be a
            # category error, so they stay raw.
            targets.append((symbol, f"Commodity/{key_safe(symbol)}", False))
    return targets


def event_study(gkg, prices: list[dict],
                quantile: float = INTENSITY_QUANTILE,
                anchor: str = "onset") -> tuple[list[dict], dict]:
    """Theme -> asset weighted edges: excess move on high-intensity theme days.

    `move` is a **double difference**, which is what makes it theme-specific:
      1. vs the benchmark (abnormal return, for equity targets), removing market beta
      2. vs the asset's own baseline over every trading day in the corpus window,
         removing that asset's unconditional drift.
    Without step 2 the study reports each asset's sample-period drift under every
    theme alike — measured here as a spurious ~+1.4% 10-day Utilities "effect"
    attached to all 16 themes at t>3.5, which is a fact about 2018-2022, not about
    tariffs.

    Significance is Welch's t against the baseline sample. Note the two samples
    overlap (theme days are a subset of the window), so this is a *screening*
    statistic, not clean inference — it is reported, never hidden behind.
    """
    series = price_series(prices)
    benchmark = series.get(BENCHMARK_INDEX)
    counts = theme_day_articles(gkg)
    targets = study_targets(prices)
    edges, stats = [], collections.Counter()
    if not benchmark:
        stats["no_benchmark"] = 1
    if not counts:
        return edges, stats

    all_days = sorted({d for c in counts.values() for d in c})
    window = (all_days[0], all_days[-1])

    # Baseline: every trading day in the corpus window, not only ingested days.
    baseline: dict[tuple[str, int], list[float]] = {}
    for symbol, _target, use_abnormal in targets:
        if symbol not in series:
            continue
        days = trading_days(series[symbol], *window)
        for horizon in HORIZONS:
            moves, _ = sample_moves(series[symbol], benchmark, days, horizon, use_abnormal)
            baseline[(symbol, horizon)] = moves

    for theme, day_counts in sorted(counts.items()):
        if anchor == "peak":
            # Pre-fix behaviour, kept only so the defect can be shown side by
            # side: anchoring ON the coverage peak inverts the sign of the
            # largest shocks in the corpus.
            hot = high_intensity_days(day_counts, quantile, MIN_CONFIDENT_N)
            cutoff = min(day_counts[d] for d in hot) if hot else 0
        else:
            hot, cutoff = onset_anchor_days(day_counts, quantile, MIN_CONFIDENT_N)
        for symbol, target, use_abnormal in targets:
            if symbol not in series:
                continue
            for horizon in HORIZONS:
                moves, kept = sample_moves(series[symbol], benchmark, hot,
                                           horizon, use_abnormal)
                base = baseline.get((symbol, horizon)) or []
                if len(moves) < 2 or len(base) < 2:
                    stats["cell_too_thin"] += 1
                    continue
                mean = statistics.fmean(moves)
                base_mean = statistics.fmean(base)
                excess = mean - base_mean
                stdev = statistics.stdev(moves)
                base_stdev = statistics.stdev(base)
                # Welch's t on the difference of means.
                se = (stdev ** 2 / len(moves) + base_stdev ** 2 / len(base)) ** 0.5
                t_stat = excess / se if se else None
                same_sign = sum(1 for m in moves if (m >= 0) == (excess >= 0))
                src = f"Theme/{key_safe(theme)}"
                edges.append({
                    # (theme, asset, horizon) is this edge's natural identity —
                    # 16 themes x 17 assets x 4 horizons = the 1,088 rows. An
                    # explicit _key makes the rebuild an upsert instead of a
                    # duplicating insert (see build_graph.edge_key).
                    "_key": edge_key(src, target, f"h{horizon}"),
                    "_from": src,
                    "_to": target,
                    "horizonDays": horizon,
                    # The causal weight the what-if traverses.
                    "move": round(excess, 6),
                    "conditionalMove": round(mean, 6),
                    "baselineMove": round(base_mean, 6),
                    "moveMedian": round(statistics.median(moves), 6),
                    "moveRange": [round(min(moves), 6), round(max(moves), 6)],
                    "moveStdev": round(stdev, 6),
                    "n": len(moves),
                    "baselineN": len(base),
                    "consistency": round(same_sign / len(moves), 4),
                    "tStat": round(t_stat, 3) if t_stat is not None else None,
                    "lowConfidence": len(moves) < MIN_CONFIDENT_N
                                     or (t_stat is None or abs(t_stat) < 2),
                    "proxySymbol": symbol,
                    "intensityCutoff": cutoff,
                    "anchorMode": anchor,
                    "window": list(window),
                    "method": ("event-study/excess-vs-baseline+"
                               + BENCHMARK_INDEX if use_abnormal
                               else "event-study/excess-vs-baseline"),
                    # Citation trail: the highest-intensity news days in
                    # the sample. Deliberately ranked by intensity, not by |move| —
                    # citing the biggest moves would be selecting confirming evidence.
                    "analogDates": sorted(kept, key=lambda d: -day_counts[d])[:10],
                })
                stats["cells"] += 1
    return edges, stats


# --- (b) affects -------------------------------------------------------------
def commodity_affects(prices: list[dict]) -> list[dict]:
    """Commodity/macro -> Sector propagation weight = daily-return correlation."""
    series = price_series(prices)
    classes = {row["symbol"]: row.get("assetClass", "company") for row in prices}
    units = {row["symbol"]: row.get("unit") for row in prices if row.get("unit")}

    def daily_returns(symbol) -> dict[str, float]:
        dates, closes = series[symbol]
        if units.get(symbol) == "percent":
            # A yield's day is a change in level, not a percentage of the level:
            # 0.05% -> 0.08% is +3bp, and as a "return" it would read +60%.
            return {dates[i]: closes[i] - closes[i - 1] for i in range(1, len(closes))}
        # Same guard as forward_return: a return off a non-positive base is
        # undefined. WTI's 2020-04-20 settlement at -$37.63 otherwise injects a
        # -306% observation that dominates the correlation — it read 0.25 for
        # CL=F -> Energy against 0.59 for Brent, two series that normally move
        # near-identically.
        return {dates[i]: closes[i] / closes[i - 1] - 1.0
                for i in range(1, len(closes))
                if closes[i - 1] > 0 and closes[i] > 0}

    edges = []
    sources = [s for s, c in classes.items() if c in ("commodity", "macro") and s in series]
    for source in sorted(sources):
        src_ret = daily_returns(source)
        for etf, sector in sorted(SECTOR_ETF.items()):
            if etf not in series:
                continue
            etf_ret = daily_returns(etf)
            shared = sorted(src_ret.keys() & etf_ret.keys())
            if len(shared) < 30:
                continue
            xs = [src_ret[d] for d in shared]
            ys = [etf_ret[d] for d in shared]
            try:
                corr = statistics.correlation(xs, ys)
            except statistics.StatisticsError:
                continue
            coll = "Commodity" if classes[source] == "commodity" else "MacroVariable"
            src = f"{coll}/{key_safe(source)}"
            dst = f"Sector/{key_safe(sector)}"
            edges.append({
                # (source, sector) is the natural identity: 7 sources x 11
                # sectors = the 77 rows.
                "_key": edge_key(src, dst),
                "_from": src,
                "_to": dst,
                "corr": round(corr, 4),
                "n": len(shared),
                "window": [shared[0], shared[-1]],
                "method": ("daily-change-correlation" if units.get(source) == "percent"
                           else "daily-return-correlation"),
            })
    return edges


# --- (c) similarTo ----------------------------------------------------------
# ArangoSearch-first: a faceted score whose every term is inspectable,
# which is why it leads and the vector index only re-ranks. Weights are an open
# question; these are a defensible starting point, not a tuned result.
ANALOG_WEIGHTS = {"cameo": 3.0, "cameoRoot": 1.5, "dyad": 3.0,
                  "theme": 2.0, "geo": 1.0, "goldstein": 1.0}

ANALOG_VIEW_DEFINITION = {
    "links": {
        "Event": {
            "analyzers": ["identity"],
            "includeAllFields": False,
            "fields": {
                "cameo": {}, "cameoRoot": {}, "actorFromCode": {}, "actorToCode": {},
                "geoCountry": {}, "themes": {}, "goldstein": {}, "dateAdded": {},
                "avgTone": {}, "isDayAnchor": {},
            },
        }
    }
}

ANALOG_AQL = """
/* Faceted analog retrieval — every term is visible in the returned score, which is
 * what makes the downstream estimate auditable. */
FOR ev IN event_analog
  SEARCH ev.cameo == @cameo OR ev.cameoRoot == @cameoRoot
      OR (ev.actorFromCode == @actorFrom AND ev.actorToCode == @actorTo)
      OR ev.themes IN @themes
  LET score = @wCameo    * (ev.cameo == @cameo ? 1 : 0)
            + @wRoot     * (ev.cameoRoot == @cameoRoot ? 1 : 0)
            + @wDyad     * ((ev.actorFromCode == @actorFrom
                             AND ev.actorToCode == @actorTo) ? 1 : 0)
            + @wTheme    * LENGTH(INTERSECTION(ev.themes, @themes)) / @themeCount
            + @wGeo      * (ev.geoCountry == @geo ? 1 : 0)
            + @wGoldstein* (ABS(ev.goldstein - @goldstein) <= 2 ? 1 : 0)
  FILTER score > 0
  SORT score DESC, ev.numMentions DESC
  LIMIT @topK
  RETURN {event: ev._key, date: ev.dateAdded, score,
          matched: {cameo: ev.cameo == @cameo,
                    dyad: ev.actorFromCode == @actorFrom AND ev.actorToCode == @actorTo,
                    themes: INTERSECTION(ev.themes, @themes)}}
"""


# --- (c) PriceSeries + BaselineStats -----------------------------------------
# W1 / G11. The demo answer used to be read from theme-level `correlatesWith`
# edges, so it was identical whichever analogs were retrieved and the cited
# events were decorative. To compute the outcome over the analogs a
# query actually returns, the price history has to be reachable from AQL.
#
# Materialising Event->Sector move edges was rejected: ~5.7M edges, almost none
# ever traversed, and every corpus widening would trigger a full rebuild. The
# series itself is tiny by comparison — 26 symbols, ~2,263 days each — so it is
# stored whole and the arithmetic happens at query time.
def _calendar_index(dates: list[str]) -> dict[str, int]:
    """{every calendar date -> index of the last trading day on or before it}."""
    if not dates:
        return {}
    out: dict[str, int] = {}
    day = dt.date.fromisoformat(dates[0])
    last = dt.date.fromisoformat(dates[-1])
    i = 0
    while day <= last:
        iso = day.isoformat()
        while i + 1 < len(dates) and dates[i + 1] <= iso:
            i += 1
        out[iso] = i
        day += dt.timedelta(days=1)
    return out


def price_series_docs(prices: list[dict]) -> list[dict]:
    """One document per symbol: parallel dates/closes plus a date->index map.

    `pos` maps EVERY calendar date in the window to the index of the last trading
    day on or before it — not just the trading days. That is deliberate: events
    happen at weekends (2019-07-20, one of the E1 analogs, is a Saturday), and an
    exact trading-day lookup silently drops them. Measured before this was fixed:
    2 of 8 E1 analogs vanished, with no error, just a smaller n.

    `prev` is the same map shifted to the last trading day STRICTLY BEFORE each
    calendar date, and it is the one an event window should start from: basing the
    window on `pos` for a weekday event puts the base at that day's own close,
    which is after the reaction being measured. `pos` is still what you want to
    display ("which trading day did this event land on"); `prev` is what you want
    to measure from. Keeping both named makes the choice explicit at each call
    site rather than hiding it in an off-by-one.

    This matches `forward_return()`, which uses `bisect_left(dates, date) - 1`.
    The stored statistic and the query-time one have to agree about which close
    an event maps to, or any difference between them is an artefact rather than
    a finding.

    Cost is ~3,280 calendar entries per symbol against ~2,263 trading days — a
    third more, for an exact lookup instead of a binary search in AQL.

    Mirrors `price_series()` exactly — adjusted close where available — so the
    query-time number and the stored `correlatesWith` statistic are computed from
    the same input and any divergence is a real difference, not a data mismatch.
    """
    classes = {}
    for row in prices:
        classes.setdefault(row["symbol"], row.get("assetClass", "company"))
    units = {row["symbol"]: row.get("unit") for row in prices if row.get("unit")}
    labels = {row["symbol"]: (row.get("name"), row.get("group"))
              for row in prices if row.get("group")}
    docs = []
    for symbol, (dates, closes) in sorted(price_series(prices).items()):
        docs.append({
            "_key": key_safe(symbol),
            "symbol": symbol,
            "assetClass": classes.get(symbol, "company"),
            # "percent" for yields: a day is a change in basis points.
            "unit": units.get(symbol),
            # exposure series only: what it is and why it is here.
            "name": labels.get(symbol, (None, None))[0],
            "group": labels.get(symbol, (None, None))[1],
            "dates": dates,
            "closes": closes,
            "pos": (cal := _calendar_index(dates)),
            # last trading day strictly before each calendar date (see docstring)
            "prev": {d: (i - 1 if dates[i] == d else i) for d, i in cal.items()},
            "firstDate": dates[0] if dates else None,
            "lastDate": dates[-1] if dates else None,
            "n": len(dates),
        })
    return docs


def baseline_docs(prices: list[dict], window: str = "delivered") -> list[dict]:
    """Unconditional mean/stdev of the forward return, per (symbol, horizon).

    The analog set is only interesting as an *excess* over what the asset does on
    an average day, and a t-statistic needs a reference distribution. Uses the same
    `sample_moves` path as the event study, including the abnormal-return
    convention from `study_targets` — sector ETFs measured against the benchmark,
    commodities not.

    Restricted to the CORPUS window rather than each symbol's full price history.
    Two reasons, one of them a bug found on the first run. Comparability: XLC
    (Communication Services) only lists from 2018-06-19, so its baseline spanned
    1,639 days against 2,258 for every other sector — a different market regime,
    which makes "excess over baseline" mean something different per sector and
    quietly favoured XLC in every hypothetical tested. And relevance: a baseline is
    meant to say what a normal day looks like *in the period the analogs come
    from*, not across a decade that mostly predates the corpus.
    """
    series = price_series(prices)
    benchmark = series.get(BENCHMARK_INDEX)
    docs = []
    for symbol, target_id, use_abnormal in study_targets(prices):
        s = series.get(symbol)
        if not s:
            continue
        w_start, w_end = WINDOWS[window]
        days = trading_days(s, w_start or s[0][0], w_end or s[0][-1])
        for horizon in HORIZONS:
            moves, _ = sample_moves(s, benchmark, days, horizon, use_abnormal)
            if len(moves) < 2:
                continue
            mean = statistics.fmean(moves)
            docs.append({
                "_key": f"{key_safe(symbol)}_{horizon}"
                        + WINDOW_SUFFIX.get(window, f"_{window}"),
                "windowId": window,
                "symbol": symbol,
                "target": target_id,
                "horizonDays": horizon,
                "useAbnormal": use_abnormal,
                "mean": round(mean, 8),
                "stdev": round(statistics.pstdev(moves), 8),
                "n": len(moves),
                "window": [days[0], days[-1]],
                "coverage": round(len(days) / max(1, len(s[0])), 3),
                "method": "unconditional-forward-return/corpus-window",
            })
    return docs


def write_docs(database, collection: str, docs: list[dict], batch: int = 500,
               window: str | None = None) -> None:
    """Replace the contents of a plain document collection, idempotently.

    `window` scopes the replacement. Without it the collection is truncated, which
    is right when one build owns the whole thing. With it, only rows belonging to
    that window are removed — because two measurement windows share the collection
    and truncating would delete the delivered figures every write-up
    quotes.
    """
    if not database.has_collection(collection):
        database.create_collection(collection)
    col = database.collection(collection)
    if window is None:
        col.truncate()
    else:
        database.aql.execute(
            "FOR d IN @@c FILTER NOT_NULL(d.windowId, 'delivered') == @w "
            "REMOVE d IN @@c",
            bind_vars={"@c": collection, "w": window})
    for i in range(0, len(docs), batch):
        col.insert_many(docs[i:i + batch], overwrite=True)


def ensure_analog_view(database) -> None:
    views = {v["name"] for v in database.views()}
    if ANALOG_VIEW in views:
        database.update_view(ANALOG_VIEW, ANALOG_VIEW_DEFINITION)
        print(f"[inferred] updated ArangoSearch view '{ANALOG_VIEW}'")
    else:
        database.create_arangosearch_view(ANALOG_VIEW, ANALOG_VIEW_DEFINITION)
        print(f"[inferred] created ArangoSearch view '{ANALOG_VIEW}'")


def write_edges(database, collection: str, edges: list[dict], batch: int = 5000) -> None:
    if not edges:
        return
    coll = database.collection(collection)
    for i in range(0, len(edges), batch):
        coll.import_bulk(edges[i:i + batch], on_duplicate="update")
    print(f"  [edges] {collection:<16} {len(edges):>9,}")


def report(correlates: list[dict], affects: list[dict], stats: dict) -> None:
    print(f"=== correlatesWith: {len(correlates):,} edges "
          f"(theme x asset x horizon) ===")
    confident = [e for e in correlates if not e["lowConfidence"]]
    print(f"  confident cells    {len(confident):,} / {len(correlates):,}")
    if stats:
        print("  " + ", ".join(f"{k}={v:,}" for k, v in sorted(stats.items())))
    strong = sorted(confident, key=lambda e: -abs(e["move"]))[:12]
    if strong:
        print("  strongest confident signals (theme -> asset, horizon):")
        for e in strong:
            theme = e["_from"].split("/", 1)[1]
            asset = e["_to"].split("/", 1)[1]
            print(f"    {theme:<38} -> {asset:<24} {e['horizonDays']:>2}d  "
                  f"excess={e['move']*100:+6.2f}%  (cond={e['conditionalMove']*100:+5.2f}% "
                  f"base={e['baselineMove']*100:+5.2f}%)  n={e['n']:>3}  "
                  f"consist={e['consistency']:.2f}  t={e['tStat']}")
    print(f"=== affects: {len(affects):,} edges (commodity/macro -> sector) ===")
    for e in sorted(affects, key=lambda x: -abs(x["corr"]))[:6]:
        print(f"    {e['_from']:<22} -> {e['_to']:<28} corr={e['corr']:+.3f} n={e['n']:,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--baselines-only", action="store_true",
                    help="write BaselineStats for --window and nothing else. The "
                         "inferred edges span whatever the GKG covers, so a full "
                         "rebuild would recompute correlatesWith/affects across the "
                         "extended corpus and change the delivered demo's graph")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="delivered",
                    help="measurement window for BaselineStats. 'delivered' keeps "
                         "the published numbers reproducible; 'full' spans the "
                         "whole corpus and suffixes its keys so both coexist")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute the event study + affects offline; print the "
                         "analog view/AQL; write nothing")
    ap.add_argument("--anchor", choices=("onset", "peak"), default="onset",
                    help="event-window anchor: episode onset / first-mention "
                         "(default) or the pre-fix coverage peak")
    args = ap.parse_args()

    data = pathlib.Path(args.data)
    # STREAM the GKG; never slurp it. `load_jsonl` on these files is what took a
    # machine down on 2026-08-24 (see JsonlSource in build_graph.py), and this
    # script kept doing it after build_graph was fixed. At the extended corpus —
    # 14.3GB of GKG, 4-6x inflated as Python objects — it is not survivable on a
    # 48GB machine: killed at 12.8GB and climbing after one minute.
    #
    # events.jsonl is not read at all. `event_study` took an `events` argument and
    # never referenced it, so 8.2GB was being loaded to be ignored.
    gkg = JsonlSource(data / "gkg.jsonl")
    prices = load_jsonl(data / "prices.jsonl")
    print(f"[inferred] input: {len(gkg):,} gkg articles (streamed), "
          f"{len(prices):,} price rows")
    if not prices:
        print("  no prices — run scripts/market_data_pull.py first "
              "(no prices => no causal weights)")
        return 1

    correlates, stats = event_study(gkg, prices, anchor=args.anchor)
    affects = commodity_affects(prices)
    series_docs = price_series_docs(prices)
    baselines = baseline_docs(prices, args.window)
    report(correlates, affects, stats)
    print(f"[inferred] PriceSeries: {len(series_docs)} symbols "
          f"({sum(d['n'] for d in series_docs):,} closes) · "
          f"BaselineStats: {len(baselines)} (symbol, horizon) pairs")

    if args.dry_run:
        print(f"=== ArangoSearch view '{ANALOG_VIEW}' definition ===")
        print(json.dumps(ANALOG_VIEW_DEFINITION, indent=2))
        print("=== analog scoring AQL ===" + ANALOG_AQL)
        print(f"=== analog weights === {ANALOG_WEIGHTS}")
        print("[inferred] dry run — nothing written")
        return 0

    from _common import db

    database = db()
    if not args.baselines_only:
        # These span whatever the GKG covers, not the measurement window. Rebuilding
        # them against an extended corpus rewrites the graph the delivered demo
        # traverses, so adding a second window's baselines must not touch them.
        write_edges(database, "correlatesWith", correlates)
        write_edges(database, "affects", affects)
        # This script owns `affects` outright, so an edge it no longer produces is
        # stale, not someone else's. Upsert alone left them in place: the monthly
        # CPI and Fed-funds series were dropped on 2026-11-16 and their 22 edges,
        # correlated on percentage "returns" of a rate, would have stayed.
        stale = list(database.aql.execute(
            "FOR e IN affects FILTER e._key NOT IN @keep REMOVE e IN affects "
            "RETURN OLD._key", bind_vars={"keep": [e["_key"] for e in affects]}))
        if stale:
            print(f"  [edges] affects          {len(stale):>9,} stale removed")
    # W1/G11: the query-time outcome path. Plain documents, keyed lookup only —
    # deliberately not part of the named graph, nothing traverses them.
    if not args.baselines_only:
        write_docs(database, "PriceSeries", series_docs)
    write_docs(database, "BaselineStats", baselines, window=args.window)
    if not args.baselines_only:
        ensure_analog_view(database)
        database.collection("correlatesWith").add_persistent_index(
            fields=["horizonDays", "lowConfidence"], sparse=True)
    print("[inferred] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
