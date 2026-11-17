#!/usr/bin/env python3
"""Per-date volatility baselines, so an event is judged against its own period.

The single-event verdict asks how far a move sits from what that series does
ordinarily, and "ordinarily" has been one number for the whole corpus. It is not
one number: WTI's daily sd runs 1.6% to 8.3% across half-years, a 5.3x spread, and
a single sigma over that is not a property of the event being measured.

Two conditional baselines are written beside the unconditional one, and NEITHER
replaces it — the UI reports all three, because switching silently would move every
published number in our own favour.

  trailing   sd of the WINDOW observations before the day. Adapts to anything,
             named or not, and uses only what was known at the time.
  regime     sd of the detected volatility regime containing the day
             (scripts/volatility_regimes.py). Sharper across a boundary than a
             trailing window, which blurs one regime into the next.

Regimes are detected on DAILY returns and then applied to the h-day moves. h-day
forward returns overlap, and overlapping windows autocorrelate, which manufactures
regime structure; daily returns do not have that problem.

    python scripts/build_conditional_baselines.py
    python scripts/build_conditional_baselines.py --dry-run
"""
from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

import numpy as np

sys.path.insert(0, "scripts")
from _common import db                                              # noqa: E402
from build_graph import key_safe                                    # noqa: E402
from build_inferred_edges import (BENCHMARK_INDEX, HORIZONS,          # noqa: E402
                                  WINDOW_SUFFIX, WINDOWS, load_jsonl,
                                  price_series, sample_moves, study_targets,
                                  trading_days)
from volatility_regimes import regimes                              # noqa: E402

COLLECTION = "ConditionalBaseline"
WINDOW = 60          # trading observations in the trailing baseline
MIN_OBS = 20         # below this a conditional sd is noise; fall back to global


def _trailing(moves: list[float], window: int = WINDOW) -> list[float | None]:
    """sd of the `window` observations STRICTLY BEFORE each position.

    Strictly before for the same reason the event window anchors on the previous
    close: a baseline that includes the day it is judging has already seen the
    answer.
    """
    out: list[float | None] = []
    for i in range(len(moves)):
        prior = moves[max(0, i - window):i]
        out.append(statistics.stdev(prior) if len(prior) >= MIN_OBS else None)
    return out


def _regime_sd(days: list[str], moves: list[float],
               daily_dates: list[str], daily_rets) -> tuple[list[float | None], list[dict]]:
    """sd of the h-day moves inside each regime detected on the daily series."""
    regs = regimes(daily_dates, daily_rets)
    out: list[float | None] = [None] * len(days)
    meta = []
    for r in regs:
        idx = [i for i, d in enumerate(days) if r["from"] <= d <= r["to"]]
        sd = statistics.stdev([moves[i] for i in idx]) if len(idx) >= MIN_OBS else None
        meta.append({**r, "obs": len(idx), "moveSd": sd})
        for i in idx:
            out[i] = sd
    return out, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="delivered",
                    help="must match the BaselineStats window it sits beside")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    w_start, w_end_opt = WINDOWS[args.window]
    prices = load_jsonl(pathlib.Path(args.data) / "prices.jsonl")
    series = price_series(prices)
    benchmark = series.get(BENCHMARK_INDEX)

    docs = []
    for symbol, _target, use_abnormal in study_targets(prices):
        s = series.get(symbol)
        if not s:
            continue
        # Daily returns of the raw series, for regime detection only.
        dd, dr = [], []
        for i in range(1, len(s[1])):
            a, b = s[1][i - 1], s[1][i]
            w_end = w_end_opt or s[0][-1]
            lo = w_start or s[0][0]
            if a > 0 and b > 0 and lo <= s[0][i] <= w_end:
                dd.append(s[0][i])
                dr.append(b / a - 1)
        if len(dd) < 120:
            continue
        dr_arr = np.array(dr)

        for horizon in HORIZONS:
            days = trading_days(s, w_start or s[0][0], w_end_opt or s[0][-1])
            moves, kept = sample_moves(s, benchmark, days, horizon, use_abnormal)
            if len(moves) < 120:
                continue
            trail = _trailing(moves)
            reg, meta = _regime_sd(kept, moves, dd, dr_arr)
            docs.append({
                "_key": f"{key_safe(symbol)}_{horizon}"
                        + WINDOW_SUFFIX.get(args.window, f"_{args.window}"),
                "windowId": args.window,
                "symbol": symbol, "horizonDays": horizon,
                "useAbnormal": use_abnormal,
                "dates": kept,
                "pos": {d: i for i, d in enumerate(kept)},
                "trailSd": trail,
                "regimeSd": reg,
                "regimes": meta,
                "window": WINDOW,
            })

    n_reg = {d["symbol"]: len(d["regimes"]) for d in docs if d["horizonDays"] == 1}
    print(f"  {len(docs)} (symbol, horizon) baselines over {len(n_reg)} series")
    for sym, n in sorted(n_reg.items())[:8]:
        print(f"    {sym:<10}{n} regimes")
    if args.dry_run:
        print("  dry run — nothing written")
        return 0

    d = db()
    if d.has_collection(COLLECTION):
        # Remove only THIS window's rows. Truncating deletes the other window's
        # too — the `full` build silently wiped the delivered conditional
        # baselines that way, which are the ones every published figure uses.
        d.aql.execute(
            "FOR x IN @@c FILTER NOT_NULL(x.windowId, 'delivered') == @w REMOVE x IN @@c",
            bind_vars={"@c": COLLECTION, "w": args.window})
    else:
        d.create_collection(COLLECTION)
    d.collection(COLLECTION).import_bulk(docs, on_duplicate="replace")
    print(f"  wrote {len(docs)} docs to {COLLECTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
