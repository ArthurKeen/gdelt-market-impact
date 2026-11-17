#!/usr/bin/env python3
"""Build the exposure graph: from a place where something happened to who feels it.

    Event -locatedIn-> Location -inRegion-> Chokepoint <-flowsThrough- Good
    Good <-dependsOn- Country <-listedIn- Index|Company      (who imports it)
    Good <-produces-  Country                               (who ships it)
    Good|Chokepoint <-uses- Company <-suppliedBy- Company <-holds- Holding

So a strike near Abqaiq reaches Japan through crude (95% of its imports from the
Middle East) and NVIDIA in the demo portfolio through helium (Qatar, 35% of world
output) and TSMC. Each curated edge carries its figure and its source
(scripts/exposure_reference.py); `inRegion` is computed from coordinates.

Its own named graph, `gdelt_exposure`, so build_graph.py's rebuild of
`gdelt_market` cannot drop these definitions. Re-runnable: every edge has a
deterministic key, and `inRegion` edges for locations that left a box are pruned.

    python scripts/build_exposure_graph.py            # build or refresh
    python scripts/build_exposure_graph.py --dry-run  # count, write nothing
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "scripts")
import exposure_reference as R                                  # noqa: E402
from build_graph import edge_key, key_safe                       # noqa: E402

GRAPH = "gdelt_exposure"
NODES = ["Chokepoint", "Good", "Country"]
EDGES = ["inRegion", "flowsThrough", "dependsOn", "produces", "uses",
         "suppliedBy", "listedIn", "sameAs"]
EDGE_DEFINITIONS = [
    {"edge_collection": "inRegion", "from_vertex_collections": ["Location"],
     "to_vertex_collections": ["Chokepoint"]},
    {"edge_collection": "flowsThrough", "from_vertex_collections": ["Good"],
     "to_vertex_collections": ["Chokepoint"]},
    {"edge_collection": "dependsOn", "from_vertex_collections": ["Country"],
     "to_vertex_collections": ["Good"]},
    {"edge_collection": "produces", "from_vertex_collections": ["Country"],
     "to_vertex_collections": ["Good"]},
    {"edge_collection": "uses", "from_vertex_collections": ["Company"],
     "to_vertex_collections": ["Good", "Chokepoint"]},
    {"edge_collection": "suppliedBy", "from_vertex_collections": ["Company"],
     "to_vertex_collections": ["Company"]},
    {"edge_collection": "listedIn", "from_vertex_collections": ["Index", "Company"],
     "to_vertex_collections": ["Country"]},
    {"edge_collection": "sameAs", "from_vertex_collections": ["Country"],
     "to_vertex_collections": ["Actor"]},
]

IN_REGION_AQL = """
FOR l IN Location
  FILTER l.lat != null AND l.lng != null
  FOR r IN @regions
    FILTER l.lat >= r.lat[0] AND l.lat <= r.lat[1]
       AND l.lng >= r.lng[0] AND l.lng <= r.lng[1]
    RETURN { loc: l._key, region: r.key }
"""


def cited(source: tuple[str, str]) -> dict:
    return {"source": source[0], "sourceUrl": source[1], "asOf": R.AS_OF}


def curated() -> dict[str, list[dict]]:
    """Every curated node and edge. Pure: no database."""
    out: dict[str, list[dict]] = {c: [] for c in NODES + EDGES + ["Index", "Company"]}
    for key, r in R.REGIONS.items():
        out["Chokepoint"].append({"_key": key, "name": r["name"],
                                  "box": {"lat": list(r["lat"]), "lng": list(r["lng"])}})
    for order, (key, g) in enumerate(R.GOODS.items()):
        out["Good"].append({"_key": key, "order": order, **g})
    for code, name in R.COUNTRIES.items():
        out["Country"].append({"_key": code, "name": name})

    def edge(coll, frm, to, *ident, **attrs):
        out[coll].append({"_key": edge_key(frm, to, *ident), "_from": frm, "_to": to, **attrs})

    for good, cp, share, lo, hi, measure, src in R.FLOWS:
        edge("flowsThrough", f"Good/{good}", f"Chokepoint/{cp}",
             share=share, lo=lo, hi=hi, measure=measure, **cited(src))
    for c, good, share, lo, hi, measure, src in R.DEPENDS:
        edge("dependsOn", f"Country/{c}", f"Good/{good}", via=R.VIA,
             share=share, lo=lo, hi=hi, measure=measure, **cited(src))
    for c, good, share, measure, src in R.PRODUCES:
        edge("produces", f"Country/{c}", f"Good/{good}", via=R.VIA,
             share=share, measure=measure, **cited(src))
    for sym, coll, name, country in R.MARKETS:
        # The S&P is the benchmark the build already owns: link it to its
        # country, but do not restyle it as an exposure series.
        if sym != "^GSPC":
            out[coll].append({"_key": key_safe(sym), "symbol": sym, "name": name,
                              "exposure": True, "country": country}
                             | ({"ticker": sym} if coll == "Company" else {}))
        if country:
            edge("listedIn", f"{coll}/{key_safe(sym)}", f"Country/{country}")
    for sym, target, role, note in R.USES:
        edge("uses", f"Company/{key_safe(sym)}", target, role, role=role, note=note)
    for cust, sup, name, note in R.SUPPLIED_BY:
        edge("suppliedBy", f"Company/{key_safe(cust)}", f"Company/{key_safe(sup)}",
             customer=name, note=note, **cited(R.TENK))
    return out


def ensure(database) -> None:
    for c in NODES:
        if not database.has_collection(c):
            database.create_collection(c)
    for c in EDGES:
        if not database.has_collection(c):
            database.create_collection(c, edge=True)
    if not database.has_graph(GRAPH):
        database.create_graph(GRAPH, edge_definitions=EDGE_DEFINITIONS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    built = curated()
    for c, rows in built.items():
        if rows:
            print(f"  [curated] {c:<13} {len(rows):>4}")
    if args.dry_run:
        return 0

    from _common import db
    database = db()
    ensure(database)
    for c, rows in built.items():
        if rows:
            database.collection(c).import_bulk(rows, on_duplicate="update")

    # Countries that are also corpus actors: the bridge from "who did it" to
    # "who depends on it". Only where the Actor exists.
    actors = set(database.aql.execute(
        "FOR a IN Actor FILTER a._key IN @k RETURN a._key",
        bind_vars={"k": list(R.COUNTRIES)}))
    same = [{"_key": edge_key(f"Country/{c}", f"Actor/{c}"),
             "_from": f"Country/{c}", "_to": f"Actor/{c}"} for c in sorted(actors)]
    database.collection("sameAs").import_bulk(same, on_duplicate="update")

    regions = [{"key": k, "lat": list(r["lat"]), "lng": list(r["lng"])}
               for k, r in R.REGIONS.items()]
    hits = list(database.aql.execute(IN_REGION_AQL, bind_vars={"regions": regions},
                                     batch_size=50000, ttl=600))
    rows = [{"_key": edge_key(f"Location/{h['loc']}", f"Chokepoint/{h['region']}"),
             "_from": f"Location/{h['loc']}", "_to": f"Chokepoint/{h['region']}"}
            for h in hits]
    coll = database.collection("inRegion")
    for i in range(0, len(rows), 5000):
        coll.import_bulk(rows[i:i + 5000], on_duplicate="update")
    stale = list(database.aql.execute(
        "FOR e IN inRegion FILTER e._key NOT IN @keep REMOVE e IN inRegion RETURN 1",
        bind_vars={"keep": [r["_key"] for r in rows]}))
    by_region = {}
    for h in hits:
        by_region[h["region"]] = by_region.get(h["region"], 0) + 1
    print(f"  [inRegion] {len(rows):,} locations in a region {by_region}"
          + (f", {len(stale)} stale removed" if stale else ""))
    print(f"  [sameAs]   {len(same)} countries linked to corpus actors")
    print(f"[exposure] graph '{GRAPH}' ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
