"""Rates, inflation expectations and the dollar on the day: the transmission row.

An oil shock reaches equities partly through what it does to expected inflation
and rates, so a single-day answer is more complete with those beside it. These are
FRED daily series (scripts/market_data_pull.py) carried in `PriceSeries` with a
`unit`, and the unit decides what a day's move IS:

  percent   a yield or a breakeven — the move is a change in level, in basis
            points. 0.05% -> 0.08% is +3bp; as a percentage of the rate it would
            read +60%, which is how the first macro pull got it wrong.
  index     the trade-weighted dollar — an ordinary percentage return.

"vs normal" is the same idea as everywhere else in the app: the move against the
spread of that series' own h-day moves over the selected window. Context only —
these series never enter a verdict, a baseline or the sweep.
"""
from __future__ import annotations

import statistics
import time

_cache: dict = {}
CACHE_SECONDS = 3600


def change(ps: dict, i: int, horizon: int) -> float | None:
    """A move over `horizon` sessions from index i, in the series' own unit."""
    if i is None or i + horizon >= len(ps["closes"]):
        return None
    a, b = ps["closes"][i], ps["closes"][i + horizon]
    if ps.get("unit") == "percent":
        return (b - a) * 100                       # basis points
    return (b / a - 1) * 100 if a > 0 else None    # percent


def day_rows(series: list[dict], date: str, horizon: int,
             lo: str | None, hi: str | None) -> list[dict]:
    rows = []
    for ps in series:
        i = ps["prev"].get(date)
        mv = change(ps, i, horizon)
        if mv is None:
            continue
        dates = ps["dates"]
        moves = [m for k, d in enumerate(dates)
                 if (lo is None or d >= lo) and (hi is None or d <= hi)
                 and (m := change(ps, k, horizon)) is not None]
        sd = statistics.pstdev(moves) if len(moves) > 1 else 0
        z = (mv - statistics.fmean(moves)) / sd if sd else None
        rows.append({"symbol": ps["_key"], "name": ps.get("name") or ps["_key"],
                     "group": ps.get("group"),
                     "unit": "bp" if ps.get("unit") == "percent" else "%",
                     "move": round(mv, 2), "z": None if z is None else round(z, 2)})
    rows.sort(key=lambda r: -abs(r["z"] or 0))
    return rows


def load(run, asset_class: str = "macro") -> list[dict]:
    """PriceSeries of one class. `exposure` (the who-else-felt-it markets) is read
    the same way and measured the same way: context beside an answer."""
    now = time.time()
    hit = _cache.setdefault(asset_class, {"at": 0.0, "docs": None})
    if hit["docs"] is None or now - hit["at"] > CACHE_SECONDS:
        hit.update(at=now, docs=run("""
          FOR p IN PriceSeries FILTER p.assetClass == @cls
            LET m = DOCUMENT("MacroVariable", p._key)
            RETURN MERGE(KEEP(p, "_key", "unit", "dates", "closes", "prev", "group"),
                         {name: NOT_NULL(m.name, p.name)})""", cls=asset_class))
    return hit["docs"]
