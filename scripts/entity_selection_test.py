#!/usr/bin/env python3
"""Is the named-entity class an effect, or selection on coverage?

Filtering events by entities read out of their news coverage is a retrieval
facet (docs/entity-extraction.md). Using it to define a class that is then
MEASURED is a different act, and a suspect one: articles name the operator
partly because the price moved, so the filter may select the outcome.

The confounder is coverage volume. This matches named-entity days against
unnamed days of comparable coverage in the same event class, and measures both
against WTI. If the effect is selection, the matched control shows it too.

Read-only. Prints a table; writes nothing.
"""
from __future__ import annotations

import bisect
import math
import statistics
import sys

sys.path.insert(0, "scripts")
from _common import db                                       # noqa: E402

HORIZONS = (1, 3, 5, 10)
SYMBOL = "CL=F"

# Middle East attack classes — where the named entities actually live.
CLASS_FILTER = """
  FILTER e.actorToCode IN ["SAU", "ISR", "IRQ"]
  FILTER e.cameoRoot IN ["18", "19", "20"]
"""


def forward_return(series, date, horizon):
    """Last close STRICTLY BEFORE `date` to `horizon` sessions later."""
    dates, closes = series
    i = bisect.bisect_left(dates, date) - 1
    if i < 0 or i + horizon >= len(closes):
        return None
    base, end = closes[i], closes[i + horizon]
    if base <= 0 or end <= 0:
        return None
    return end / base - 1.0


def tstat(xs):
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return None if sd == 0 else statistics.mean(xs) / (sd / math.sqrt(len(xs)))


def day_sets(d):
    """Per-day: coverage volume, and whether any event that day names an entity."""
    rows = list(d.aql.execute(f"""
      WITH Event
      FOR e IN Event
        {CLASS_FILTER}
        COLLECT day = e.dateAdded INTO g
        LET mentions = SUM(g[*].e.numMentions)
        LET named = LENGTH(
          FOR x IN g FILTER LENGTH(x.e.perpetrators) > 0
             OR LENGTH(x.e.targetFacilities) > 0
             OR LENGTH(x.e.targetOperators) > 0
          RETURN 1)
        RETURN {{day, mentions, named}}"""))
    treat = [r for r in rows if r["named"] > 0]
    ctrl = [r for r in rows if r["named"] == 0]
    return treat, ctrl


def match_on_volume(treat, ctrl, by_year=True):
    """One unnamed day per named day, nearest on coverage volume, no reuse.

    Matched WITHIN YEAR by default. Volume alone is not enough: the first run of
    this test drew 146 control days from 2020 against the named set's 100, and
    2020 is the year WTI averaged $40 against 2018's $65. A calendar tilt that
    size manufactures a 10-day drift on its own, and duly did — the unmatched
    control printed t = 3.16 at 10d, which was the year mix, not the events.
    """
    out = []
    years = sorted({r["day"][:4] for r in treat}) if by_year else [None]
    for y in years:
        t_y = [r for r in treat if y is None or r["day"].startswith(y)]
        pool = sorted((r for r in ctrl if y is None or r["day"].startswith(y)),
                      key=lambda r: r["mentions"])
        vols = [r["mentions"] for r in pool]
        used = set()
        for t in sorted(t_y, key=lambda r: -r["mentions"]):
            i = bisect.bisect_left(vols, t["mentions"])
            best, bestd = None, None
            for j in range(max(0, i - 60), min(len(pool), i + 60)):
                if j in used:
                    continue
                dist = abs(pool[j]["mentions"] - t["mentions"])
                if bestd is None or dist < bestd:
                    best, bestd = j, dist
            if best is not None:
                used.add(best)
                out.append(pool[best])
    return out


def main() -> int:
    d = db()
    ps = d.collection("PriceSeries").get(SYMBOL)
    series = (ps["dates"], ps["closes"])

    treat, ctrl = day_sets(d)
    matched = match_on_volume(treat, ctrl)

    print(f"  class: attacks on SAU/ISR/IRQ, CAMEO roots 18/19/20")
    print(f"  named-entity days      {len(treat):>5}   "
          f"median coverage {statistics.median([r['mentions'] for r in treat]):>8,.0f}")
    print(f"  unnamed days (all)     {len(ctrl):>5}   "
          f"median coverage {statistics.median([r['mentions'] for r in ctrl]):>8,.0f}")
    print(f"  unnamed, volume-matched{len(matched):>5}   "
          f"median coverage {statistics.median([r['mentions'] for r in matched]):>8,.0f}")
    print()
    import collections
    for label, rows in (("named  ", treat), ("matched", matched)):
        c = collections.Counter(r["day"][:4] for r in rows)
        print(f"  {label} by year: " + "  ".join(f"{y}:{c[y]}" for y in sorted(c)))
    print()
    print(f"  WTI ({SYMBOL}) forward return from the close before the day\n")
    print(f"  {'horizon':<9}{'named mean':>12}{'t':>8}   "
          f"{'matched mean':>13}{'t':>8}   {'difference':>11}")
    print("  " + "-" * 66)

    for h in HORIZONS:
        a = [r for r in (forward_return(series, x["day"], h) for x in treat) if r is not None]
        b = [r for r in (forward_return(series, x["day"], h) for x in matched) if r is not None]
        ta, tb = tstat(a), tstat(b)
        diff = statistics.mean(a) - statistics.mean(b)
        print(f"  {h:>2}d      {statistics.mean(a)*100:>10.2f}%"
              f"{ta if ta is None else round(ta,2):>8}   "
              f"{statistics.mean(b)*100:>11.2f}%{tb if tb is None else round(tb,2):>8}   "
              f"{diff*100:>10.2f}%")
    print()
    print("  If the matched control moves like the named set, the entity filter is")
    print("  selecting coverage, not identifying a kind of event.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
