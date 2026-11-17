#!/usr/bin/env python3
"""Run the four exposure hypotheses exactly as stated in docs/exposure-hypotheses.md.

That document was committed (dd547ef) before this script existed or any of these
numbers had been computed. This script implements it and nothing more: if a
choice below is not in that document, it is a bug, not a parameter.

    python scripts/exposure_hypotheses.py            # run, print, write JSON
    python scripts/exposure_hypotheses.py --days     # also list the 40 days
    python scripts/exposure_hypotheses.py --h5       # H5, docs/exposure-hypotheses-h5.md

H5 was committed (ee40d07) before its code existed, under the same rule.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys

sys.path.insert(0, "scripts")
from _common import db                                   # noqa: E402
from multiple_testing import t_two_sided_p               # noqa: E402

N_DAYS = 40
ALPHA = 0.05 / 4
SPAN = ("2013-04-01", "2026-10-31")
OUT = pathlib.Path("docs/exposure-hypotheses.json")

# H5: named in docs/exposure-hypotheses-h5.md. City and province names (Jazan,
# Yanbu, Jubail, Qatif) and the route (Strait of Hormuz) are excluded there.
H5_FACILITIES = ["Abqaiq", "Khurais", "Ras Tanura", "Shaybah", "Safaniya", "Khafji",
                 "Kharg Island", "Haradh"]
H5_OPERATORS = ["Saudi Aramco", "ADNOC", "Kuwait Petroleum", "SABIC", "Petro Rabigh", "NIOC"]
H5_ALPHA = 0.05 / 3
H5_OUT = pathlib.Path("docs/exposure-hypotheses-h5.json")

FACILITY_DAYS_AQL = """
FOR ev IN Event
  FILTER TO_NUMBER(ev.quadClass) == 4
  FILTER ev.dateAdded >= @lo AND ev.dateAdded <= @hi
  FILTER LENGTH(INTERSECTION(NOT_NULL(ev.targetFacilities, []), @fac)) > 0
      OR LENGTH(INTERSECTION(NOT_NULL(ev.targetOperators, []), @ops)) > 0
  COLLECT day = ev.dateAdded AGGREGATE coverage = SUM(ev.numMentions), events = COUNT(1)
  RETURN { day, coverage, events }
"""

# Material-conflict events located in the pre-registered Gulf box, summed by day.
GULF_DAYS_AQL = """
LET locs = (FOR e IN inRegion FILTER e._to == "Chokepoint/hormuz" RETURN e._from)
FOR loc IN locs
  FOR ev IN 1..1 INBOUND loc locatedIn
    FILTER TO_NUMBER(ev.quadClass) == 4
    FILTER ev.dateAdded >= @lo AND ev.dateAdded <= @hi
    COLLECT day = ev.dateAdded AGGREGATE coverage = SUM(ev.numMentions), events = COUNT(1)
    RETURN { day, coverage, events }
"""


def gulf_days(database) -> list[dict]:
    """Steps 3-5 of the event-set definition: daily coverage, best day per 7-day
    block, top 40 blocks."""
    rows = list(database.aql.execute(GULF_DAYS_AQL, bind_vars={"lo": SPAN[0], "hi": SPAN[1]},
                                     ttl=3600))
    return top_blocks(rows)


def facility_days(database) -> list[dict]:
    """H5 event set: same blocks and top 40, defined by named facility or operator."""
    rows = list(database.aql.execute(
        FACILITY_DAYS_AQL, ttl=3600,
        bind_vars={"lo": SPAN[0], "hi": SPAN[1], "fac": H5_FACILITIES, "ops": H5_OPERATORS}))
    return top_blocks(rows)


def top_blocks(rows: list[dict]) -> list[dict]:
    import datetime as dt
    base = dt.date(2018, 1, 1)
    best: dict[int, dict] = {}
    for r in rows:
        blk = math.floor((dt.date.fromisoformat(r["day"]) - base).days / 7)
        if blk not in best or r["coverage"] > best[blk]["coverage"]:
            best[blk] = r
    return sorted(best.values(), key=lambda r: -r["coverage"])[:N_DAYS]


def next_return(ps: dict, day: str, used: set | None = None) -> float | None:
    """Last close strictly before `day` to the next close.

    `used` holds anchors already measured. The pre-registered intent is that "no
    two test days share a window", and fixed 7-day blocks did not deliver it: a
    Sunday and the Monday after fall in different blocks and resolve to the same
    session (2019-09-15/16, 2026-02-28/03-02). Counting that session twice inflates
    n. The class sweep already guards against this the same way.
    """
    i = ps["prev"].get(day)
    if i is None or i + 1 >= len(ps["closes"]):
        return None
    if used is not None:
        if i in used:
            return None
        used.add(i)
    a, b = ps["closes"][i], ps["closes"][i + 1]
    return b / a - 1 if a > 0 and b > 0 else None


def mean_daily(ps: dict) -> float:
    d, c = ps["dates"], ps["closes"]
    rets = [c[k] / c[k - 1] - 1 for k in range(1, len(c))
            if SPAN[0] <= d[k] <= SPAN[1] and c[k - 1] > 0 and c[k] > 0]
    return statistics.fmean(rets)


def one_sided(xs: list[float], direction: int) -> dict:
    """Exact Student-t, one-sided in the stated direction (-1: mean < 0)."""
    n = len(xs)
    m, sd = statistics.fmean(xs), statistics.stdev(xs)
    t = m / (sd / math.sqrt(n))
    p_two = t_two_sided_p(t, n - 1)
    p = p_two / 2 if (t * direction) > 0 else 1 - p_two / 2
    return {"n": n, "meanPct": round(m * 100, 3), "t": round(t, 2), "p": float(f"{p:.3g}"),
            "passes": p < ALPHA}


def without_strongest(xs: list[float], direction: int) -> dict:
    """The pre-stated check for H5: drop the one day that helps the claim most."""
    drop = max(range(len(xs)), key=lambda k: xs[k] * direction)
    return {"dropped": xs[drop], **one_sided(xs[:drop] + xs[drop + 1:], direction)}


def run_h5(database, show_days: bool) -> int:
    days = facility_days(database)
    keys = ["BZ=F", "_N225", "_KS11", "_GSPC"]
    ps = {d["_key"]: d for d in database.aql.execute(
        "FOR k IN @k RETURN DOCUMENT('PriceSeries', k)", bind_vars={"k": keys})}

    def paired(a: str, b: str) -> tuple[list[float], list[str]]:
        # Days arrive highest coverage first, so on a shared session the
        # higher-coverage day is the one kept; a session of EITHER series counts once.
        out, when, ua, ub = [], [], set(), set()
        for d in days:
            ia, ib = ps[a]["prev"].get(d["day"]), ps[b]["prev"].get(d["day"])
            if ia in ua or ib in ub:
                continue
            ra, rb = next_return(ps[a], d["day"]), next_return(ps[b], d["day"])
            if ra is not None and rb is not None:
                ua.add(ia); ub.add(ib)
                out.append(ra - rb); when.append(d["day"])
        return out, when

    def excess(a: str) -> tuple[list[float], list[str]]:
        mu, used, out, when = mean_daily(ps[a]), set(), [], []
        for d in days:
            if (r := next_return(ps[a], d["day"], used)) is not None:
                out.append(r - mu); when.append(d["day"])
        return out, when

    tests = {
        "H5.1": ("Control: Brent rises", excess("BZ=F"), +1),
        "H5.2": ("Japan more exposed: Nikkei minus S&P", paired("_N225", "_GSPC"), -1),
        "H5.3": ("Korea more exposed: KOSPI minus S&P (data-suggested)",
                 paired("_KS11", "_GSPC"), -1),
    }
    earlier = set()
    if OUT.exists():
        earlier = {d["day"] for d in json.loads(OUT.read_text())["days"]}
    overlap = sorted(d["day"] for d in days if d["day"] in earlier)

    print(f"\n  H5: {len(days)} facility- or operator-named conflict days, coverage "
          f"{days[-1]['coverage']:,} .. {days[0]['coverage']:,}")
    print(f"  {len(overlap)} of them also in the H1-H4 set")
    print(f"  one-sided exact t, Bonferroni alpha = {H5_ALPHA:.4f}\n")
    print(f"  {'':6}{'claim':<52}{'n':>4}{'mean':>9}{'t':>7}{'p':>10}  result")
    results = {}
    for h, (claim, (xs, when), direction) in tests.items():
        r = one_sided(xs, direction)
        r["passes"] = r["p"] < H5_ALPHA
        loo = without_strongest(xs, direction)
        loo["passes"] = loo["p"] < H5_ALPHA
        loo["droppedDay"] = when[max(range(len(xs)), key=lambda k: xs[k] * direction)]
        results[h] = {"claim": claim, **r, "withoutStrongestDay": loo}
        print(f"  {h:<6}{claim:<52}{r['n']:>4}{r['meanPct']:>+8.2f}%{r['t']:>+7.2f}"
              f"{r['p']:>10.3g}  {'PASS' if r['passes'] else 'fail'}")
        label = f"  without {loo['droppedDay']} ({loo['dropped'] * 100:+.2f}%)"
        print(f"  {'':6}{label:<52}"
              f"{loo['n']:>4}{loo['meanPct']:>+8.2f}%{loo['t']:>+7.2f}{loo['p']:>10.3g}  "
              f"{'PASS' if loo['passes'] else 'fail'}")
    if show_days:
        print("\n  the days:")
        for d in sorted(days, key=lambda d: d["day"]):
            print(f"    {d['day']}  coverage {d['coverage']:>7,}  events {d['events']:>5,}"
                  f"{'  (also H1-H4)' if d['day'] in earlier else ''}")
    H5_OUT.write_text(json.dumps({"preregistered": "docs/exposure-hypotheses-h5.md (ee40d07)",
                                  "alpha": H5_ALPHA, "overlapWithH1to4": overlap,
                                  "days": days, "results": results}, indent=1) + "\n")
    print(f"\n  written: {H5_OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", action="store_true")
    ap.add_argument("--h5", action="store_true", help="run H5 instead of H1-H4")
    args = ap.parse_args()

    database = db()
    if args.h5:
        return run_h5(database, args.days)
    days = gulf_days(database)
    keys = ["_N225", "_GSPC", "BZ=F", "2330.TW", "_TWII", "FRO"]
    ps = {d["_key"]: d for d in database.aql.execute(
        "FOR k IN @k RETURN DOCUMENT('PriceSeries', k)", bind_vars={"k": keys})}

    def paired(a: str, b: str) -> list[float]:
        out, used = [], set()
        for d in days:
            if ps[a]["prev"].get(d["day"]) in used:
                continue
            ra, rb = next_return(ps[a], d["day"], used), next_return(ps[b], d["day"])
            if ra is not None and rb is not None:
                out.append(ra - rb)
        return out

    def excess(a: str) -> list[float]:
        mu, used = mean_daily(ps[a]), set()
        return [r - mu for d in days if (r := next_return(ps[a], d["day"], used)) is not None]

    tests = {
        "H1": ("Japan more exposed than the US: Nikkei minus S&P", paired("_N225", "_GSPC"), -1),
        "H2": ("Positive control: Brent rises", excess("BZ=F"), +1),
        "H3": ("Helium channel: TSMC minus Taiwan TAIEX", paired("2330.TW", "_TWII"), -1),
        "H4": ("Tankers gain: Frontline", excess("FRO"), +1),
    }
    results = {}
    print(f"\n  {len(days)} Gulf shock days, coverage {days[-1]['coverage']:,} .. "
          f"{days[0]['coverage']:,}; a session already measured is not measured again")
    print(f"  one-sided exact t, Bonferroni alpha = {ALPHA}\n")
    print(f"  {'':4}{'claim':<46}{'n':>4}{'mean':>9}{'t':>7}{'p':>10}  result")
    for h, (claim, xs, direction) in tests.items():
        r = one_sided(xs, direction)
        results[h] = {"claim": claim, **r}
        print(f"  {h:<4}{claim:<46}{r['n']:>4}{r['meanPct']:>+8.2f}%{r['t']:>+7.2f}"
              f"{r['p']:>10.3g}  {'PASS' if r['passes'] else 'fail'}")
    if args.days:
        print("\n  the days:")
        for d in sorted(days, key=lambda d: d["day"]):
            print(f"    {d['day']}  coverage {d['coverage']:>7,}  events {d['events']:>5,}")
    OUT.write_text(json.dumps({"preregistered": "docs/exposure-hypotheses.md (dd547ef)",
                               "alpha": ALPHA, "days": days, "results": results},
                              indent=1) + "\n")
    print(f"\n  written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
