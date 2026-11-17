"""Arithmetic and packaging invariants. No database, no network, sub-second.

Every test here corresponds to a defect that actually occurred during this project.
They are not chosen for coverage; they are chosen because each one was expensive.
"""
from __future__ import annotations

import json
import os
import shutil
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from build_inferred_edges import forward_return  # noqa: E402
from multiple_testing import t_two_sided_p  # noqa: E402


# --------------------------------------------------------------------------
# The event-study window. This is the one that flipped signs on the headline
# number: the base must be the last close STRICTLY BEFORE the event, so the
# event day's own reaction falls inside the measured window.
# --------------------------------------------------------------------------
DATES = ["2019-09-12", "2019-09-13", "2019-09-16", "2019-09-17", "2019-09-18"]
CLOSES = [60.38, 60.22, 69.02, 64.55, 63.60]


def test_weekend_event_anchors_on_the_prior_friday():
    """Abqaiq fell on Saturday; the window must run Friday -> Monday."""
    got = forward_return((DATES, CLOSES), "2019-09-14", 1)
    assert got == pytest.approx(69.02 / 60.22 - 1, rel=1e-9)


def test_weekday_event_anchors_before_itself_not_on_itself():
    """The defect: anchoring ON the event day measures the days AFTER the reaction.

    For 2019-09-16 the base must be Friday's 60.22, not Monday's own 69.02.
    Anchoring on the day itself gives -6.5% where the truth is +14.6% — opposite
    signs, which is how this survived review for months.
    """
    got = forward_return((DATES, CLOSES), "2019-09-16", 1)
    assert got > 0, "a weekday event must not be anchored on its own close"
    assert got == pytest.approx(69.02 / 60.22 - 1, rel=1e-9)


def test_non_positive_prices_are_dropped_not_clamped():
    """WTI settled at -$37.63 on 2020-04-20. A return off a negative base is
    undefined, and the naive formula turns it into a -306% 'daily move' that
    corrupts every baseline touching it."""
    dates = ["2020-04-17", "2020-04-20", "2020-04-21"]
    assert forward_return((dates, [18.27, -37.63, 10.01]), "2020-04-20", 1) is None
    assert forward_return((dates, [18.27, -37.63, 10.01]), "2020-04-17", 1) is None


def test_window_outside_the_price_history_returns_none():
    assert forward_return((DATES, CLOSES), "2019-09-18", 10) is None
    assert forward_return((DATES, CLOSES), "2015-01-01", 1) is None


# --------------------------------------------------------------------------
# Multiple-testing. The normal approximation says the strongest cell in the
# sweep survives correction; the exact Student-t tail says it misses — by 139x on
# the 5.52M-event corpus, and by 2.5x on the 21.1M-event one.
# The headline claim depends on which one is used.
# --------------------------------------------------------------------------
def test_exact_t_tail_is_fatter_than_normal_at_small_n():
    """t=6.36 on 13 observations. Under a normal it looks decisive; under t(12)
    it is 1-in-28,000, which is two orders of magnitude short of the bar."""
    p = t_two_sided_p(6.36, 12, steps=20000)
    assert 1e-5 < p < 1e-4, f"expected ~3.6e-5, got {p:.2e}"


def test_t_tail_shrinks_as_degrees_of_freedom_grow():
    assert t_two_sided_p(3.0, 5, steps=20000) > t_two_sided_p(3.0, 500, steps=20000)


def test_t_tail_is_symmetric():
    assert t_two_sided_p(2.5, 30, steps=20000) == pytest.approx(
        t_two_sided_p(-2.5, 30, steps=20000), rel=1e-12)


# --- documentation links -----------------------------------------------------
# A link to a file that is not in the repository 404s for every reader.

@pytest.mark.parametrize("doc", ["README.md", "tests/README.md", "starter/README.md",
                                 "docs/operations.md", "docs/schema.md",
                                 "docs/market-data.md"])
def test_relative_links_resolve_on_this_branch(doc):
    import re
    text = (REPO / doc).read_text()
    base = (REPO / doc).parent
    broken = [
        t for t in re.findall(r"\]\((?!https?:|mailto:|#)([^)#]+)", text)
        if not (base / t.strip()).exists()
    ]
    assert not broken, f"{doc} links to missing paths: {broken}"


# --- the verdict, when the class runs out ------------------------------------
# A narrow class exhausts the ladder: every rung returns the same events, and the
# old logic read the identical rows as "the same sector leads at every sample
# size". Houthis + Saudi Aramco returned Real Estate t=3.75, "holds up as the
# sample grows", off one extra observation between the first two rungs.

def _verdict():
    import importlib.util
    spec = importlib.util.spec_from_file_location("appmain", REPO / "app" / "main.py")
    try:
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.verdict
    except Exception:
        pytest.skip("app.main needs its runtime deps")


def test_exhausted_ladder_is_not_a_finding():
    v = _verdict()
    sweep = [{"n": 7, "sector": "Real Estate", "tStat": 3.75, "looksSignificant": True},
             {"n": 8, "sector": "Real Estate", "tStat": 4.44, "looksSignificant": True},
             {"n": 8, "sector": "Real Estate", "tStat": 4.44, "looksSignificant": True},
             {"n": 8, "sector": "Real Estate", "tStat": 4.44, "looksSignificant": True}]
    out = v(sweep)
    assert out["call"] == "too-few", f"a stalled ladder was called {out['call']}"
    assert "8" in out["headline"]


def test_a_real_ladder_still_gets_judged():
    """The guard must not swallow classes that genuinely grow."""
    v = _verdict()
    sweep = [{"n": n, "sector": "Energy", "tStat": t, "looksSignificant": abs(t) >= 2}
             for n, t in ((8, 2.4), (12, 2.9), (20, 3.3), (40, 3.8), (80, 4.1))]
    assert v(sweep)["call"] == "detectable"
    fading = [{"n": n, "sector": s, "tStat": t, "looksSignificant": abs(t) >= 2}
              for n, s, t in ((8, "Energy", 2.4), (12, "Utilities", 1.9),
                              (20, "Energy", 1.2), (40, "Materials", 0.8),
                              (80, "Energy", 0.3))]
    assert v(fading)["call"] == "not-detectable"


# --- the environment must reach every writer ---------------------------------
# build_graph's Python side honoured ARANGO_DATABASE while arangoimport — the
# half that writes the rows — read .env directly. A 12M-event corpus intended for
# a staging database went into the production one on 2026-09-01 that way.

def test_arangoimport_honours_the_process_environment(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aimp", REPO / "scripts" / "arangoimport_load.py")
    try:
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    except Exception:
        pytest.skip("arangoimport_load needs its deps")
    if not (REPO / ".env").exists():
        pytest.skip("needs a .env to override")
    monkeypatch.setenv("ARANGO_DATABASE", "some_staging_db")
    assert m._conn()["database"] == "some_staging_db", \
        "arangoimport ignored ARANGO_DATABASE and would write to the .env database"



# --- the corpus refresh ------------------------------------------------------
# The 2013 extension's pull exited 0 with seven days missing, visible only as
# FAILED lines in its log. The refresh reads that log itself rather than trusting
# the exit code, and an empty day counts as missing too.

def test_refresh_starts_the_day_after_the_corpus_ends():
    import datetime as dt
    from refresh_corpus import plan_window
    assert plan_window("2026-08-31", dt.date(2026, 11, 2), None) == \
        ("2026-09-01", "2026-10-31")
    assert plan_window("2026-08-31", dt.date(2026, 11, 2), "2026-09-30") == \
        ("2026-09-01", "2026-09-30")


def test_refresh_finds_failed_empty_and_half_pulled_days():
    from refresh_corpus import missing_days
    log = "\n".join([
        "  [events] 2026-09-01: 2,684 matched, 2,684 new (1/4 days)",
        "  [events] 2026-09-02 FAILED: ReadTimeout: timed out",
        "  [events] 2026-09-03: 0 matched, 0 new (3/4 days)",
        "  [events] 2026-09-04: 1,210 matched, 1,210 new (4/4 days)",
        "  [gkg] 2026-09-01: 1,408 matched, 1,408 new (1/3 days)",
        "  [gkg] 2026-09-04 FAILED: ChunkedEncodingError: broken pipe",
    ])
    # 09-02 failed, 09-03 is empty, 09-04 has events but lost its GKG, and 09-05
    # never appears in the log at all.
    assert missing_days(log, "2026-09-01", "2026-09-05") == \
        ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"]


def test_headline_register_matches_its_own_figure_list():
    """A figure added to headline_figures.py but never written to the register is
    one the refresh cannot report as moved."""
    from headline_figures import EVENT_FIGURES, REGISTER
    if not REGISTER.exists():
        pytest.skip("no register yet")
    have = set(json.loads(REGISTER.read_text())["figures"])
    missing = sorted({f[0] for f in EVENT_FIGURES} - have)
    assert not missing, f"run headline_figures.py --write: {missing}"


# --- the sweep's strongest cell is the one with the smallest p ---------------
# Ranked by |t|, IRN->IRQ (t -6.81, n=14) led and the published margin read 101x;
# PSE->ISR (t -6.16, n=37) is thirty times stronger evidence and reads 3.4x.

def test_strongest_cell_is_ranked_by_p_not_by_t():
    from multiple_testing import rank_by_p
    cells = [{"t": -6.81, "n": 14, "id": "small-n"},
             {"t": -6.16, "n": 37, "id": "large-n"},
             {"t": 2.0, "n": 40, "id": "noise"}]
    ranked = rank_by_p(cells)
    assert [r["id"] for r in ranked] == ["large-n", "small-n"]
    assert ranked[0]["p"] < ranked[1]["p"] / 10


# --- the portfolio panel -----------------------------------------------------
# The holdings are measured from the same anchor as everything else — the last
# close STRICTLY BEFORE the day — and a weighted average over analogs must not
# quietly drop a holding that has no price.

def _series(dates, closes):
    prev = {}
    for k, d in enumerate(dates):
        prev[d] = k - 1 if k else None
    return {"closes": closes, "prev": {d: i for d, i in prev.items() if i is not None}}


def test_portfolio_day_impact_uses_the_close_before_the_day():
    sys.path.insert(0, str(REPO / "app"))
    from portfolio import day_impact
    days = ["2019-09-13", "2019-09-16", "2019-09-17"]
    series = {"XOM": _series(days, [100, 110, 108]),
              "XLK": _series(days, [50, 49, 50]),
              "_GSPC": _series(days, [3000, 2990, 3000])}
    holdings = [{"ticker": "XOM", "weight": 0.5}, {"ticker": "XLK", "weight": 0.5}]
    out = day_impact(holdings, series, "2019-09-16", 1)
    by = {r["ticker"]: r for r in out["rows"]}
    assert by["XOM"]["movePct"] == 10.0            # 100 -> 110, the day's own move
    assert by["XLK"]["contributionPct"] == -1.0    # -2% at half weight
    assert out["totalPct"] == 4.0 and out["benchmarkPct"] == pytest.approx(-0.33)


def test_portfolio_analogs_skip_a_date_with_a_holding_unpriced():
    from portfolio import analog_impact
    days = ["2020-01-02", "2020-01-03", "2020-01-06"]
    series = {"A": _series(days, [10, 11, 12]),
              "B": _series(days[1:], [20, 21]),           # no price before 01-03
              "_GSPC": _series(days, [100, 101, 102])}
    holdings = [{"ticker": "A", "weight": 0.5}, {"ticker": "B", "weight": 0.5}]
    out = analog_impact(holdings, series, ["2020-01-03", "2020-01-06"], 1)
    assert [a["date"] for a in out["analogs"]] == ["2020-01-06"]
    assert out["n"] == 1


def test_verdict_prose_names_series_the_way_a_reader_would():
    from app.main import series_name
    assert series_name("BZ=F") == "Brent crude"
    assert series_name("Consumer_Staples") == "Consumer Staples"



# --- rates move in basis points ----------------------------------------------
# The first FRED pull fed yields through the percentage-return path, so a Fed
# funds move from 0.05% to 0.08% read as +60% and CPI, a monthly print, was
# correlated with daily sector returns. Both are wrong in kind, not degree.

def test_a_yield_moves_in_basis_points_and_the_dollar_in_percent():
    sys.path.insert(0, str(REPO / "app"))
    from macro import change
    assert change({"unit": "percent", "closes": [0.05, 0.08]}, 0, 1) == pytest.approx(3.0)
    assert change({"unit": "index", "closes": [100.0, 101.0]}, 0, 1) == pytest.approx(1.0)


def test_affects_correlates_rate_changes_not_rate_returns():
    from build_inferred_edges import commodity_affects
    days = [f"2020-{m:02d}-{d:02d}" for m in (1, 2) for d in range(1, 29)]
    # A yield near zero: as returns it swings wildly, as changes it tracks the ETF.
    level = [0.05 + 0.01 * (k % 3) for k in range(len(days))]
    etf = [100 + (level[k] - 0.05) * 100 for k in range(len(days))]
    prices = ([{"symbol": "DGS2", "assetClass": "macro", "unit": "percent",
                "date": d, "close": v} for d, v in zip(days, level)]
              + [{"symbol": "XLF", "assetClass": "sector_etf", "date": d, "close": v}
                 for d, v in zip(days, etf)])
    edges = commodity_affects(prices)
    assert len(edges) == 1 and edges[0]["method"] == "daily-change-correlation"
    assert edges[0]["corr"] > 0.95


# --- plain English, read by rule ---------------------------------------------

@pytest.mark.parametrize("q, want", [
    ("What if the Houthis attack Saudi Aramco?",
     {"mode": "class", "actorFrom": "YEM", "cameo": "190", "actorTo": "SAU",
      "group": "Houthis", "target": "Saudi Aramco", "horizon": 5}),
    ("What happens to my portfolio if the US imposes new tariffs on China?",
     {"mode": "class", "actorFrom": "USA", "cameo": "163", "actorTo": "CHN"}),
    # passive voice: the actor after "by" is the one doing it
    ("What if Saudi Arabia is attacked by Iran with drones?",
     {"actorFrom": "IRN", "cameo": "195", "actorTo": "SAU"}),
    # a group on the receiving side cannot narrow the attacker
    ("Israel strikes Hezbollah in Lebanon",
     {"actorFrom": "ISR", "actorTo": "LBN", "group": None}),
    ("If Iran blockades the Strait of Hormuz, what do stocks do over two weeks?",
     {"actorFrom": "IRN", "cameo": "172", "horizon": 10}),
    # "us" the pronoun is not the United States; "U.S." is
    ("Does it hurt us if China accuses Israel?", {"actorFrom": "CHN", "actorTo": "ISR"}),
    ("Russia threatens the U.S. — impact over 3 days?",
     {"actorFrom": "RUS", "cameo": "138", "actorTo": "USA", "horizon": 3}),
    ("What happened on 14 September 2019?", {"mode": "event", "date": "2019-09-14"}),
    ("what happened at Abqaiq", {"mode": "event", "date": "2019-09-14", "horizon": 1}),
    # a named event WITH an action is a class question about events like it
    ("What if the Houthis hit Abqaiq again?", {"mode": "class", "actorFrom": "YEM"}),
])
def test_plain_english_reads_into_the_form(q, want):
    sys.path.insert(0, str(REPO / "app"))
    from nlq import parse
    got = parse(q, ["Saudi Aramco", "Abqaiq", "Jazan"])
    assert {k: got.get(k) for k in want} == want, got["reading"]


def test_every_named_event_the_parser_knows_is_an_app_episode():
    from nlq import EPISODES
    from app.main import OIL_EPISODES, TARIFF_EPISODES
    known = {e["d"] for e in OIL_EPISODES + TARIFF_EPISODES}
    assert {d for _, d in EPISODES} <= known


def test_a_question_the_corpus_cannot_measure_is_refused():
    from nlq import parse
    assert parse("Will the stock market go up next year?")["refused"]
    assert "refused" not in parse("What if China threatens Russia?")


# --- who else felt it: beside the answer, never in it -------------------------
# Fifteen more series in the verdict would make "detectable" easier by luck and
# move published answers. They are context, like the rates panel.

def _expo_prices():
    days = [f"2019-0{m}-{d:02d}" for m in (1, 2) for d in range(1, 29)]
    return ([{"symbol": "^N225", "assetClass": "exposure", "name": "Nikkei 225",
              "group": "Asia", "date": d, "close": 100 + k} for k, d in enumerate(days)]
            + [{"symbol": "XLE", "assetClass": "sector_etf", "date": d, "close": 50 + k}
               for k, d in enumerate(days)]
            + [{"symbol": "^GSPC", "assetClass": "index", "date": d, "close": 3000 + k}
               for k, d in enumerate(days)])


def test_exposure_series_never_enter_a_baseline_or_the_event_study():
    from build_inferred_edges import study_targets, baseline_docs, price_series_docs
    prices = _expo_prices()
    assert "^N225" not in {s for s, _, _ in study_targets(prices)}
    assert not any(b["symbol"] == "^N225" for b in baseline_docs(prices))
    ps = {d["symbol"]: d for d in price_series_docs(prices)}
    assert ps["^N225"]["group"] == "Asia" and ps["^N225"]["name"] == "Nikkei 225"


def test_exposure_series_are_not_made_into_company_nodes():
    from build_graph import transform
    built = transform([], [], _expo_prices())
    assert not any("N225" in k for k in built["nodes"]["Company"])
    assert not any("N225" in k for k in built["nodes"]["Index"])


def test_every_exposure_series_says_why_it_is_there():
    from market_data_pull import EXPOSURE, UNIVERSE
    assert set(UNIVERSE["exposure"]) == set(EXPOSURE)
    assert all(name and group for name, group in EXPOSURE.values())


# --- the exposure graph ------------------------------------------------------

def test_every_published_figure_in_the_exposure_graph_has_a_source():
    from build_exposure_graph import curated
    built = curated()
    for coll in ("flowsThrough", "dependsOn", "produces", "suppliedBy"):
        for e in built[coll]:
            assert e["source"] and e["sourceUrl"].startswith("http") and e["asOf"], e["_key"]
            for k in ("share", "lo", "hi"):
                assert e.get(k) is None or 0 <= e[k] <= 1, (e["_key"], k)
            if e.get("lo") is not None:
                assert e["lo"] <= e["hi"]


def test_the_gulf_box_is_the_one_registered_before_the_tests_ran():
    """docs/exposure-hypotheses.md fixed it at 22.0-30.5N, 47.5-60.0E before any
    result existed. Tuning it afterwards would make the tests meaningless."""
    from exposure_reference import REGIONS
    assert REGIONS["hormuz"]["lat"] == (22.0, 30.5)
    assert REGIONS["hormuz"]["lng"] == (47.5, 60.0)


def test_import_shares_say_which_route_they_describe():
    """Japan's 95% is Middle East crude through Hormuz. Without `via` it was listed
    under the Red Sea too."""
    from build_exposure_graph import curated
    built = curated()
    assert all(e.get("via") for e in built["dependsOn"] + built["produces"])


def test_a_session_already_measured_is_not_measured_again():
    """A Sunday and the Monday after resolve to the same session; counting both
    doubled Abqaiq, 2016-01-03/04 and 2026-02-28/03-02 in the first run."""
    from exposure_hypotheses import next_return
    ps = {"closes": [100.0, 110.0], "prev": {"2019-09-15": 0, "2019-09-16": 0}}
    used: set = set()
    assert next_return(ps, "2019-09-15", used) == pytest.approx(0.10)
    assert next_return(ps, "2019-09-16", used) is None


# --- the exposure graph is optional -------------------------------------------
# The starter kit predated the exposure graph, and /api/event queried it
# unconditionally, so every single-day question on a starter build was a 500.

def test_a_missing_exposure_graph_means_no_paths_not_a_failed_day():
    sys.path.insert(0, str(REPO / "app"))
    from paths import day_paths

    class NotFound(Exception):
        error_code = 1203

    def run(aql, **binds):
        raise NotFound("collection or view not found: dependsOn")
    assert day_paths(run, "2019-09-14") == []

    class Other(Exception):
        error_code = 1501

    def broken(aql, **binds):
        raise Other("syntax error")
    with pytest.raises(Other):
        day_paths(broken, "2019-09-14")
