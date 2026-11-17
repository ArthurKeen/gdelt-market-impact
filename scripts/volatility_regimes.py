#!/usr/bin/env python3
"""Find the volatility regimes in a price series, without being told they exist.

COVID is the obvious one, but naming it is an outside-the-corpus judgement, and
the boundaries would be arbitrary — why 15 February and not the 20th? This asks
the series instead: where does the VARIANCE change by more than chance would
explain? The answer is derived, dated, and reproducible, and it finds regimes
nobody thought to look for.

Method: binary segmentation with a Gaussian variance model. A segment's cost is
n*log(sigma^2); a split is accepted only if it improves the total by more than a
BIC penalty, so noise does not buy itself a boundary. Daily returns are treated as
zero-mean, which over a 60-day window is a rounding error next to their variance.

This is the DETECTION half. The measurement half does not need it: a conditional
baseline (see --compare) adapts to whatever regime it is in, named or not. The
regimes are for the reader — so the corpus can say what it contains.

    python scripts/volatility_regimes.py                 # the headline series
    python scripts/volatility_regimes.py --symbol XLE
    python scripts/volatility_regimes.py --all --quiet   # every series, summary
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

sys.path.insert(0, "scripts")
from _common import db                                        # noqa: E402

MIN_SEG = 30          # trading days; shorter "regimes" are events, not regimes
DEFAULT = ("CL=F", "_GSPC", "XLE", "XLK", "GC=F")


def _cost(v: np.ndarray) -> float:
    """Gaussian negative log-likelihood for a zero-mean segment, up to constants."""
    if len(v) < 2:
        return 0.0
    var = float(np.mean(v ** 2))
    if var <= 0:
        return 0.0
    return len(v) * math.log(var)


def segment(r: np.ndarray, penalty: float, lo: int = 0, hi: int | None = None,
            out: list | None = None) -> list[int]:
    """Recursive binary segmentation; returns accepted change-point indices."""
    if out is None:
        out = []
    if hi is None:
        hi = len(r)
    if hi - lo < 2 * MIN_SEG:
        return out
    whole = _cost(r[lo:hi])
    best, best_i = None, None
    for i in range(lo + MIN_SEG, hi - MIN_SEG + 1):
        c = _cost(r[lo:i]) + _cost(r[i:hi])
        if best is None or c < best:
            best, best_i = c, i
    if best is None or whole - best <= penalty:
        return out
    out.append(best_i)
    segment(r, penalty, lo, best_i, out)
    segment(r, penalty, best_i, hi, out)
    return out


def regimes(dates: list[str], rets: np.ndarray) -> list[dict]:
    penalty = 2.0 * math.log(len(rets))        # BIC: one variance parameter added
    cuts = sorted(segment(rets, penalty))
    bounds = [0, *cuts, len(rets)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        sd = float(np.std(rets[a:b], ddof=1)) if b - a > 1 else 0.0
        out.append({"from": dates[a], "to": dates[b - 1], "days": b - a, "sd": sd})
    return out


def series_returns(d, symbol, start=None, end=None):
    ps = d.collection("PriceSeries").get(symbol)
    if not ps:
        return None, None
    dates, closes, out_d, out_r = ps["dates"], ps["closes"], [], []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        dt = dates[i]
        if a > 0 and b > 0 and (not start or dt >= start) and (not end or dt <= end):
            out_d.append(dt)
            out_r.append(b / a - 1)
    return out_d, np.array(out_r)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", action="append", dest="symbols")
    ap.add_argument("--all", action="store_true", help="every series in the graph")
    ap.add_argument("--from", dest="start", default="2018-02-27")
    ap.add_argument("--to", dest="end", default="2022-03-31")
    ap.add_argument("--quiet", action="store_true", help="one line per series")
    args = ap.parse_args()

    d = db()
    if args.all:
        syms = [x["_key"] for x in d.aql.execute("FOR p IN PriceSeries RETURN {_key: p._key}")]
    else:
        syms = args.symbols or list(DEFAULT)

    for sym in syms:
        dates, r = series_returns(d, sym, args.start, args.end)
        if r is None or len(r) < 4 * MIN_SEG:
            continue
        reg = regimes(dates, r)
        loud = max(reg, key=lambda x: x["sd"])
        quiet = min(reg, key=lambda x: x["sd"])
        if args.quiet:
            print(f"  {sym:<10}{len(reg)} regimes   loudest {loud['from']}..{loud['to']} "
                  f"sd {loud['sd']:.2%}   spread {loud['sd']/max(quiet['sd'],1e-9):.1f}x")
            continue
        print(f"\n  {sym} — {len(reg)} regime(s) found in {len(r)} trading days")
        print(f"  {'from':<12}{'to':<12}{'days':>6}{'daily sd':>11}   vs series")
        print("  " + "-" * 62)
        whole = float(np.std(r, ddof=1))
        for x in reg:
            bar = "#" * min(40, int(x["sd"] / whole * 12))
            print(f"  {x['from']:<12}{x['to']:<12}{x['days']:>6}{x['sd']:>10.2%}"
                  f"   {x['sd']/whole:>4.1f}x {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
