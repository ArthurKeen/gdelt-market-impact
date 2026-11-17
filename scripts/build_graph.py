"""Create collections + named graph, load nodes + structural/temporal edges (stage 2).

The mechanical half of the pipeline (a plain relational-to-graph mapping):
  - Event/Actor/Location/Theme/Source nodes from the stage-1 pull
  - Actor canonicalisation (collapse GDELT's name variants onto the CAMEO code)
  - Market layer (Sector/Index/Commodity/Company/MacroVariable) from prices.jsonl
  - Portfolio layer (a demo portfolio, for the cross-graph join)
  - Structural edges: mentions, taggedWith, locatedIn, partOf, holds
  - Temporal edges: precedes (lagDays), coOccursWith
  - Persistent indexes per docs/schema.md

Source-agnostic: reads whatever stage 1 produced (gdelt_http_pull.py or
bigquery_pull.py — both emit the same JSONL keys).

The inferred edges (similarTo / correlatesWith / affects) are stage 3, in
build_inferred_edges.py.

Examples:
  python scripts/build_graph.py --dry-run      # transform + report, no DB needed
  python scripts/build_graph.py --drop         # rebuild from scratch
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import json
import pathlib
import tempfile
import time
import re

from _common import EDGE_COLLECTIONS, GRAPH_NAME, NODE_COLLECTIONS
from gdelt_vocab import THEME_GROUPS

# --- Market/portfolio reference data (demo scale; extend per client) ---------
# Sector ETFs are the *tradeable proxy* for a sector: the event study measures the
# ETF's realised move and attributes it to the Sector node (kept as `proxySymbol`
# rather than a new edge class, so the schema in docs/schema.md still holds).
SECTOR_ETF = {
    "XLE": "Energy", "XLF": "Financials", "XLK": "Information Technology",
    "XLI": "Industrials", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLV": "Health Care", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}
COMPANY_SECTOR = {
    "AAPL": "Information Technology", "NVDA": "Information Technology",
    "XOM": "Energy", "JPM": "Financials", "BA": "Industrials",
}
BENCHMARK_INDEX = "^GSPC"

# Demo portfolio so the cross-graph join is demoable end-to-end.
DEMO_PORTFOLIO = {
    "id": "demo1",
    "name": "Sample balanced portfolio",
    "client": "demo",
    "holdings": {"AAPL": 0.18, "NVDA": 0.14, "XOM": 0.12, "JPM": 0.11,
                 "BA": 0.08, "XLK": 0.15, "XLE": 0.12, "XLF": 0.10},
}

THEME_GROUP_OF = {t: g for g, ts in THEME_GROUPS.items() for t in ts}

# ArangoDB _key charset: letters, digits and _ - : . @ ( ) + , = ; $ ! * ' %
_KEY_BAD = re.compile(r"[^A-Za-z0-9_:.@()+,=;$!*'%-]")

# precedes lag ceiling. Set wide on purpose: the corpus is deliberately
# episode-clustered, so consecutive episodes sit months apart (2018-03 memo ->
# 2018-07 tariffs -> 2019-05 escalation) and a 30-day cap severed exactly the
# inter-episode hops the E1 narrative is made of — 1,608 of 8,419 transitions,
# measured. The edge carries `lagDays` and that field is indexed, so "only short
# lags" is a query-time filter (FILTER e.lagDays <= 30), not an ingest-time
# amputation. The ceiling only exists to stop a spine spanning unrelated eras.
MAX_PRECEDES_LAG_DAYS = 400


def edge_key(*parts: object) -> str:
    """Deterministic, ArangoDB-legal `_key` built from an edge's identifying parts.

    Edge writes go through `import_bulk(on_duplicate="update")`, which can only
    match an existing row **by `_key`**. Without one ArangoDB assigns a random key,
    so `on_duplicate` never fires and every re-run *inserts a duplicate set*
    instead of updating — measured: `correlatesWith` 1,088 -> 2,176, and a
    `build_graph.py` re-run without `--drop` would double all 983,341 edges while
    the counts still looked plausible and every traversal silently double-counted.

    Making each edge's natural identity explicit fixes that and makes incremental
    rebuilds possible: `correlatesWith` is (theme, asset, horizon), `affects` is
    (source, sector), `mentions` is (event, actor, role), and the rest are
    (from, to) plus whatever else distinguishes a pair.

    `/` is not legal in a `_key`, so document ids are flattened to `coll_key`.
    """
    return key_safe("--".join(str(p).replace("/", "_") for p in parts))


def key_safe(value: str) -> str:
    return _KEY_BAD.sub("_", value.strip())[:254] or "unknown"



def load_jsonl(path: pathlib.Path) -> list[dict]:
    """Read a whole JSONL file into memory. Small inputs only — see JsonlSource.

    Kept for `prices.jsonl` (~11MB) and for callers that genuinely need a list.
    Do NOT use it for events or GKG: at 5.5M events those files are 3.7GB and
    4.6GB on disk, and JSON inflates 4-6x as Python objects.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class JsonlSource:
    """A re-iterable, streaming view of a JSONL file.

    `load_jsonl` on events + GKG was the single largest memory consumer in this
    script and it took a machine down on 2026-08-24: reading both files as lists
    is ~35GB of Python objects before a single output document exists, and the
    builder then holds ~34M more dicts on top of that. Peak was comfortably 50GB.

    The builder iterates events twice and GKG once, so a plain generator will not
    do — this re-opens the file per iteration instead. Re-reading 3.7GB from an
    SSD costs a few seconds; holding it costs tens of gigabytes.

    `len()` is a line count, computed once and cached, because callers only want
    it for progress reporting.
    """

    __slots__ = ("path", "limit", "_len")

    def __init__(self, path: pathlib.Path, limit: int | None = None):
        self.path, self.limit, self._len = path, limit, None

    def __iter__(self):
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if self.limit is not None and i >= self.limit:
                    return
                if line.strip():
                    yield json.loads(line)

    def __len__(self) -> int:
        if self._len is None:
            if not self.path.exists():
                self._len = 0
            else:
                with self.path.open("rb") as fh:
                    n = sum(1 for line in fh if line.strip())
                self._len = min(n, self.limit) if self.limit is not None else n
        return self._len

    def __bool__(self) -> bool:
        return len(self) > 0


class EdgeSpool:
    """Append-only JSONL spool that looks enough like a list for the builder.

    The ten `edges[...].append(...)` sites are unchanged; only what they append
    TO is different. Edge documents are serialised straight to disk instead of
    accumulating — at 5.5M events that is ~28M dicts, another ~17GB that never
    needs to exist all at once.

    `arangoimport` wants a file anyway, so for the default path this removes a
    round trip rather than adding one: the spool IS the import file.
    """

    __slots__ = ("path", "n", "_fh")

    def __init__(self, path: pathlib.Path):
        self.path, self.n, self._fh = path, 0, path.open("w", encoding="utf-8")

    def append(self, doc: dict) -> None:
        self._fh.write(json.dumps(doc, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __len__(self) -> int:
        return self.n

    def __bool__(self) -> bool:
        return self.n > 0

    def __iter__(self):
        """Replay the spool — used by the import_bulk path, which wants dicts."""
        self.close()
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def iso_date(yyyymmdd) -> str | None:
    """20180322 -> '2018-03-22'. GDELT ships a few impossible dates; drop those."""
    if not yyyymmdd:
        return None
    s = str(yyyymmdd)[:8]
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
    except ValueError:
        return None


def _domain(url: str) -> str | None:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower().removeprefix("www.") if m else None


# --- Actor canonicalisation --------------------------------------------------
# GDELT's real duplication problem, measured on the pulled slice, is *name*
# variants under one code ("UNITED STATES" / "WASHINGTON" / "US" all under USA),
# not code collisions -- the CAMEO code already IS GDELT's canonical identifier.
# So we key Actor on the code and fold the observed spellings in as `aliases`,
# promoting the most frequent to the display name. That collapse is deterministic
# and explainable.
#
# Seam for the harder cases (one entity under several codes -- a leader appearing
# as both a person and a government code): `arango-entity-resolution`'s
# blocking+similarity+clustering pipeline runs over the Actor collection after
# this load. Wired via --er; not required for E1, where the dyad is code-clean.
CAMEO_TYPE = {
    "GOV": "government", "MIL": "military", "MED": "media", "BUS": "business",
    "LEG": "legislature", "JUD": "judiciary", "COP": "police", "OPP": "opposition",
    "REB": "rebels", "CVL": "civilian", "EDU": "education", "ELI": "elite",
}


def actor_docs(events) -> dict[str, dict]:
    """Collapse (code -> name variants) into canonical Actor docs."""
    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    country: dict[str, str] = {}
    for ev in events:
        for side in ("1", "2"):
            code = (ev.get(f"Actor{side}Code") or "").strip()
            if not code:
                continue
            name = (ev.get(f"Actor{side}Name") or "").strip()
            if name:
                names[code][name] += 1
            cc = (ev.get(f"Actor{side}CountryCode") or "").strip()
            if cc:
                country.setdefault(code, cc)
    out = {}
    for code, variants in names.items():
        suffix = code[3:6] if len(code) > 3 else ""
        out[code] = {
            "_key": key_safe(code),
            "code": code,
            "name": variants.most_common(1)[0][0].title() if variants else code,
            "country": country.get(code),
            "type": CAMEO_TYPE.get(suffix, "state" if len(code) == 3 else "other"),
            "aliases": [n for n, _ in variants.most_common(8)],
            "aliasCount": len(variants),
            "mentionCount": sum(variants.values()),
            "resolution": "cameo-code",
        }
    return out


def transform(events, gkg, prices: list[dict], spool_dir: pathlib.Path | None = None) -> dict:
    """Pure transform: GDELT + price rows -> {collection: [docs]}. No DB access."""
    nodes: dict[str, dict[str, dict]] = {c: {} for c in NODE_COLLECTIONS}
    # Spool edges to disk when a directory is given (the default for real runs);
    # fall back to in-memory lists only for --dry-run and small smoke tests.
    edges: dict = ({c: EdgeSpool(spool_dir / f"{c}.jsonl") for c in EDGE_COLLECTIONS}
                   if spool_dir is not None else {c: [] for c in EDGE_COLLECTIONS})
    stats: dict[str, int] = collections.Counter()

    # --- Event / Location / Source nodes + mentions(actor) / locatedIn -----
    actors = actor_docs(events)
    nodes["Actor"] = {a["_key"]: a for a in actors.values()}

    for ev in events:
        gid = str(ev.get("GLOBALEVENTID") or "").strip()
        if not gid:
            stats["event_no_id"] += 1
            continue
        date = iso_date(ev.get("SQLDATE"))
        added = iso_date(ev.get("DATEADDED"))
        if not added:
            stats["event_bad_dateadded"] += 1
            continue
        a1 = (ev.get("Actor1Code") or "").strip() or None
        a2 = (ev.get("Actor2Code") or "").strip() or None
        key = key_safe(gid)
        nodes["Event"][key] = {
            "_key": key,
            "cameo": (ev.get("EventCode") or "").strip() or None,
            "cameoRoot": (ev.get("EventRootCode") or "").strip() or None,
            "quadClass": ev.get("QuadClass"),
            "goldstein": ev.get("GoldsteinScale"),
            "avgTone": ev.get("AvgTone"),
            "numMentions": ev.get("NumMentions"),
            # `date` = when the event happened; `dateAdded` = when GDELT published it.
            # The event study keys off dateAdded — that is when a market could react.
            "date": date,
            "dateAdded": added,
            "year": ev.get("Year"),
            "month": int(str(ev.get("MonthYear"))[4:6]) if ev.get("MonthYear") else None,
            # Denormalised facets: the analog-search view (stage 3) scores over
            # actor+cameo+theme+geo on a single document.
            "actorFromCode": a1,
            "actorToCode": a2,
            "geoCountry": (ev.get("ActionGeo_CountryCode") or "").strip() or None,
            "geoName": (ev.get("ActionGeo_FullName") or "").strip() or None,
            "themes": [],
            "sourceUrl": ev.get("SOURCEURL"),
        }
        for code, role in ((a1, "actor1"), (a2, "actor2")):
            if code and key_safe(code) in nodes["Actor"]:
                actor_id = f"Actor/{key_safe(code)}"
                edges["mentions"].append({
                    # (event, actor, role): the same actor can appear on both
                    # sides of a dyad, so role is part of the identity.
                    "_key": edge_key(f"Event/{key}", actor_id, role),
                    "_from": f"Event/{key}", "_to": actor_id,
                    "role": role,
                })
        feature = (ev.get("ActionGeo_FeatureID") or "").strip()
        adm1 = (ev.get("ActionGeo_ADM1Code") or "").strip()
        geo_id = feature or adm1 or (ev.get("ActionGeo_CountryCode") or "").strip()
        if geo_id:
            gkey = key_safe(geo_id)
            nodes["Location"].setdefault(gkey, {
                "_key": gkey,
                "name": (ev.get("ActionGeo_FullName") or "").strip() or None,
                "countryCode": (ev.get("ActionGeo_CountryCode") or "").strip() or None,
                "adm1": adm1 or None,
                "lat": ev.get("ActionGeo_Lat"),
                "lng": ev.get("ActionGeo_Long"),
            })
            edges["locatedIn"].append({
                "_key": edge_key(f"Event/{key}", f"Location/{gkey}"),
                "_from": f"Event/{key}", "_to": f"Location/{gkey}"})
        domain = _domain(ev.get("SOURCEURL") or "")
        if domain:
            nodes["Source"].setdefault(key_safe(domain), {"_key": key_safe(domain),
                                                          "domain": domain})

    # --- Theme nodes + Event->Theme taggedWith via the GKG join ------------
    # GKG `CAMEOEVENTIDS` is the article<->event bridge (47% of articles carry it;
    # 97% of referenced ids resolve in the same-day Events file). This is what puts
    # themes and article tone onto Events so analog search can facet on them.
    theme_hits: dict[tuple[str, str], int] = collections.Counter()
    event_tone: dict[str, list[float]] = collections.defaultdict(list)
    for art in gkg:
        themes = art.get("_matchedThemes")
        if themes is None:  # BigQuery path: resolve tokens here instead
            toks = {t.split(",")[0] for t in (art.get("V2Themes") or "").split(";") if t}
            themes = sorted(toks & set(THEME_GROUP_OF))
        linked = art.get("_linkedEventIds")
        if linked is None:
            linked = [i for i in (art.get("CAMEOEVENTIDS") or "").split(",") if i]
        tone = (art.get("V2Tone") or "").split(",")[0]
        try:
            tone_val = float(tone) if tone else None
        except ValueError:
            tone_val = None
        domain = (art.get("SourceCommonName") or "").strip().lower()
        if domain:
            nodes["Source"].setdefault(key_safe(domain), {"_key": key_safe(domain),
                                                          "domain": domain})
        for theme in themes:
            tkey = key_safe(theme)
            nodes["Theme"].setdefault(tkey, {
                "_key": tkey, "name": theme, "group": THEME_GROUP_OF.get(theme),
            })
        for gid in linked:
            ekey = key_safe(str(gid))
            if ekey not in nodes["Event"]:
                stats["gkg_link_miss"] += 1
                continue
            if tone_val is not None:
                event_tone[ekey].append(tone_val)
            for theme in themes:
                theme_hits[(ekey, key_safe(theme))] += 1

    for (ekey, tkey), n in theme_hits.items():
        # Dedicated collection, not mentions(role='theme'): this is the first hop
        # of the causal chain (Event -> Theme -> Sector), so the demo traversal
        # must not have to walk 336k Event->Actor edges and discard them.
        edges["taggedWith"].append({
            "_key": edge_key(f"Event/{ekey}", f"Theme/{tkey}"),
            "_from": f"Event/{ekey}", "_to": f"Theme/{tkey}",
            "articleCount": n,
        })
        nodes["Event"][ekey]["themes"].append(nodes["Theme"][tkey]["name"])
    for ekey, tones in event_tone.items():
        nodes["Event"][ekey]["gkgTone"] = round(sum(tones) / len(tones), 4)
        nodes["Event"][ekey]["gkgArticles"] = len(tones)

    # --- Market layer from prices.jsonl ------------------------------------
    # symbol -> (assetClass, friendlyName). Only FRED rows carry `name` (e.g.
    # CPIAUCSL -> "CPI"); yfinance rows have none, so every other layer keeps
    # name == symbol exactly as before.
    seen_symbols: dict[str, tuple[str, str | None]] = {}
    for row in prices:
        seen_symbols.setdefault(row["symbol"],
                                (row.get("assetClass", "company"), row.get("name")))
    for name in set(SECTOR_ETF.values()) | set(COMPANY_SECTOR.values()):
        skey = key_safe(name)
        etf = next((e for e, s in SECTOR_ETF.items() if s == name), None)
        nodes["Sector"][skey] = {"_key": skey, "name": name, "proxySymbol": etf}
    for symbol, (asset_class, friendly) in seen_symbols.items():
        if asset_class == "exposure":
            # Read straight from PriceSeries by the app's exposure panel. They
            # become graph nodes when the exposure graph models what each one
            # depends on; until then a node would be a name with no edges.
            continue
        skey = key_safe(symbol)
        if asset_class == "commodity":
            nodes["Commodity"][skey] = {"_key": skey, "symbol": symbol, "name": symbol}
        elif asset_class == "macro":
            # Label by the readable series name where FRED gave us one — a
            # MacroVariable node reading "CPI" beats "CPIAUCSL" on the canvas.
            nodes["MacroVariable"][skey] = {"_key": skey, "seriesId": symbol,
                                            "name": friendly or symbol}
        elif asset_class in ("index", "sector_etf"):
            nodes["Index"][skey] = {"_key": skey, "symbol": symbol, "name": symbol,
                                    "isSectorProxy": asset_class == "sector_etf",
                                    "sector": SECTOR_ETF.get(symbol)}
        else:
            nodes["Company"][skey] = {"_key": skey, "ticker": symbol, "name": symbol,
                                      "sector": COMPANY_SECTOR.get(symbol)}
    for ticker, sector in COMPANY_SECTOR.items():
        if key_safe(ticker) in nodes["Company"]:
            edges["partOf"].append({
                "_key": edge_key(f"Company/{key_safe(ticker)}", f"Sector/{key_safe(sector)}"),
                "_from": f"Company/{key_safe(ticker)}",
                "_to": f"Sector/{key_safe(sector)}"})
    bkey = key_safe(BENCHMARK_INDEX)
    if bkey in nodes["Index"]:
        for skey in nodes["Sector"]:
            edges["partOf"].append({
                "_key": edge_key(f"Sector/{skey}", f"Index/{bkey}"),
                "_from": f"Sector/{skey}", "_to": f"Index/{bkey}"})

    # --- Portfolio layer (demo) -------------------------------------------
    pkey = key_safe(DEMO_PORTFOLIO["id"])
    nodes["Portfolio"][pkey] = {"_key": pkey, "name": DEMO_PORTFOLIO["name"],
                                "client": DEMO_PORTFOLIO["client"]}
    for ticker, weight in DEMO_PORTFOLIO["holdings"].items():
        hkey = key_safe(f"{DEMO_PORTFOLIO['id']}_{ticker}")
        nodes["Holding"][hkey] = {"_key": hkey, "ticker": ticker, "weight": weight}
        edges["holds"].append({
            "_key": edge_key(f"Portfolio/{pkey}", f"Holding/{hkey}"),
            "_from": f"Portfolio/{pkey}", "_to": f"Holding/{hkey}",
            "weight": weight})
        if key_safe(ticker) in nodes["Company"]:
            edges["holds"].append({
                "_key": edge_key(f"Holding/{hkey}", f"Company/{key_safe(ticker)}"),
                "_from": f"Holding/{hkey}",
                "_to": f"Company/{key_safe(ticker)}"})
        elif key_safe(ticker) in nodes["Index"]:  # ETF position
            edges["holds"].append({
                "_key": edge_key(f"Holding/{hkey}", f"Index/{key_safe(ticker)}"),
                "_from": f"Holding/{hkey}",
                "_to": f"Index/{key_safe(ticker)}"})
        sector = COMPANY_SECTOR.get(ticker) or SECTOR_ETF.get(ticker)
        if sector:
            exkey = key_safe(f"{DEMO_PORTFOLIO['id']}_{sector}")
            ex = nodes["Exposure"].setdefault(exkey, {"_key": exkey, "sector": sector,
                                                      "weight": 0.0,
                                                      "portfolio": DEMO_PORTFOLIO["id"]})
            ex["weight"] = round(ex["weight"] + weight, 6)

    # --- Temporal edges ----------------------------------------------------
    # Two distinct jobs, deliberately modelled differently ("windowed and
    # sequential" traversal):
    #
    #   coOccursWith — same day, same dyad. Chained, not cliqued: a clique over a
    #       day's events is O(n^2) and buys no traversal reach a chain does not.
    #
    #   precedes — the day-to-day spine. Naively chaining *consecutive events* per
    #       dyad almost never crosses a day boundary (we ingest episode clusters, so
    #       consecutive events are overwhelmingly same-day) and yields a spine that
    #       barely exists. Linking every event to every event on the next day is a
    #       cross product; funnelling every event into the next day's top event
    #       builds a deliberate supernode, a well-known traversal bottleneck.
    #       So we anchor: one representative per
    #       (dyad, day) — the most-mentioned event, i.e. the day's headline — and
    #       chain those anchors across consecutive distinct days. Bounded by
    #       (dyad x day), no supernode, and every hop reads as "the next
    #       development in this relationship".
    #
    # The "windowed" half of R3 is served by the range index on Event.dateAdded, not
    # by edges; the anchors carry `isDayAnchor` so the spine is queryable directly.
    by_dyad: dict[tuple, list[tuple[str, str]]] = collections.defaultdict(list)
    for ekey, doc in nodes["Event"].items():
        by_dyad[(doc["actorFromCode"], doc["actorToCode"])].append((doc["dateAdded"], ekey))

    for dyad, items in by_dyad.items():
        items.sort()
        anchors: list[tuple[str, str]] = []
        for day, group in itertools.groupby(items, key=lambda pair: pair[0]):
            same_day = [k for _, k in group]
            for k1, k2 in itertools.pairwise(same_day):
                edges["coOccursWith"].append({
                    "_key": edge_key(f"Event/{k1}", f"Event/{k2}"),
                    "_from": f"Event/{k1}", "_to": f"Event/{k2}",
                    "window": "same-day", "date": day, "dyad": f"{dyad[0]}->{dyad[1]}",
                })
            anchor = max(same_day, key=lambda k: nodes["Event"][k]["numMentions"] or 0)
            nodes["Event"][anchor]["isDayAnchor"] = True
            anchors.append((day, anchor))
        for (d1, k1), (d2, k2) in itertools.pairwise(anchors):
            lag = (dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days
            if lag <= MAX_PRECEDES_LAG_DAYS:
                edges["precedes"].append({
                    "_key": edge_key(f"Event/{k1}", f"Event/{k2}"),
                    "_from": f"Event/{k1}", "_to": f"Event/{k2}",
                    "lagDays": lag, "dyad": f"{dyad[0]}->{dyad[1]}",
                })
            else:
                stats["precedes_gap_exceeded"] += 1

    return {"nodes": nodes, "edges": edges, "stats": stats}


# --- Load --------------------------------------------------------------------
EDGE_DEFINITIONS = [
    {"edge_collection": "mentions", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Actor", "Company"]},
    {"edge_collection": "taggedWith", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Theme"]},
    {"edge_collection": "locatedIn", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Location"]},
    {"edge_collection": "partOf", "from_vertex_collections": ["Company", "Sector"],
     "to_vertex_collections": ["Sector", "Index"]},
    {"edge_collection": "holds", "from_vertex_collections": ["Portfolio", "Holding"],
     "to_vertex_collections": ["Holding", "Company", "Index"]},
    {"edge_collection": "precedes", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Event"]},
    {"edge_collection": "coOccursWith", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Event"]},
    {"edge_collection": "similarTo", "from_vertex_collections": ["Event"],
     "to_vertex_collections": ["Event"]},
    {"edge_collection": "correlatesWith", "from_vertex_collections": ["Event", "Theme"],
     "to_vertex_collections": ["Sector", "Index", "Commodity"]},
    {"edge_collection": "affects", "from_vertex_collections": ["Commodity", "MacroVariable"],
     "to_vertex_collections": ["Sector"]},
]

INDEXES = {
    "Event": [["cameo"], ["year", "month"], ["dateAdded"], ["avgTone"],
              ["actorFromCode", "actorToCode"], ["isDayAnchor", "dateAdded"]],
    "Actor": [["code"]],
    "Theme": [["name"]],
    "Location": [["countryCode"]],
    "Holding": [["ticker"]],
    "precedes": [["lagDays"]],
    "mentions": [["role"]],
    "taggedWith": [["articleCount"]],   # rank an event's themes by article support
}


# Replication factor for every collection this script creates. The pilot
# deployment is a 3-DBServer cluster, and RF 1 means a lost DBServer is lost data
# with no second copy — the edge collections in particular have no older copy
# since the camelCase migration recreated them. Set at CREATE time on purpose:
# raising it afterwards works, but the next `--drop` rebuild would silently put it
# back to the cluster default, so the durable place for the decision is here.
DEFAULT_REPLICATION_FACTOR = 2


def _single_instance_lock(data: pathlib.Path):
    """Refuse to run while another build is loading the same database.

    These builds are long and are usually started with nohup, so they outlive the
    shell that launched them — and an orphan is invisible in the terminal that
    thinks it owns the job. Two concurrent loads on 2026-08-24 raced on the same
    collections and produced 78,282 import errors before anyone noticed: the
    symptom was "created 0, updated N" where a freshly dropped collection should
    have reported "created N, updated 0".

    O_EXCL on a pid file, with a liveness check so a crashed run does not wedge
    the next one.
    """
    import errno, os
    path = data / ".build_graph.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        try:
            other = int(path.read_text().strip() or 0)
        except (ValueError, OSError):
            other = 0
        alive = False
        if other:
            try:
                os.kill(other, 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            raise SystemExit(
                f"[build_graph] another build is already running (pid {other}).\n"
                f"  It may be an orphan from a previous shell — check with:\n"
                f"      ps -p {other} -o pid,lstart,command\n"
                f"  Kill it, or remove {path} if you are certain it is stale.")
        print(f"[build_graph] clearing stale lock from pid {other or '?'}")
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return path


def _await_drop(database, names, timeout: float = 180.0) -> None:
    """Block until every named collection is really gone, cluster-wide."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        present = [n for n in names if database.has_collection(n)]
        if not present:
            return
        time.sleep(1.0)
    raise SystemExit(f"[build_graph] collections still present {timeout:.0f}s after "
                     f"drop: {', '.join(present)} — cluster may be unhealthy")


def _create_with_retry(database, name: str, *, edge: bool, replication_factor: int,
                       attempts: int = 12) -> None:
    """Create a collection, tolerating a drop that has not finished propagating.

    ERR 1207 here does not mean "you already made this"; it means the cluster is
    still tearing down the previous one. Retrying with backoff is the documented
    way through it — the alternative is a build that fails a few minutes in, after
    the whole corpus has been transformed.
    """
    from arango.exceptions import CollectionCreateError

    for i in range(attempts):
        try:
            database.create_collection(name, edge=edge,
                                       replication_factor=replication_factor)
            return
        except CollectionCreateError as exc:
            if getattr(exc, "error_code", None) != 1207 or i == attempts - 1:
                raise
            wait = min(2.0 * (i + 1), 15.0)
            print(f"  [schema] {name}: still dropping, retrying in {wait:.0f}s "
                  f"({i + 1}/{attempts})")
            time.sleep(wait)
            if database.has_collection(name):
                return


def ensure_schema(database, drop: bool = False,
                  replication_factor: int = DEFAULT_REPLICATION_FACTOR) -> None:
    if drop:
        # Order matters: ArangoDB refuses to drop an edge collection that is still
        # part of a named graph ([HTTP 500][ERR 1942]), so the graph definition goes
        # first. drop_collections=False because the loop below owns that, and this
        # path must also work when the graph is absent but collections are not.
        if database.has_graph(GRAPH_NAME):
            database.delete_graph(GRAPH_NAME, drop_collections=False)
        for name in EDGE_COLLECTIONS + NODE_COLLECTIONS:
            if database.has_collection(name):
                database.delete_collection(name)
        # A drop is accepted by the coordinator before the DB-servers have finished
        # removing the shards. has_collection() then reports the collection gone
        # while a create still fails [HTTP 409][ERR 1207]. Harmless at 168k
        # documents, fatal at 5.5M: on 2026-08-24 the very first recreate — Actor —
        # died this way, because it had had the least time to settle since its drop.
        _await_drop(database, EDGE_COLLECTIONS + NODE_COLLECTIONS)
    for name in NODE_COLLECTIONS:
        if not database.has_collection(name):
            _create_with_retry(database, name, edge=False,
                               replication_factor=replication_factor)
    for name in EDGE_COLLECTIONS:
        if not database.has_collection(name):
            _create_with_retry(database, name, edge=True,
                               replication_factor=replication_factor)
    if database.has_graph(GRAPH_NAME):
        graph = database.graph(GRAPH_NAME)
        existing = {d["edge_collection"] for d in graph.edge_definitions()}
        for definition in EDGE_DEFINITIONS:
            if definition["edge_collection"] not in existing:
                graph.create_edge_definition(**definition)
    else:
        database.create_graph(GRAPH_NAME, edge_definitions=EDGE_DEFINITIONS)
    print(f"[build_graph] ensured {len(NODE_COLLECTIONS)} node + "
          f"{len(EDGE_COLLECTIONS)} edge collections (replicationFactor="
          f"{replication_factor}) + graph '{GRAPH_NAME}'")


def ensure_indexes(database) -> None:
    for coll, specs in INDEXES.items():
        collection = database.collection(coll)
        for fields in specs:
            collection.add_persistent_index(fields=fields, sparse=True)
    print(f"[build_graph] indexes ensured on {len(INDEXES)} collections")


def load_via_arangoimport(built: dict, threads: int = 8) -> None:
    """Load through `arangoimport` in Docker instead of batched `import_bulk`.

    Same idempotency contract — `--on-duplicate update` mirrors
    `import_bulk(on_duplicate="update")` — but it streams whole files in parallel
    rather than paying a round trip per 5,000 rows. Worth it once the continuous
    pull (W4/G12) takes the corpus past a few million documents; below that the
    Python path is simpler and fast enough.
    """
    from arangoimport_load import docker_available, import_collections

    if not docker_available():
        raise SystemExit(
            "--via-arangoimport needs Docker and the arangodb/arangodb image:\n"
            "    docker pull arangodb/arangodb:latest")
    flat = {c: list(d.values()) for c, d in built["nodes"].items()}
    # Edge spools are already JSONL on disk in exactly the form arangoimport wants.
    # Hand over the paths so the rows are not deserialised and reserialised for
    # nothing — that round trip was the whole point of spooling them.
    for coll, rows in built["edges"].items():
        if isinstance(rows, EdgeSpool):
            rows.close()
            flat[coll] = rows.path if rows.n else []
        else:
            flat[coll] = rows
    import_collections(flat, edge=set(built["edges"]), threads=threads)


def load(database, built: dict, batch: int = 5000) -> None:
    for coll, docs in built["nodes"].items():
        rows = list(docs.values())
        if not rows:
            continue
        collection = database.collection(coll)
        for i in range(0, len(rows), batch):
            collection.import_bulk(rows[i:i + batch], on_duplicate="update")
        print(f"  [nodes] {coll:<15} {len(rows):>9,}")
    for coll, rows in built["edges"].items():
        if not rows:
            continue
        collection = database.collection(coll)
        # Spools replay from disk, so batch by accumulating rather than slicing.
        buf: list[dict] = []
        for doc in rows:
            buf.append(doc)
            if len(buf) >= batch:
                collection.import_bulk(buf, on_duplicate="update")
                buf.clear()
        if buf:
            collection.import_bulk(buf, on_duplicate="update")
        print(f"  [edges] {coll:<15} {len(rows):>9,}")


def report(built: dict) -> None:
    nodes, edges, stats = built["nodes"], built["edges"], built["stats"]
    print("=== nodes ===")
    for coll, docs in nodes.items():
        if docs:
            print(f"  {coll:<15} {len(docs):>9,}")
    print("=== edges ===")
    for coll, rows in edges.items():
        if rows:
            print(f"  {coll:<15} {len(rows):>9,}")
    if stats:
        print("=== skipped / diagnostics ===")
        for k, v in sorted(stats.items()):
            print(f"  {k:<22} {v:>9,}")
    ev = nodes["Event"]
    if ev:
        with_themes = sum(1 for d in ev.values() if d["themes"])
        dates = sorted(d["dateAdded"] for d in ev.values())
        print("=== event corpus ===")
        print(f"  date range        {dates[0]} .. {dates[-1]}")
        print(f"  with themes       {with_themes:,} / {len(ev):,} "
              f"({100*with_themes/len(ev):.1f}%)")
        top = collections.Counter(
            (d["actorFromCode"], d["actorToCode"]) for d in ev.values()).most_common(5)
        print("  top dyads         " + ", ".join(f"{a}->{b}:{n:,}" for (a, b), n in top))


def legacy_edge_check(database) -> bool:
    """Refuse a no-`--drop` load while edges from before deterministic keys survive.

    Edges are now written with an explicit `_key` (see :func:`edge_key`), so a
    re-run upserts in place. Rows loaded *before* that change carry random keys
    and cannot be matched, so writing over them inserts a second full set —
    silently doubling every count and double-counting every traversal.

    A pre-migration key has no ``--`` separator, which makes the two eras cheap
    to tell apart. One `--drop` rebuild clears the condition permanently.
    """
    legacy = []
    for name in EDGE_COLLECTIONS:
        if not database.has_collection(name):
            continue
        sample = list(database.aql.execute(
            "FOR e IN @@c LIMIT 1 RETURN e._key", bind_vars={"@c": name}))
        if sample and "--" not in sample[0]:
            legacy.append((name, database.collection(name).count()))
    if not legacy:
        return True
    print("[build_graph] REFUSING to load: these edge collections predate "
          "deterministic _keys, so writing over them would duplicate rather "
          "than update:")
    for name, n in legacy:
        print(f"    {name:16s} {n:>9,} edges with random keys")
    print("  Re-run with --drop to rebuild them (one time); after that, loads "
          "are idempotent and this check passes.")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="dir holding events/gkg/prices jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="transform + report only; no ArangoDB connection")
    ap.add_argument("--drop", action="store_true", help="drop collections first")
    ap.add_argument("--limit", type=int, default=None, help="cap events (smoke test)")
    ap.add_argument("--market-only", action="store_true",
                    help="load only the market and portfolio layers from prices.jsonl "
                         "— new price or FRED series without the hours-long event load. "
                         "Event-derived nodes (Actor, Source, ...) are left untouched.")
    ap.add_argument("--via-arangoimport", action="store_true",
                    help="load through arangoimport in Docker rather than batched "
                         "import_bulk — faster past a few million documents (W4)")
    ap.add_argument("--import-threads", type=int, default=8,
                    help="arangoimport --threads (default 8)")
    ap.add_argument("--replication-factor", type=int, default=DEFAULT_REPLICATION_FACTOR,
                    help=f"replicas per shard for collections this run creates "
                         f"(default {DEFAULT_REPLICATION_FACTOR})")
    args = ap.parse_args()

    data = pathlib.Path(args.data)
    lock_path = None if args.dry_run else _single_instance_lock(data)
    # Streamed, not slurped. See JsonlSource — reading these two as lists is what
    # exhausted a 32GB machine on 2026-08-24.
    prices = load_jsonl(data / "prices.jsonl")     # ~11MB, a list is fine
    if args.market_only:
        # Empty inputs, not a separate code path: transform() then builds exactly
        # the market and portfolio layers, and every event-derived collection
        # comes out empty, which an update-on-duplicate load leaves alone.
        events, gkg = [], []
        print(f"[build_graph] market-only: {len(prices):,} price rows")
    else:
        events = JsonlSource(data / "events.jsonl", limit=args.limit)
        gkg = JsonlSource(data / "gkg.jsonl")
        print(f"[build_graph] input: {len(events):,} events, {len(gkg):,} gkg, "
              f"{len(prices):,} price rows")
    if not events and not args.market_only:
        print("  no events — run scripts/gdelt_http_pull.py (or bigquery_pull.py) first")
        return 1

    # The edge spools live here and must outlive transform(): the loaders read
    # them. --dry-run keeps edges in memory since nothing is written anyway.
    with tempfile.TemporaryDirectory(prefix="gdelt-build-") as tmp:
        spool = None if args.dry_run else pathlib.Path(tmp)
        built = transform(events, gkg, prices, spool_dir=spool)
        report(built)

        if args.dry_run:
            print("[build_graph] dry run — nothing written")
            return 0

        from _common import db

        database = db()
        if not args.drop and not legacy_edge_check(database):
            return 1
        ensure_schema(database, drop=args.drop,
                      replication_factor=args.replication_factor)
        if args.via_arangoimport and not args.market_only:
            load_via_arangoimport(built, threads=args.import_threads)
        else:
            load(database, built)
        ensure_indexes(database)
    if lock_path:
        lock_path.unlink(missing_ok=True)
    print("[build_graph] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
