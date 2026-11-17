"""The flagship causal number, and the contrast case that bounds it.

Two oil events, each measured both ways, so the anchoring argument is shown rather
than asserted:

  * **Abqaiq/Khurais, 2019-09-14** — a surprise Saturday drone attack on Saudi
    processing capacity. Unanticipated, precisely timed, exogenous. This is the
    identifying variation an observational panel cannot supply, and the most
    defensible causal number this project owns.
  * **Saudi-Russia price war, 2020-03-09** — OPEC+ broke down on the Friday and
    Riyadh cut its selling prices over the weekend. WTI -24.59% on reopening, the
    largest single-day move in the corpus and the strongest causal claim here.
  * **US ban on Russian oil imports, 2022-03-08** — a widely-telegraphed policy
    step taken two weeks into the invasion. Kept as the case where the answer is
    small.

    NOTE, 2026-08-23: this case used to be the script's punchline, on the strength
    of reading -12.13% at 1 day and supporting a claim that anticipation decides
    measurability. That was an anchoring bug (see below), not a finding. It reads
    +3.60% corrected, and the negative figure belonged to the UAE output
    announcement the next day. The anticipation hypothesis is plausible and is no
    longer asserted anywhere in this repo.

Anchors compared:
  onset — the documented event date. `forward_return` bases on the last close
          STRICTLY BEFORE it, so the event day's own reaction is inside the
          window. It used to base on the last close *at or before*, which is the
          same thing for a weekend onset and wrong for a weekday one — the window
          began after the reaction. That is the bug the note above refers to.
  peak  — the day news coverage peaked, which is what the pre-fix code used. News
          volume lags the shock, so this bases on an already-moved close and
          measures the decay instead of the move.

    python scripts/demo_anchor_contrast.py
    python scripts/demo_anchor_contrast.py --symbol BZ=F     # Brent instead of WTI
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from build_graph import load_jsonl  # noqa: E402
from build_inferred_edges import HORIZONS, forward_return, price_series  # noqa: E402

REPO = pathlib.Path(__file__).parent.parent

# (label, documented onset, observed coverage-peak day, what economics expects)
CASES = [
    ("Saudi-Russia price war", "2020-03-09", "2020-03-10", "supply war → oil DOWN, hard"),
    ("Abqaiq/Khurais attack", "2019-09-14", "2019-09-16", "surprise → oil UP"),
    ("US ban on Russian oil", "2022-03-08", "2022-03-09", "telegraphed → small"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="CL=F", help="price symbol (default WTI CL=F)")
    ap.add_argument("--data", default="data")
    args = ap.parse_args()

    prices = load_jsonl(REPO / args.data / "prices.jsonl")
    if not prices:
        print("no prices — run scripts/market_data_pull.py first")
        return 1
    series = price_series(prices)
    if args.symbol not in series:
        print(f"{args.symbol} not in the price slice; have: {sorted(series)}")
        return 1

    hdr = "".join(f"{str(h)+'d':>9}" for h in HORIZONS)
    print(f"{args.symbol} forward return from each anchor\n")
    print(f"  {'event':26s} {'anchor':>6s}{hdr}   expectation")
    print("  " + "-" * (26 + 7 + 9 * len(HORIZONS) + 32))
    for label, onset, peak, expectation in CASES:
        for tag, day in (("onset", onset), ("peak", peak)):
            cells = ""
            for horizon in HORIZONS:
                value = forward_return(series[args.symbol], day, horizon)
                cells += f"{value * 100:+9.2f}" if value is not None else f"{'n/a':>9}"
            lead = label if tag == "onset" else ""
            note = f"   {expectation}" if tag == "onset" else ""
            print(f"  {lead:26s} {tag:>6s}{cells}{note}")
        print()

    print("How to read it:")
    print("  Price war, onset-anchored: -24.59% at 1 day. A production agreement")
    print("    collapsing over a weekend, with no repair in sight. The largest single-day")
    print("    move in the corpus, and the strongest causal claim this project owns.")
    print("    Read the 10-day cell with care: it runs into the COVID drawdown, so it")
    print("    is two events stacked. The 1-day figure is the clean one.")
    print("  Abqaiq, onset-anchored: +14.68% at 1 day. A surprise attack removing ~5%")
    print("    of world supply moved oil up, hard, immediately. Anchored on the coverage")
    print("    peak the same event reads NEGATIVE at every horizon — the pre-fix bug.")
    print("  Russian ban: small at 1 day, and we no longer claim to know why. It reads")
    print("    +3.60%; it read -12.13% before the anchoring fix, and that number was the")
    print("    UAE output announcement the following day rather than the ban. The ban was")
    print("    probably priced during two weeks of invasion coverage, but these figures")
    print("    do not show it, so the claim is withdrawn rather than restated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
