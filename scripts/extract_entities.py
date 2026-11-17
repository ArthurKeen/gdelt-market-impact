#!/usr/bin/env python3
"""Recover WHO acted and WHAT was hit, from the coverage rather than from CAMEO.

CAMEO says `YEM -> SAU`, code 190. It does not say the Houthis, and it does not
say the world's largest oil processing facility — and both distinctions are the
difference between +14.68% and nothing. GDELT has no actor code for a non-state
group at all: it resolves the Houthis to `YEM`, Hezbollah to `LBN`, Hamas to
`PSE`. Searching every field of a full GDELT day for "HOUTHI" finds it only in
source URLs.

The coverage names them. Three GKG fields were pulled and never loaded:

  V2Organizations   the groups and companies the coverage names
  V2Persons         leaders and spokesmen — "abdul malik al-houthi" lands here,
                    not in organisations, so both fields have to be read
  V2Locations       every place named, at facility resolution, rather than the
                    single ActionGeo the Events table carries

GKG links to Events through CAMEOEVENTIDS, so an event inherits the entities its
own coverage named. "Yemen attacks Saudi Arabia" plus "the coverage names the
Houthis, Saudi Aramco and Abqaiq" is a materially finer question than CAMEO can
pose, and it is the one an analyst would actually ask.

WHAT THIS IS NOT
----------------
A retrieval facet — a way to *find* events — not identification and not a causal
claim. Coverage is written around the market reaction, so an article naming the
operator may do so partly BECAUSE the price moved. That is selection on the
outcome, it is unresolved, and the test meant to clear it failed: the effect does
not replicate on coverage published before the market opened.

Attribution is a second reason for caution, and here the ambiguity is the point
rather than a defect. For Abqaiq the Houthis claimed responsibility while the US
and Saudi Arabia named Iran, so the honest output is *both*, with the share of
coverage behind each — not a single winner picked by a threshold.

    python scripts/extract_entities.py --dry-run    # classify, write nothing
    python scripts/extract_entities.py              # ...and enrich Event docs
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _common import db  # noqa: E402

# Non-state actors GDELT flattens into a country code, with the aliases the
# coverage actually uses. Deliberately short: every addition is a judgement about
# who counts, and a long list invites tuning until it says what you hoped.
PERPETRATORS = {
    "Houthis":        ("houthi", "ansar allah", "ansarallah"),
    "Hezbollah":      ("hezbollah", "hizbullah", "hizballah"),
    "IRGC / Quds":    ("islamic revolutionary guard", "quds force", "irgc"),
    "Hamas":          ("hamas", "al-qassam"),
    "Kataib Hezbollah": ("kataib hezbollah", "kata'ib hezbollah"),
    "AQAP / al-Qaeda":  ("al-qaeda", "al qaeda", "aqap"),
    "Islamic State":  ("islamic state of iraq", "isil", "daesh"),
}

# Operators whose name in the coverage implies an energy target.
OPERATORS = {
    "Saudi Aramco":   ("aramco",),
    "ADNOC":          ("adnoc",),
    "Kuwait Petroleum": ("kuwait petroleum",),
    "SABIC":          ("sabic",),
    "Petro Rabigh":   ("petro rabigh",),
    "NIOC":           ("national iranian oil",),
}

# Named energy infrastructure, taken from GDELT's own location strings rather
# than a gazetteer, so it matches what the coverage writes.
FACILITIES = {
    "Abqaiq":     ("abqaiq", "buqayq"),
    "Khurais":    ("khurais",),
    "Ras Tanura": ("ras tanura",),
    "Shaybah":    ("shaybah",),
    "Jubail":     ("jubail",),
    "Yanbu":      ("yanbu",),
    "Safaniya":   ("safaniya",),
    "Khafji":     ("khafji",),
    "Jazan":      ("jazan", "jizan"),
    "Haradh":     ("haradh",),
    "Qatif":      ("qatif",),
    "Kharg Island": ("kharg",),
    "Strait of Hormuz": ("hormuz",),
}


def _found(blob: str, gazetteer: dict[str, tuple[str, ...]]) -> list[str]:
    return [name for name, aliases in gazetteer.items()
            if any(a in blob for a in aliases)]


def per_event_signal(gkg_path: pathlib.Path, event_days: dict[str, str]):
    """Per event: how many linked articles named each entity, and how many in all.

    Counted per ARTICLE, not per mention, so one heavily-repeating wire story
    cannot manufacture a share on its own.
    """
    acc = collections.defaultdict(
        lambda: {"arts": 0,
                 "perp": collections.Counter(),
                 "oper": collections.Counter(),
                 "fac": collections.Counter()})
    for line in gkg_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        # A set, not a list: CAMEOEVENTIDS repeats ids within one article, so
        # iterating the raw list counts a single article once per repeat. That
        # inflated coverage counts ~5x on the Abqaiq events (38 for 7) and made
        # the "articles" evidence figure meaningless while leaving shares roughly
        # intact — which is exactly how it survived a read-through.
        keys = {k for k in (str(x) for x in (row.get("_linkedEventIds") or []))
                if k in event_days}
        if not keys:
            continue
        # Persons matters as much as organisations: "abdul malik al-houthi" and
        # "ansar allah houthi" are person strings, and dropping them loses the
        # clearest attributions in the file.
        who = ((row.get("V2Organizations") or "") + ";"
               + (row.get("V2Persons") or "")).lower()
        locs = (row.get("V2Locations") or "").lower()
        perps = _found(who, PERPETRATORS)
        opers = _found(who, OPERATORS)
        facs = _found(locs, FACILITIES)
        for k in keys:
            a = acc[k]
            a["arts"] += 1
            a["perp"].update(perps)
            a["oper"].update(opers)
            a["fac"].update(facs)
    return acc


def _shares(counter, total, floor):
    """Entities named by at least `floor` of the coverage, strongest first."""
    out = [{"name": n, "pct": round(c / total, 4), "articles": c}
           for n, c in counter.items() if c / total >= floor]
    return sorted(out, key=lambda d: -d["pct"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    ap.add_argument("--min-articles", type=int, default=3,
                    help="events with less coverage get no signal — a share over "
                         "one or two articles is noise")
    ap.add_argument("--floor", type=float, default=0.15,
                    help="minimum share of an event's coverage naming an entity "
                         "for it to be recorded (default 0.15)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    database = db()
    print("[entities] reading events…")
    event_days = {r["k"]: r["d"] for r in database.aql.execute(
        "WITH Event FOR e IN Event RETURN {k: e._key, d: e.dateAdded}",
        batch_size=200000, stream=True)}
    print(f"[entities] {len(event_days):,} events")

    acc = per_event_signal(pathlib.Path(args.data) / "gkg.jsonl", event_days)
    print(f"[entities] {len(acc):,} events have linked coverage")

    docs = []
    for key, a in acc.items():
        if a["arts"] < args.min_articles:
            continue
        perps = _shares(a["perp"], a["arts"], args.floor)
        opers = _shares(a["oper"], a["arts"], args.floor)
        facs = _shares(a["fac"], a["arts"], args.floor)
        if not (perps or opers or facs):
            continue
        docs.append({
            "_key": key,
            "coverageArticles": a["arts"],
            # Flat name arrays are what queries filter on; the detail objects
            # carry the evidence so a hit can be argued with rather than trusted.
            "perpetrators": [d["name"] for d in perps],
            "targetOperators": [d["name"] for d in opers],
            "targetFacilities": [d["name"] for d in facs],
            "entityDetail": {"perpetrators": perps, "operators": opers,
                             "facilities": facs},
        })

    print(f"[entities] {len(docs):,} events carry at least one named entity")
    for label, field in (("perpetrators", "perpetrators"),
                         ("operators", "targetOperators"),
                         ("facilities", "targetFacilities")):
        c = collections.Counter(n for d in docs for n in d[field])
        print(f"\n  {label} named (events):")
        for name, n in c.most_common(8):
            print(f"     {name:22s} {n:>7,}")

    if args.dry_run:
        print("\n[entities] dry run — nothing written")
        return 0

    coll = database.collection("Event")
    for i in range(0, len(docs), 5000):
        coll.import_bulk(docs[i:i + 5000], on_duplicate="update")
    print(f"\n[entities] enriched {len(docs):,} Event documents")
    for field in ("perpetrators[*]", "targetOperators[*]", "targetFacilities[*]"):
        coll.add_index({"type": "persistent", "fields": [field], "sparse": True})
    print("[entities] indexed perpetrators, targetOperators, targetFacilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
