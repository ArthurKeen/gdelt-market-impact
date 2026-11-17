#!/usr/bin/env python3
"""Can this corpus tell us which groups are Iran-aligned? It cannot.

An `alignedWith` edge — Iran sponsors the Houthis, Hezbollah and so on — would let
the UI ask about a sponsor's proxies as one class, which is the difference between
a class too small to test (8 events) and one that is (117-141 days). The tempting
shortcut is to derive it from our own data: groups named alongside Iran in the same
article are presumably Iran's.

They are not. This measures it, so nobody has to take that on trust.

Reads data/gkg.jsonl. No database, no credentials.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

# Substrings, lowercased, matched against the GKG entity columns. Crude on purpose:
# a generous matcher can only INFLATE co-mention, so it biases toward the shortcut
# working, which makes the negative result harder to dismiss.
GROUPS = {"houthi": "Houthis", "hezbollah": "Hezbollah", "hamas": "Hamas",
          "al-qaeda": "AQAP / al-Qaeda", "islamic state": "Islamic State"}
IRAN = ("iran", "irgc", "islamic revolutionary guard", "quds force", "tehran")

# What the answer has to be to be usable: adversaries must not outrank clients.
# Ground truth here is not contested in the two directions that matter — AQAP and
# Islamic State are Iran's enemies; Hamas receives Iranian funding on every serious
# account, contested only in degree.
ADVERSARIES = ("AQAP / al-Qaeda", "Islamic State")
CLIENT = "Hamas"


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/gkg.jsonl")
    if not path.exists():
        print(f"  {path} not found — run scripts/gdelt_http_pull.py first")
        return 1

    total = iran_total = 0
    seen: collections.Counter = collections.Counter()
    with_iran: collections.Counter = collections.Counter()
    with path.open() as fh:
        for line in fh:
            total += 1
            d = json.loads(line)
            blob = ";".join(filter(None, (d.get("V2Organizations"), d.get("V2Persons"),
                                          d.get("V2Locations")))).lower()
            has_iran = any(k in blob for k in IRAN)
            iran_total += has_iran
            for key, name in GROUPS.items():
                if key in blob:
                    seen[name] += 1
                    with_iran[name] += has_iran

    base = iran_total / total
    print(f"  GKG articles {total:,} — naming Iran anywhere {iran_total:,} ({base:.1%})\n")
    print(f"  {'group':<20}{'articles':>10}{'with Iran':>12}{'rate':>9}{'lift':>9}")
    print("  " + "-" * 60)
    lift = {}
    for name in sorted(seen, key=lambda n: -(with_iran[n] / seen[n])):
        rate = with_iran[name] / seen[name]
        lift[name] = rate / base
        print(f"  {name:<20}{seen[name]:>10,}{with_iran[name]:>12,}"
              f"{rate:>8.1%}{lift[name]:>8.2f}x")

    print("\n  Lift = how much more often Iran is named in the same article than in a"
          "\n  random article here. It measures COVERAGE, not sponsorship.\n")

    worst_adversary = max((lift.get(a, 0), a) for a in ADVERSARIES)
    client = lift.get(CLIENT, 0)
    if worst_adversary[0] > client:
        print(f"  VERDICT: unusable. {worst_adversary[1]} — an Iranian ADVERSARY — scores"
              f" {worst_adversary[0]:.2f}x,\n  above {CLIENT} at {client:.2f}x, which"
              " Iran funds on every serious account.\n  The signal is regional"
              " co-coverage: everyone in a Yemen story is named together.\n"
              "  Alignment has to come from a source that codes it. See"
              " docs/alignment-data.md.")
        return 0
    print("  VERDICT: co-mention now ranks clients above adversaries — re-read the doc"
          "\n  before trusting it; this test was written when it did not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
