#!/usr/bin/env python3
"""Install the Graph Visualizer theme for `gdelt_market` (colors + icons + rules).

How the theme is installed, and why:
  * theme lives in `_graphThemeStore` in the TARGET db (managed/cloud deployment),
    upserted by (graphId, name), `createdAt` preserved, `replace(check_rev=False)`;
  * installed with `isDefault: false` — a default theme cannot be saved/edited in
    the UI, and the built-in "Default" must stay the only `isDefault: true`;
  * icons are `mdi:` only. `fa6-solid:` silently fails to resolve on this
    Visualizer and the whole rule config is dropped (nodes fall back to a
    generic glyph), which looks like the theme "didn't install".

COLOUR CARRIES THE CAUSAL CHAIN. The palette is layered on position in the
pipeline, so a demo subgraph reads left-to-right by hue:

    news/observed (slate)  ->  semantic (violet)  ->  market (teal)  ->  portfolio (amber)
    Event/Actor/Location       Theme                  Sector/Index/       Portfolio/Holding
                                                      Commodity/Company

RULES ENCODE CONFIDENCE, NOT JUST CATEGORY. Every threshold
below was measured against the loaded graph, not guessed:
  * Event.quadClass    1:90,785  2:20,322  3:32,238  4:25,032 — all four fire.
  * correlatesWith |tStat| >= 2 -> 226 of 1,088 edges (109 pos / 117 neg).
    That cut reproduces the stored `lowConfidence` flag exactly (226 false /
    862 true), so significant edges are coloured and the other 862 stay grey.
    A grey edge is the honest default here: it says "not distinguishable from
    baseline", which is precisely the confidence caveat made visible on the canvas.
  * affects.corr spans -0.066 .. +0.590; only Brent->Energy clears 0.40.

Run:  python scripts/install_visualizer.py [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from datetime import datetime, timezone

from _common import GRAPH_NAME, db

THEME_NAME = "gdelt_causal_impact"

# Fixed namespace so re-runs regenerate identical rule ids (the Visualizer keys
# rules by `id`; fresh uuid4s on every run would churn the stored document).
_RULE_NS = uuid.UUID("9f3c1d2e-7b4a-5c68-9e10-2d4f6a8b0c31")


def _rule_id(*parts: str) -> str:
    return str(uuid.uuid5(_RULE_NS, "|".join(parts)))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
NEWS = "#4a6fa5"      # slate blue   — observed world (also = quadClass 1, the 54% majority)
NEWS_MID = "#5b7fb8"
NEWS_LIGHT = "#7d97c4"
NEWS_PALE = "#9db3d4"
SEMANTIC = "#7c5cbf"  # violet       — themes: the bridge from news to markets
MARKET_DEEP = "#1f7a70"   # teal
MARKET = "#2f8f83"
MARKET_MID = "#3aa396"
MARKET_LIGHT = "#46b3a4"
MARKET_PALE = "#5cc4b6"
PORTFOLIO = "#c9902c"     # amber    — the client's stake
PORTFOLIO_MID = "#dba743"
PORTFOLIO_LIGHT = "#e8c374"

POS = "#2e8b57"       # significant positive move / strong correlation
NEG = "#c0392b"       # significant negative move
WARN = "#e07b39"      # verbal conflict
NEUTRAL = "#b8c0cc"   # not statistically distinguishable — the honest default
EDGE_FAINT = "#d3d9e2"
LABEL = "#1d2531"


def node_rule(attr: str, attr_type: str, op: str, value, color: str,
              icon: str | None = None, *, tag: str) -> dict:
    """One attribute-based node rule in the nested schema the Visualizer expects.

    Flat `{"name", "condition": "<expr>"}` rules render as BLACK nodes and show
    blank in the Attribute-based editor.
    """
    return {
        "id": _rule_id("node", tag, attr, op, str(value)),
        "attributePath": attr,
        "attributeType": attr_type,
        "conditionType": "singleValue",
        "condition": {
            "op": op,
            "right": {"type": "literal", "value": value},
            "config": {
                "background": {"color": color, "iconName": icon or "mdi:table"},
                "labelAttribute": "",
                "hoverInfoAttributes": [],
                "rules": [],
            },
            "enabledFields": {
                "color": True,
                "icon": icon is not None,
                "labelAttribute": False,
                "hoverInfoAttributes": False,
            },
        },
    }


def edge_rule(attr: str, attr_type: str, op: str, value, color: str,
              thickness: float, *, tag: str) -> dict:
    """Edge counterpart — `lineStyle` in config instead of `background`."""
    return {
        "id": _rule_id("edge", tag, attr, op, str(value)),
        "attributePath": attr,
        "attributeType": attr_type,
        "conditionType": "singleValue",
        "condition": {
            "op": op,
            "right": {"type": "literal", "value": value},
            "config": {
                "lineStyle": {"color": color, "thickness": thickness},
                "labelAttribute": "",
                "hoverInfoAttributes": [],
                "rules": [],
            },
            "enabledFields": {
                "color": True,
                "icon": False,
                "labelAttribute": False,
                "hoverInfoAttributes": False,
            },
        },
    }


# --------------------------------------------------------------------------
# node styling — labelAttribute values verified against real documents
# --------------------------------------------------------------------------
# Event has no `name`; `date` is NOT usable as a label because 14,370 events
# carry a GDELT SQLDATE artifact dating them to 1920 (articles referencing
# history, bucketed into daily files by DATEADDED). `dateAdded` is clean
# (2018-02-27 .. 2022-03-10) and is what the event study keys off anyway.
NODE_CONFIG: dict[str, dict] = {
    "Event": {
        "background": {"color": NEWS, "iconName": "mdi:newspaper-variant-outline"},
        "labelAttribute": "dateAdded",
        "hoverInfoAttributes": [
            "dateAdded", "cameo", "quadClass", "goldstein", "avgTone",
            "numMentions", "actorFromCode", "actorToCode", "geoName",
        ],
        # CAMEO QuadClass — escalation legible at a glance. quadClass 1
        # (verbal cooperation, 54% of the corpus) intentionally has no rule and
        # falls through to the neutral slate base.
        "rules": [
            node_rule("quadClass", "number", "=", 4, NEG, "mdi:fire", tag="event_qc"),
            node_rule("quadClass", "number", "=", 3, WARN, "mdi:bullhorn", tag="event_qc"),
            node_rule("quadClass", "number", "=", 2, POS, "mdi:handshake", tag="event_qc"),
        ],
    },
    "Actor": {
        "background": {"color": NEWS_MID, "iconName": "mdi:account-group"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "code", "type", "country", "mentionCount", "aliasCount"],
        "rules": [],
    },
    "Location": {
        "background": {"color": NEWS_LIGHT, "iconName": "mdi:map-marker"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "countryCode", "adm1"],
        "rules": [],
    },
    "Source": {  # pruned — not in the named graph today; kept as a seam
        "background": {"color": NEWS_PALE, "iconName": "mdi:web"},
        "labelAttribute": "domain",
        "hoverInfoAttributes": ["domain"],
        "rules": [],
    },
    "Theme": {
        "background": {"color": SEMANTIC, "iconName": "mdi:tag-multiple"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "group"],
        "rules": [],
    },
    "Sector": {
        "background": {"color": MARKET, "iconName": "mdi:domain"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "proxySymbol"],
        "rules": [],
    },
    "Index": {
        "background": {"color": MARKET_MID, "iconName": "mdi:chart-line"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["symbol", "name", "isSectorProxy", "sector"],
        "rules": [],
    },
    "Commodity": {
        "background": {"color": MARKET_DEEP, "iconName": "mdi:barrel"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["symbol", "name"],
        "rules": [],
    },
    "Company": {
        "background": {"color": MARKET_LIGHT, "iconName": "mdi:office-building"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["ticker", "name", "sector"],
        "rules": [],
    },
    "MacroVariable": {
        # Verified 2026-08-12 once the FRED key landed: 4 series (CPI, UST10Y,
        # FedFunds, Breakeven10Y) carrying `seriesId` + `name`. The earlier
        # `series`/`source` hover fields were a guess against an empty collection
        # and do not exist.
        "background": {"color": MARKET_PALE, "iconName": "mdi:percent"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "seriesId"],
        "rules": [],
    },
    "Portfolio": {
        "background": {"color": PORTFOLIO, "iconName": "mdi:briefcase"},
        "labelAttribute": "name",
        "hoverInfoAttributes": ["name", "client"],
        "rules": [],
    },
    "Holding": {
        "background": {"color": PORTFOLIO_MID, "iconName": "mdi:wallet"},
        "labelAttribute": "ticker",   # no `name` field on Holding
        "hoverInfoAttributes": ["ticker", "weight"],
        "rules": [],
    },
    "Exposure": {  # pruned — not in the named graph today
        "background": {"color": PORTFOLIO_LIGHT, "iconName": "mdi:chart-donut"},
        "labelAttribute": "sector",
        "hoverInfoAttributes": ["sector", "weight", "portfolio"],
        "rules": [],
    },
}

# --------------------------------------------------------------------------
# edge styling
# --------------------------------------------------------------------------
# Structural/temporal edges are deliberately faint and unlabelled — `mentions`
# alone is 336,754 edges and labelling them makes the canvas unreadable. The
# two INFERRED edge classes (correlatesWith / affects) are the ones that carry
# the analytics, so they get the labels, the weight, and the rules.
EDGE_CONFIG: dict[str, dict] = {
    "mentions": {
        "lineStyle": {"color": EDGE_FAINT, "thickness": 0.6},
        "labelAttribute": "",
        "hoverInfoAttributes": ["role"],
        "rules": [],
    },
    "taggedWith": {
        "lineStyle": {"color": SEMANTIC, "thickness": 0.9},
        "labelAttribute": "",
        "hoverInfoAttributes": ["articleCount"],
        "rules": [],
    },
    "locatedIn": {
        "lineStyle": {"color": NEWS_LIGHT, "thickness": 0.6},
        "labelAttribute": "",
        "hoverInfoAttributes": [],
        "rules": [],
    },
    "partOf": {
        "lineStyle": {"color": MARKET_LIGHT, "thickness": 1.0},
        "labelAttribute": "",
        "hoverInfoAttributes": [],
        "rules": [],
    },
    "holds": {
        "lineStyle": {"color": PORTFOLIO, "thickness": 1.4},
        "labelAttribute": "weight",
        "hoverInfoAttributes": ["weight"],
        "rules": [],
    },
    "precedes": {
        # Temporal spine. Lag ceiling is 400d because the corpus is
        # episode-clustered; long inter-episode hops recede, tight chains pop.
        "lineStyle": {"color": EDGE_FAINT, "thickness": 0.8},
        "labelAttribute": "lagDays",
        "hoverInfoAttributes": ["dyad", "lagDays"],
        "rules": [
            edge_rule("lagDays", "number", "<=", 7, NEWS, 1.6, tag="precedes_lag"),
        ],
    },
    "coOccursWith": {
        "lineStyle": {"color": EDGE_FAINT, "thickness": 0.6},
        "labelAttribute": "",
        "hoverInfoAttributes": ["date", "dyad", "window"],
        "rules": [],
    },
    "similarTo": {
        # Never materialized by design — analog retrieval runs at query
        # time against the `event_analog` view. Styled so a future cache is
        # visually distinct if it ever lands.
        "lineStyle": {"color": SEMANTIC, "thickness": 1.2},
        "labelAttribute": "",
        "hoverInfoAttributes": ["score"],
        "rules": [],
    },
    "correlatesWith": {
        # Base = grey: "not distinguishable from baseline" (862 of 1,088 edges).
        # Only |tStat| >= 2 earns colour and weight.
        "lineStyle": {"color": NEUTRAL, "thickness": 0.8},
        "labelAttribute": "move",
        "hoverInfoAttributes": [
            "move", "horizonDays", "consistency", "tStat", "n",
            "lowConfidence", "proxySymbol", "method",
        ],
        "rules": [
            edge_rule("tStat", "number", ">=", 2, POS, 2.2, tag="corr_sig"),
            edge_rule("tStat", "number", "<=", -2, NEG, 2.2, tag="corr_sig"),
        ],
    },
    "affects": {
        "lineStyle": {"color": NEUTRAL, "thickness": 0.8},
        "labelAttribute": "corr",
        "hoverInfoAttributes": ["corr", "n", "method"],
        # Ordered widest-last: 0.40 (Brent->Energy alone) then 0.20 then
        # negative. First match wins, so the tighter bound must come first.
        "rules": [
            edge_rule("corr", "number", ">=", 0.40, MARKET_DEEP, 2.4, tag="affects_corr"),
            edge_rule("corr", "number", ">=", 0.20, MARKET_LIGHT, 1.6, tag="affects_corr"),
            edge_rule("corr", "number", "<", 0, NEG, 1.6, tag="affects_corr"),
        ],
    },
}


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------
def ensure_collection(database, name: str, edge: bool = False) -> None:
    if not database.has_collection(name):
        database.create_collection(name, edge=edge, system=name.startswith("_"))


def graph_schema(database, graph_name: str) -> tuple[set[str], set[str]]:
    """(vertex_colls, edge_colls) from the SDK.

    `g.edge_definitions()` returns dicts keyed "edge_collection"; querying
    `_graphs` via AQL returns "collection". Never mix the two.
    """
    g = database.graph(graph_name)
    return set(g.vertex_collections()), {ed["edge_collection"] for ed in g.edge_definitions()}


def prune(config: dict, keep: set[str]) -> dict:
    return {k: copy.deepcopy(v) for k, v in config.items() if k in keep}


def ensure_visualizer_shape(theme: dict) -> None:
    for cfg in theme["nodeConfigMap"].values():
        cfg.setdefault("rules", [])
        cfg.setdefault("hoverInfoAttributes", [])
    for cfg in theme["edgeConfigMap"].values():
        cfg.setdefault("rules", [])
        cfg.setdefault("hoverInfoAttributes", [])
        cfg.setdefault("arrowStyle", {"sourceArrowShape": "none", "targetArrowShape": "triangle"})
        cfg.setdefault("labelStyle", {"color": LABEL})


def build_theme(vertex_colls: set[str], edge_colls: set[str]) -> dict:
    theme = {
        "name": THEME_NAME,
        "graphId": GRAPH_NAME,
        "description": (
            "Causal/what-if theme. Hue tracks the causal chain "
            "(news -> theme -> market -> portfolio); correlatesWith/affects are "
            "coloured only where the event study is significant (|tStat| >= 2), "
            "grey otherwise."
        ),
        # MUST stay false: a default theme cannot be saved/edited in the UI, and
        # the built-in "Default" is already the sole isDefault:true for this graph.
        "isDefault": False,
        "nodeConfigMap": prune(NODE_CONFIG, vertex_colls),
        "edgeConfigMap": prune(EDGE_CONFIG, edge_colls),
    }
    ensure_visualizer_shape(theme)
    return theme


def install(database, theme: dict) -> str:
    ensure_collection(database, "_graphThemeStore")
    col = database.collection("_graphThemeStore")
    ts = now_iso()
    theme = {**theme, "updatedAt": ts}

    existing = list(col.find({"graphId": theme["graphId"], "name": theme["name"]}))
    if existing:
        theme["_key"] = existing[0]["_key"]
        theme["createdAt"] = existing[0].get("createdAt", ts)
        col.replace(theme, check_rev=False)
        # A prior run may have created duplicates before keys were stable.
        for dup in existing[1:]:
            col.delete(dup["_key"])
        return f"_graphThemeStore/{theme['_key']} (updated)"

    theme["createdAt"] = ts
    return f"{col.insert(theme)['_id']} (created)"


def check_default_sanity(database) -> list[str]:
    """Exactly one isDefault:true per graph, and it must not be ours."""
    col = database.collection("_graphThemeStore")
    defaults = [t for t in col.find({"graphId": GRAPH_NAME}) if t.get("isDefault")]
    warnings = []
    if len(defaults) != 1:
        warnings.append(
            f"expected exactly 1 isDefault:true theme for {GRAPH_NAME}, found {len(defaults)}: "
            f"{[t.get('name') for t in defaults]}"
        )
    elif defaults[0].get("name") == THEME_NAME:
        warnings.append(
            f"'{THEME_NAME}' is marked isDefault:true — it will not be editable in the UI"
        )
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the theme document instead of writing it")
    args = ap.parse_args()

    database = db()
    vertex_colls, edge_colls = graph_schema(database, GRAPH_NAME)
    theme = build_theme(vertex_colls, edge_colls)

    dropped = (set(NODE_CONFIG) | set(EDGE_CONFIG)) - vertex_colls - edge_colls
    n_rules = sum(len(c["rules"]) for c in theme["nodeConfigMap"].values()) + \
        sum(len(c["rules"]) for c in theme["edgeConfigMap"].values())

    if args.dry_run:
        print(json.dumps(theme, indent=2))
    else:
        print(f"✓ {install(database, theme)}")

    print(f"  graph={GRAPH_NAME} theme={THEME_NAME} isDefault=False")
    print(f"  {len(theme['nodeConfigMap'])} node configs, "
          f"{len(theme['edgeConfigMap'])} edge configs, {n_rules} rules")
    if dropped:
        print(f"  pruned (not in graph): {', '.join(sorted(dropped))}")

    if not args.dry_run:
        for w in check_default_sanity(database):
            print(f"  ! {w}")
        print("  Open the graph in the Visualizer → Legend → select "
              f"'{THEME_NAME}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
