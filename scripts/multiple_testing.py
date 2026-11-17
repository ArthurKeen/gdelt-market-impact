#!/usr/bin/env python3
"""Sweep every class question the corpus can express, then correct for the search.

The project's headline null used to be stated as a rate: "103,776 tests, 1.99%
cleared |t| >= 2.5, which is the chance rate". True, but it leaves the obvious
reply open — *fine, the average looks like noise, but what about the strongest
one?* This answers that directly by testing whether the single best result in the
whole sweep survives a correction for how wide the search was. It does not, by a
wide margin.

Two things here are easy to get wrong and are done explicitly.

**The normal approximation fails at these sample sizes.** The best cell has n=13,
and Student-t with 12 degrees of freedom has far fatter tails than a normal. Under
the normal shortcut that cell APPEARS to survive; against the exact t tail it misses
by roughly 75x. `t_two_sided_p` integrates the density rather than approximating it,
because the approximation is wrong precisely where the answer is decided.

**Bonferroni assumes independent tests and these are not.** The same sectors recur,
horizons overlap, and 5-day windows share trading days, so the effective number of
independent tests is smaller than the nominal count and the true bar is softer than
the one applied. That makes this conservative. The margin is what carries the
claim: at 75x, an effective count an order of magnitude smaller would not rescue
the result. Reported alongside so a reader can judge that rather than take it.

    python scripts/multiple_testing.py            # full sweep, ~2 minutes
    python scripts/multiple_testing.py --quick    # skip the sweep, restate stored figures
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _common import db  # noqa: E402

MIN_BLOCKS = 12          # cells with fewer independent observations are not tested
MIN_OBS = 8              # ...and a cell needs this many usable price windows
HORIZONS = (3, 5, 10)
TOPK = 40                # analogs per cell, best-supported first
ALPHA = 0.05

ACTORS = ["USA", "CHN", "IRN", "SAU", "RUS", "ISR", "YEM", "LBN", "SYR", "IRQ", "PSE"]

CELLS_AQL = """
WITH Event
FOR e IN Event
  FILTER e.actorFromCode IN @set AND e.actorToCode IN @set
  FILTER e.actorFromCode != e.actorToCode
  FILTER @until == null OR e.dateAdded <= @until
  COLLECT a = e.actorFromCode, b = e.actorToCode, c = e.cameo,
          blk = FLOOR(DATE_DIFF("2018-01-01", e.dateAdded, "d") / 7)
        INTO g
  LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC LIMIT 1 RETURN x)
  RETURN { a, b, c, blk, dt: best.dateAdded, m: best.numMentions }
"""


def t_two_sided_p(t: float, df: int, steps: int = 200000) -> float:
    """Exact-enough two-sided Student-t tail, by Simpson integration of the density.

    scipy is not a dependency of this repo and the normal approximation is exactly
    what fails here — see the module docstring.
    """
    t = abs(t)
    hi = 60.0
    if t >= hi or df < 1:
        return 0.0
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    h = (hi - t) / steps
    total = 0.0
    for i in range(steps + 1):
        x = t + i * h
        w = 1 if i in (0, steps) else (4 if i % 2 else 2)
        total += w * (1 + x * x / df) ** (-(df + 1) / 2)
    return min(1.0, 2 * c * total * h / 3)


def critical_t(df: int, alpha: float) -> float:
    lo, hi = 1.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df, steps=20000) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sweep(database, until: str | None = None):
    """Every (dyad, CAMEO, horizon, target) cell with enough evidence to test.

    `until` replays the sweep as it stood when the corpus ended on that day, so a
    change in the strongest cell can be traced to the data that moved it.
    """
    cells = list(database.aql.execute(CELLS_AQL, bind_vars={"set": ACTORS, "until": until},
                                      batch_size=100000, stream=True,
                                      ttl=3600))
    groups = collections.defaultdict(list)
    for c in cells:
        groups[(c["a"], c["b"], c["c"])].append((c["m"], c["dt"]))

    ps = {p["_key"]: p for p in database.collection("PriceSeries").all()}
    base = {b["_key"]: b for b in database.collection("BaselineStats").all()}
    targets = {}
    for s in database.collection("Sector").all():
        targets[s["proxySymbol"]] = s["name"]
    for c in database.collection("Commodity").all():
        targets[c["symbol"]] = c["name"]
    bm = ps["_GSPC"]

    def study(dates, sym, h):
        b, p = base.get(f"{sym}_{h}"), ps.get(sym)
        if not b or not p:
            return None
        obs, used = [], set()
        for d in dates:
            i = p["prev"].get(d)
            if i is None or i + h >= len(p["closes"]) or i in used:
                continue
            if until and p["dates"][i + h] > until:
                continue                     # a price the replayed corpus lacked
            if p["closes"][i] <= 0 or p["closes"][i + h] <= 0:
                continue
            used.add(i)
            raw = p["closes"][i + h] / p["closes"][i] - 1
            bench = 0.0
            if b.get("useAbnormal"):
                j = bm["pos"].get(p["dates"][i])
                if j is not None and j + h < len(bm["closes"]) and bm["closes"][j] > 0:
                    bench = bm["closes"][j + h] / bm["closes"][j] - 1
            obs.append(raw - bench)
        n = len(obs)
        if n < MIN_OBS:
            return None
        sd = statistics.stdev(obs)
        if sd == 0:
            return None
        excess = statistics.mean(obs) - b["mean"]
        return excess, excess / (sd / math.sqrt(n)), n, excess / sd

    results, tested = [], 0
    for (a, b, cam), evs in groups.items():
        if len(evs) < MIN_BLOCKS:
            continue
        evs.sort(reverse=True)
        dates = [d for _, d in evs[:TOPK]]
        for h in HORIZONS:
            for sym, name in targets.items():
                r = study(dates, sym, h)
                if not r:
                    continue
                tested += 1
                excess, t, n, sharpe = r
                results.append({"dyad": f"{a}->{b}", "cameo": cam, "horizon": h,
                                "target": name, "move": excess, "t": t, "n": n,
                                "sharpe": sharpe})
    return results, tested


# Below this |t| no cell can be the strongest: a Student-t tail is never thinner
# than the normal one, so p(|t| < 4) > 6.3e-5 at any n, and rank_by_p checks
# that something above the floor beats it.
P_FLOOR_T = 4.0


def rank_by_p(results, floor: float = P_FLOOR_T):
    """Order the sweep by exact p, strongest evidence first.

    Ranking by |t| was the old rule, and it is wrong when n varies: on the
    2026-11 corpus it put IRN->IRQ (t -6.81, n=14, p 1.2e-5) first, while
    PSE->ISR (t -6.16, n=37, p 4.2e-7) is thirty times stronger evidence. The
    published margin swung from 2.5x to 101x on that alone. Only cells with
    |t| >= floor get an exact p; nothing below it can beat one that does.
    """
    cands = [dict(r, p=t_two_sided_p(r["t"], r["n"] - 1))
             for r in results if abs(r["t"]) >= floor]
    cands.sort(key=lambda r: r["p"])
    if not cands or cands[0]["p"] > math.erfc(floor / math.sqrt(2)):
        raise SystemExit(f"no cell clears |t| >= {floor} by enough to rank the "
                         f"strongest exactly — lower P_FLOOR_T")
    return cands


def report(results, tested):
    alpha_adj = ALPHA / tested
    loose = sum(1 for r in results if abs(r["t"]) >= 2.5)
    ranked = rank_by_p(results)

    print(f"\n  tests run                    {tested:,}")
    print(f"  cleared |t| >= 2.5           {loose:,}  ({loose / tested:.2%})"
          f"   [chance is roughly 1.5-2.5%]")
    print(f"\n  Correcting for a search this wide (Bonferroni, alpha={ALPHA}):")
    print(f"    a result must beat p = {alpha_adj:.2e}   (1 in {1 / alpha_adj:,.0f})\n")
    print(f"  {'strongest cells':<34}{'t':>7}{'n':>5}{'Sharpe':>9}{'p':>12}   survives?")
    # Every cell that could survive is in `ranked`, so this counts them all —
    # not just the six printed.
    survivors = sum(1 for r in ranked if r["p"] < alpha_adj)
    for r in ranked[:6]:
        label = f"{r['dyad']} {r['cameo']} {r['horizon']}d {r['target'][:12]}"
        print(f"  {label:<34}{r['t']:>+7.2f}{r['n']:>5}{r['sharpe']:>9.3f}"
              f"{r['p']:>12.2e}   {'YES' if r['p'] < alpha_adj else 'no'}")

    best = ranked[0]
    p_best = best["p"]
    # One decimal: at 139x the rounding did not matter, at 2.5x ":.0f" reports
    # "2" and understates the margin in the direction that flatters the claim.
    print(f"\n  Best result in the entire sweep misses the bar by "
          f"{p_best / alpha_adj:,.1f}x.")
    print(f"  A cell with n={best['n']} would need |t| >= "
          f"{critical_t(best['n'] - 1, alpha_adj):.2f} to survive.")
    print(f"\n  RESULT: {survivors} of {tested:,} class results survive correction.")
    print("  Bonferroni assumes independence; these tests overlap heavily, so the true")
    print("  bar is softer than this and the margin above is what carries the claim.")
    return survivors, tested, p_best / alpha_adj, ranked


def store_result(database, results, tested, survivors, miss_multiple, best) -> None:
    """Record the sweep so the running service can state it without repeating it.

    Every stale figure in this project came from writing a number into prose and
    then changing the data underneath it — the corpus header, the window label,
    and 243,903 across sixteen files. The service reads this document instead, so
    it cannot drift from the sweep that produced it.
    """
    loose = sum(1 for r in results if abs(r["t"]) >= 2.5)
    events = database.collection("Event").count()
    doc = {
        "_key": "latest",
        "ranAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "corpusEvents": events,
        "tests": tested,
        "clearedLoose": loose,
        "clearedLoosePct": round(loose / tested * 100, 2),
        "survivors": survivors,
        "missesBarBy": round(miss_multiple, 1),
        "correctedBarOneIn": round(tested / ALPHA),
        "strongest": {"dyad": best["dyad"], "cameo": best["cameo"],
                      "horizon": best["horizon"], "target": best["target"],
                      "t": round(best["t"], 2), "n": best["n"],
                      "p": float(f"{best['p']:.3g}")},
        "rankedBy": "p",
    }
    if not database.has_collection("SweepResult"):
        database.create_collection("SweepResult")
    database.collection("SweepResult").insert(doc, overwrite=True)
    print(f"\n  stored as SweepResult/latest ({events:,} events, {tested:,} tests)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip the sweep; restate the stored figures and the Sharpe table")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                    help="replay the sweep on the corpus as it stood on this day; "
                         "does not overwrite SweepResult/latest")
    ap.add_argument("--json", default=None,
                    help="also write the top 50 cells to this file")
    args = ap.parse_args()

    if not args.quick:
        database = db()
        results, tested = sweep(database, until=args.until)
        survivors, tested, miss, ranked = report(results, tested)
        if args.json:
            pathlib.Path(args.json).write_text(json.dumps(
                {"until": args.until, "tests": tested, "missesBarBy": miss,
                 "top": ranked[:50]}, indent=1))
        if args.until:
            print(f"\n  replay to {args.until}: SweepResult/latest left untouched")
        else:
            store_result(database, results, tested, survivors, miss, ranked[0])

    # The tariff sweep, restated. t = Sharpe * sqrt(n), so these are one quantity;
    # Sharpe is the half that does not grow with the sample, which is what makes the
    # decay legible when the sign is wandering.
    print("\n  Tariff sweep (USA->CHN 163, 5d) — t and Sharpe are the same number:\n")
    print(f"  {'analogs':>8}{'t':>8}{'Sharpe':>10}")
    for n, t in [(8, 1.80), (12, -1.71), (20, -1.41), (40, 2.32), (80, -1.89)]:
        print(f"  {n:>8}{t:>+8.2f}{t / math.sqrt(n):>10.3f}")
    print("\n  Effect size decays toward zero; the sign wanders. Sharpe separates")
    print("  'the effect is small' from 'the sample is small'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
