"""What a question means for a portfolio: the cross-graph join.

The holdings are graph data — Portfolio -holds-> Holding -holds-> Company|Index —
and have been since the first build, but nothing showed them. This reads them and
measures each holding over the same window the rest of the app measures, from the
same `PriceSeries` and the same anchor: the last close STRICTLY BEFORE the day,
so the day's own reaction is inside the move.

Two shapes, matching the app's two modes:

  one day      each holding's move and its weighted contribution, beside the S&P.
               A measurement, not an estimate.
  past analogs the portfolio's move after each analog, and the range. This is
               where the danger is: a weighted average of noise still prints as a
               number. The verdict above it decides whether the number means
               anything, and the UI says so beside it.

Pure functions take plain dicts so they test without a database; the loaders at
the bottom are the only part that touches one.
"""
from __future__ import annotations

import re
import statistics
import time

BENCHMARK = "_GSPC"
_KEY_BAD = re.compile(r"[^A-Za-z0-9_:.@()+,=;$!*'%-]")


def series_key(ticker: str) -> str:
    """PriceSeries _key for a ticker — `^GSPC` is stored as `_GSPC`."""
    return _KEY_BAD.sub("_", ticker)


def move(ps: dict | None, date: str, horizon: int) -> float | None:
    """Return over `horizon` sessions from the last close strictly before `date`."""
    if not ps:
        return None
    i = ps["prev"].get(date)
    if i is None or i + horizon >= len(ps["closes"]):
        return None
    a, b = ps["closes"][i], ps["closes"][i + horizon]
    return b / a - 1 if a > 0 and b > 0 else None


def day_impact(holdings: list[dict], series: dict, date: str, horizon: int) -> dict:
    """Each holding's move on one day, weighted, beside the benchmark."""
    rows, total, covered = [], 0.0, 0.0
    for h in holdings:
        mv = move(series.get(series_key(h["ticker"])), date, horizon)
        rows.append({**h, "movePct": None if mv is None else round(mv * 100, 2),
                     "contributionPct": None if mv is None
                     else round(mv * h["weight"] * 100, 2)})
        if mv is not None:
            total += mv * h["weight"]
            covered += h["weight"]
    bench = move(series.get(BENCHMARK), date, horizon)
    rows.sort(key=lambda r: -abs(r["contributionPct"] or 0))
    return {"date": date, "horizon": horizon, "rows": rows,
            "covered": round(covered, 4),
            "totalPct": round(total * 100, 2) if covered else None,
            "benchmarkPct": None if bench is None else round(bench * 100, 2)}


def analog_impact(holdings: list[dict], series: dict, dates: list[str],
                  horizon: int) -> dict:
    """The portfolio's move after each analog date, and the spread of them."""
    per = []
    for d in sorted(set(dates)):
        day = day_impact(holdings, series, d, horizon)
        # A partial portfolio is a different portfolio: skip the date rather than
        # report a total that silently leaves holdings out.
        if day["totalPct"] is None or day["covered"] < 0.999:
            continue
        per.append({"date": d, "portfolioPct": day["totalPct"],
                    "benchmarkPct": day["benchmarkPct"]})
    vals = [p["portfolioPct"] for p in per]
    if not vals:
        return {"horizon": horizon, "n": 0, "analogs": []}
    excess = [p["portfolioPct"] - p["benchmarkPct"] for p in per
              if p["benchmarkPct"] is not None]
    return {"horizon": horizon, "n": len(vals), "analogs": per,
            "meanPct": round(statistics.mean(vals), 2),
            "worstPct": min(vals), "bestPct": max(vals),
            "meanExcessPct": round(statistics.mean(excess), 2) if excess else None}


# --- loaders -----------------------------------------------------------------

_cache: dict = {"at": 0.0, "series": None}
CACHE_SECONDS = 3600     # prices change only when the corpus is refreshed


def load_holdings(run) -> dict:
    rows = run("""
      FOR p IN Portfolio
        FOR h IN 1 OUTBOUND p holds
          FOR x IN 1 OUTBOUND h holds
            SORT h.weight DESC
            RETURN {portfolio: p.name, ticker: h.ticker, weight: h.weight,
                    sector: x.sector,
                    kind: PARSE_IDENTIFIER(x._id).collection == "Index" ? "ETF" : "stock"}""")
    name = rows[0]["portfolio"] if rows else None
    return {"name": name,
            "holdings": [{k: v for k, v in r.items() if k != "portfolio"} for r in rows]}


def load_series(run, holdings: list[dict]) -> dict:
    now = time.time()
    if _cache["series"] is None or now - _cache["at"] > CACHE_SECONDS:
        keys = sorted({series_key(h["ticker"]) for h in holdings} | {BENCHMARK})
        docs = run("FOR k IN @keys LET p = DOCUMENT('PriceSeries', k) FILTER p != null "
                   "RETURN KEEP(p, '_key', 'closes', 'prev')", keys=keys)
        _cache.update(at=now, series={d["_key"]: d for d in docs})
    return _cache["series"]
