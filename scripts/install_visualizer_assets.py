"""Install Graph Visualizer saved queries + canvas actions for the tariff demo.

Companion to install_visualizer.py (which installs the `gdelt_causal_impact`
theme). Where each asset lives:

  * `_queries`              -> Graph Visualizer "Queries" panel. Field: queryText.
                              Linked to a viewpoint via `_viewpointQueries`.
                              These LOAD GRAPH DATA, so every one returns paths or
                              edges — never a vertex, never an aggregate.
  * `_editor_saved_queries` -> the AQL editor sidebar. Fields: content AND value.
                              This is where the tabular/aggregate queries live.
  * `_canvasActions`        -> right-click menu. Field: queryText, bind var @nodes.
                              Linked via `_viewpointActions`.
  * managed/cloud deployment => everything goes in the TARGET db.

Every panel query is written with hard-coded demo defaults and
`bindVariables: {}` so a click just works on the call — nobody types bind vars
in front of an audience.

`WITH` is mandatory on all of these on a cluster deployment: the
traversals reach vertex collections the traversal loop does not name, which fails
at EXECUTION (error 1521), not at parse time.

  python scripts/install_visualizer_assets.py            # install
  python scripts/install_visualizer_assets.py --dry-run  # print, write nothing
  python scripts/install_visualizer_assets.py --test     # run every query, no writes
"""
from __future__ import annotations

import argparse
import re
import sys

from _common import db
from install_visualizer import GRAPH_NAME, ensure_collection, graph_schema, now_iso

# Demo day, chosen by measurement across all 16 E1 episode dates (2026-08-20).
# The criterion is what the top-6-by-mentions actually RENDERS as, since that is
# what panel query 1 puts on the canvas — not what happened in the world that day.
#
# 2019-12-13 (Phase One announced) wins on every axis: 6/6 of the top events are
# trade-tagged, 6/6 are on the USA<->CHN dyad, and the single most-mentioned event
# of the day (1,818 mentions) is cameo 163 / quadClass 4 / Goldstein -8 — which is
# EXACTLY the action step 2 then retrieves analogs for. Step 1 and step 2 finally
# share a visual thread.
#
# The previous choice, 2018-03-22 (Section 301 memo), is iconic in the world and
# poor on screen: its top 6 are 0/6 conflictual with mean Goldstein +6.9 — uniform
# green cooperation icons on the day the trade war started. It ranked 8th of 16.
DEMO_DATE = "2019-12-13"
DEMO_CAMEO = "163"          # impose embargo / restrict
DEMO_FROM, DEMO_TO = "USA", "CHN"
CONFIDENT = 2.0             # |tStat| threshold, matches the theme's colour rule
# Only 4 of 11 sectors have portfolio exposure (IT, Energy, Financials, Industrials).
# Probe one that does, or the portfolio action tests EMPTY and hides a real regression.
DEMO_SECTOR = "Information_Technology"

# E2 / oil. The demo's second half, and the half that carries a real result: the
# tariff question has no detectable sector effect, these do.
#
# SHOCK_DATE is the demo's climax and it is deliberately not Abqaiq. Saudi Arabia
# and Russia failed to agree output cuts on Friday 2020-03-06; Riyadh cut its
# official selling prices over the weekend and the market gapped on the Monday.
# WTI -24.59%, Brent -24.10% (z = -8.5), Energy -12.54% net of the market (z = -7.3)
# — the largest single-day move in the corpus by a wide margin.
#
# It also could not be retrieved at all until 2026-08-23. `--dyad-only` was
# hub-and-spoke, so every event needed the US on one side and the SAU<->RUS pair
# returned nothing. Widening the filter is what put this event in reach, which is
# worth saying out loud: the demo's strongest result was invisible to the previous
# corpus, not absent from the world.
#
# Windows anchor on `PriceSeries.prev` — the close STRICTLY BEFORE the event — so
# the event day's own reaction is inside the window. Both of these landed on
# weekends, so the 1-day figure is Friday close to Monday close.
SHOCK_DATE = "2020-03-09"        # Saudi-Russia price war, first session after
SHOCK_ACTORS = ("SAU", "RUS")
OIL_DATE = "2019-09-14"          # Abqaiq/Khurais attack
OIL_SYMBOL = "CL=F"              # WTI


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def with_clause(vertex_colls: set[str], edge_colls: set[str]) -> str:
    return "WITH " + ", ".join(sorted(vertex_colls | edge_colls))


# --------------------------------------------------------------------------
# Visualizer "Queries" panel — graph-loading, ordered as a walkthrough
# --------------------------------------------------------------------------
def analog_subquery(limit: int) -> str:
    """The E1 analog set, one event per date.

    Ranking purely by `numMentions` collapses the retrieval onto whatever days
    generated the most coverage: measured 2026-08-21 on the corpus of the day, the
    top 8 of 167 matching events landed on just 4 of the 55 available dates,
    because volume is wildly concentrated (1,818 mentions on the top event, 78 on
    the eighth). The corpus now holds 3,390 matching events across 785 dates and
    the concentration is unchanged. Taking the best-supported event *per date*
    — the whole E1 window rather than two episodes.

    The tail is lightly covered by construction (the 8th pick has ~20 mentions
    against the leader's 1,818). That is the honest trade and `numMentions` stays
    on every node so the thinness is inspectable rather than hidden.

    Stage 2 replaces this with the faceted scorer over `event_analog`
    (`ANALOG_AQL` in build_inferred_edges.py), keeping date diversity as a
    constraint on top of the score.
    """
    return f"""FOR e IN Event
      FILTER e.cameo == "{DEMO_CAMEO}"
      FILTER e.actorFromCode == "{DEMO_FROM}" AND e.actorToCode == "{DEMO_TO}"
      COLLECT date = e.dateAdded INTO g
      LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC RETURN x)
      SORT best.numMentions DESC
      LIMIT {limit}
      RETURN best"""


def panel_queries(w: str) -> list[dict]:
    return [
        {
            "name": "1 · Corpus — one tariff episode (2019-12-13 Phase One)",
            "description": ("One day of the trade war, drawn as a graph: the six most "
                            "heavily covered events with their actors, location and "
                            "themes. Establishes that this is real GDELT reporting, not a "
                            "fixture. The day's biggest story is a US sanctions action on "
                            "China — the same event type step 2 retrieves analogs for."),
            "query": f"""{w}
// Step 1 — the corpus is real.
FOR ev IN (
    FOR e IN Event
      FILTER e.dateAdded == "{DEMO_DATE}"
      SORT e.numMentions DESC
      LIMIT 6
      RETURN e)
  FOR v, e, p IN 1..1 ANY ev mentions, locatedIn, taggedWith
    LIMIT 150
    RETURN p""",
        },
        {
            "name": "2 · E1 analogs — historical tariff events (USA→CHN)",
            "description": ("Step 1 of the what-if: the analog set for a hypothetical "
                            "USA→CHN tariff action (CAMEO 163), with the themes each "
                            "analog carries. One event per date, so the eight span eight "
                            "distinct dates across the whole E1 window rather than "
                            "clustering on the two biggest news days."),
            "query": f"""{w}
// Step 2 — hypothetical -> historical analogs.
FOR ev IN (
    {analog_subquery(8)})
  FOR theme, tagged, p IN 1..1 OUTBOUND ev taggedWith
    RETURN p""",
        },
        {
            "name": "3 · E1 causal chain — analog → theme → sector (5-day)",
            "description": ("The audit trail: complete two-hop paths from each historical "
                            "analog, through the themes it was reported under, to the sectors "
                            "those themes historically moved. Every number in the answer traces "
                            "back along one of these lines — which is what made it testable. Pinned to the 5-day "
                            "horizon — the study computes four (1/3/5/10d) and showing all of "
                            "them quadruples the canvas into noise. NOTE: the sector figures these paths lead to do not survive a larger sample — run the sweep query before quoting any of them."),
            "query": f"""{w}
// Step 3 — the audit trail, not the answer.
// ONE 2-hop traversal, not two nested 1-hop ones: `p` must contain BOTH edges or
// the Visualizer renders Theme->Sector without the Event->Theme hop that explains it.
// correlatesWith hangs off Theme, never off Event — going straight OUTBOUND from an
// Event returns nothing at all.
FOR ev IN (
    {analog_subquery(2)})
  FOR asset, corr, p IN 2..2 OUTBOUND ev taggedWith, correlatesWith
    FILTER IS_SAME_COLLECTION("Sector", asset)
    FILTER corr.horizonDays == 5
    LIMIT 400
    RETURN p""",
        },
        {
            "name": f"4 · E1 causal chain — confident only (|tStat| ≥ {CONFIDENT:.0f}, 5-day)",
            "description": ("The same chain restricted to edges clearing |tStat| >= 2 — ~37 "
                            "paths against query 3's ~121. Run them back to back: the edges "
                            "that vanish ARE the confidence caveat, shown rather "
                            "than narrated."),
            "query": f"""{w}
// Step 3, honest variant — only statistically defensible edges.
FOR ev IN (
    {analog_subquery(2)})
  FOR asset, corr, p IN 2..2 OUTBOUND ev taggedWith, correlatesWith
    FILTER IS_SAME_COLLECTION("Sector", asset)
    FILTER corr.horizonDays == 5 AND ABS(corr.tStat) >= {CONFIDENT}
    LIMIT 400
    RETURN p""",
        },
        {
            "name": "5 · Portfolio overlay — sector → holdings → portfolio",
            "description": ("Turns a market-wide result into \"what does this mean for me\": "
                            "backwards from the sectors the what-if "
                            "moved, back down through Company/Index to the demo "
                            "portfolio's actual holdings. This is the step the event "
                            "graph alone cannot do. NOTE: shows the 5 company holdings "
                            "only — the ETF sleeve (XLF/XLE/XLK, 37% of weight) has no "
                            "Sector<->ETF edge to traverse by design (docs/schema.md keeps "
                            "the proxy as a field), so use the 'Portfolio exposure' editor "
                            "tables for whole-book coverage."),
            "query": f"""{w}
// Step 5 — market-wide effect -> this portfolio's exposure.
// holds: Portfolio->Holding->Company/Index ; partOf: Company->Sector, Sector->Index
FOR sector IN Sector
  FOR v, e, p IN 1..3 INBOUND sector partOf, holds
    FILTER IS_SAME_COLLECTION("Portfolio", v) OR IS_SAME_COLLECTION("Holding", v)
       OR IS_SAME_COLLECTION("Company", v)
    LIMIT 200
    RETURN p""",
        },
        {
            "name": "6 · Second-order — commodity / macro → sector (affects)",
            "description": ("The propagation leg: oil and the daily macro series (Treasury "
                            "yields, 10y breakeven, the dollar) into sectors. The macro "
                            "half exists only when a FRED key is set."),
            "query": f"""{w}
// The rates/inflation channel a tariff question actually implies.
FOR src IN UNION(
      (FOR c IN Commodity RETURN c),
      (FOR m IN MacroVariable RETURN m))
  FOR v, e, p IN 1..1 OUTBOUND src affects
    LIMIT 150
    RETURN p""",
        },
        {
            "name": "7 · Temporal spine — precedes chain across episodes",
            "description": ("Shows time. The day-anchor spine (one representative "
                            "event per country-pair per day, chained to the next day's) with "
                            "each link carrying the days elapsed. Makes visible that the corpus "
                            "is a set of clustered episodes, not an even sweep of dates."),
            "query": f"""{w}
// The day-anchor spine — deliberately NOT a clique.
FOR ev IN (
    FOR e IN Event
      FILTER e.isDayAnchor == true
      SORT e.dateAdded
      LIMIT 40
      RETURN e)
  FOR v, e, p IN 1..1 OUTBOUND ev precedes
    LIMIT 120
    RETURN p""",
        },
        {
            "name": "8 · Oil shock — Saudi-Russia price war (2020-03-09)",
            "description": ("The counterpart to step 1, for the question that DOES have "
                            "an answer, and the largest single-day move in the corpus. "
                            "OPEC+ broke down on the Friday; Riyadh cut prices over the "
                            "weekend. WTI -24.59%%, Brent -24.10%% (z = -8.5). Both sides "
                            "of this dyad are non-US, so it was unreachable until the "
                            "hub-and-spoke dyad filter was fixed."),
            "query": f"""{w}
// The demo's climax: a supply shock between two producers, neither of them the US.
FOR ev IN (
    FOR e IN Event
      FILTER e.dateAdded == "{SHOCK_DATE}"
      FILTER e.actorFromCode IN {list(SHOCK_ACTORS)} AND e.actorToCode IN {list(SHOCK_ACTORS)}
      FILTER e.actorFromCode != e.actorToCode
      SORT e.numMentions DESC
      LIMIT 6
      RETURN e)
  FOR v, e, p IN 1..1 ANY ev mentions, locatedIn, taggedWith
    LIMIT 150
    RETURN p""",
        },
        {
            "name": "9 · Oil shock — the Abqaiq day (2019-09-14)",
            "description": ("The second witness, so the result does not rest on one day. "
                            "A drone strike removes ~5%% of world oil supply on a Saturday; "
                            "markets reopened Monday with WTI up 14.68%% (z = +4.1). A "
                            "different dyad, a different mechanism, the same machinery."),
            "query": f"""{w}
FOR ev IN (
    FOR e IN Event
      FILTER e.dateAdded == "{OIL_DATE}"
      SORT e.numMentions DESC
      LIMIT 6
      RETURN e)
  FOR v, e, p IN 1..1 ANY ev mentions, locatedIn, taggedWith
    LIMIT 150
    RETURN p""",
        },
    ]


# --------------------------------------------------------------------------
# AQL editor saved queries — tabular / aggregate (never graph-loading)
# --------------------------------------------------------------------------
def editor_queries() -> list[dict]:
    return [
        {
            "name": "GDELT · Corpus counts by collection",
            "description": ("One row per collection with live document counts — the "
                            "\"this is real, not a fixture\" number."),
            "query": """/* Corpus scale, straight from the live graph. */
FOR c IN [
    "Event","Actor","Location","Theme","Source","Sector","Index","Commodity",
    "MacroVariable","Company","Portfolio","Holding","Exposure",
    "mentions","taggedWith","locatedIn","partOf","holds",
    "precedes","coOccursWith","similarTo","correlatesWith","affects"]
  RETURN { collection: c, documents: COLLECTION_COUNT(c) }""",
        },
        {
            "name": "GDELT · E1 sector impact distribution (ranked by t-stat)",
            "description": ("SUPERSEDED as an answer — kept because the demo shows it being "
                            "retired. The theme-level ranked table: per sector "
                            "the expected move, its range, average t-statistic, consistency, "
                            "typical horizon and how many edges support it. Ranked by "
                            "t-statistic rather than raw percent, so a highly volatile "
                            "instrument cannot top the list purely by being volatile, with "
                            "the confidence measure surfaced instead of dropped. Every figure here is a "
                            "small-sample artifact; the sweep query shows why."),
            "query": f"""/* E1 what-if: historical outcome distribution, NOT a forecast. */
WITH Event, Theme, Sector
FOR ev IN (
    {analog_subquery(8)})
  FOR theme, tagged IN 1..1 OUTBOUND ev taggedWith
    FOR sector, corr IN 1..1 OUTBOUND theme correlatesWith
      FILTER IS_SAME_COLLECTION("Sector", sector)
      COLLECT name = sector.name INTO hits = {{
        move: corr.move, tStat: corr.tStat, consistency: corr.consistency,
        horizon: corr.horizonDays, n: corr.n, weak: corr.lowConfidence,
        analog: ev._key, theme: theme.name }}
      LET moves  = (FOR h IN hits FILTER h.move  != null RETURN h.move)
      LET tstats = (FOR h IN hits FILTER h.tStat != null RETURN h.tStat)
      LET confident = LENGTH(FOR h IN hits FILTER h.weak != true RETURN 1)
      SORT ABS(AVERAGE(tstats)) DESC
      RETURN {{
        sector: name,
        expectedMove: AVERAGE(moves),
        range: [MIN(moves), MAX(moves)],
        avgTStat: AVERAGE(tstats),
        avgConsistency: AVERAGE(hits[*].consistency),
        typicalHorizonDays: AVERAGE(hits[*].horizon),
        edges: LENGTH(hits),
        confidentEdges: confident,
        thin: confident == 0,
        analogs: UNIQUE(hits[*].analog),
        themes: UNIQUE(hits[*].theme) }}""",
        },
        {
            "name": "GDELT · E1 sector impact — ANALOG-CONDITIONAL",
            "description": ('The same question answered from the analogs themselves rather than from pre-computed theme averages. Ask a different question and this table changes — the older distribution query cannot, because it reads theme-level edges identical for every hypothetical. That is what made the answer testable, and the test failed: run the sweep query next. These figures are real arithmetic over real events and still do not survive a larger sample.'),
            "query": '/* E1 what-if — outcome computed over the RETRIEVED ANALOGS.\n *\n * The older "sector impact distribution" query reads pre-computed Theme->Sector\n * `correlatesWith` edges. Those are a theme-level summary: every theme reaches\n * every sector, so that answer is identical whichever analogs come back and the\n * cited events are decorative. This one computes the move from the\n * price series at the analog dates themselves, so changing the question changes\n * the answer.\n *\n * Two subtleties, both found by measurement:\n *   - `PriceSeries.pos` maps EVERY calendar date to the last trading day on or\n *     before it. Events happen at weekends; an exact lookup silently dropped 2 of\n *     8 E1 analogs.\n *   - Observations are then deduplicated BY RESOLVED TRADING DAY. Friday and\n *     Saturday resolve to the same close, and counting that measurement twice\n *     inflates n and shrinks the standard deviation.\n */\nLET analogs = (\n  FOR e IN Event\n    FILTER e.cameo == "163"\n    FILTER e.actorFromCode == "USA" AND e.actorToCode == "CHN"\n    COLLECT date = e.dateAdded INTO g\n    LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC RETURN x)\n    SORT best.numMentions DESC\n    LIMIT 8\n    RETURN best)\n\nFOR sector IN Sector\n  LET base = DOCUMENT("BaselineStats", CONCAT(sector.proxySymbol, "_5"))\n  LET ps   = DOCUMENT("PriceSeries", sector.proxySymbol)\n  LET bm   = DOCUMENT("PriceSeries", "_GSPC")\n  LET obs = (\n    FOR a IN analogs\n      LET i = ps.prev[a.dateAdded]\n      FILTER i != null AND i + 5 < LENGTH(ps.closes)\n      COLLECT idx = i INTO grp\n      LET anchor = ps.dates[idx]\n      LET j = bm.pos[anchor]\n      LET raw = (ps.closes[idx + 5] - ps.closes[idx]) / ps.closes[idx]\n      LET bench = (base.useAbnormal AND j != null AND j + 5 < LENGTH(bm.closes))\n                ? (bm.closes[j + 5] - bm.closes[j]) / bm.closes[j] : 0\n      RETURN { anchor, move: raw - bench, events: UNIQUE(grp[*].a._key) })\n  LET moves = obs[*].move\n  LET n  = LENGTH(moves)\n  LET sd = n > 1 ? STDDEV_SAMPLE(moves) : null\n  FILTER n > 0\n  LET excess = AVERAGE(moves) - base.mean\n  LET t = (sd == null OR sd == 0) ? null : excess / (sd / SQRT(n))\n  SORT ABS(t == null ? 0 : t) DESC\n  RETURN {\n    sector: sector.name,\n    expectedMovePct: ROUND(excess * 10000) / 100,\n    tStat: t == null ? null : ROUND(t * 100) / 100,\n    observations: n,\n    baselineMeanPct: ROUND(base.mean * 10000) / 100,\n    tradingDays: obs[*].anchor,\n    analogEvents: UNIQUE(FLATTEN(obs[*].events)) }',
        },
        {
            "name": "GDELT · Tariff what-if — does the effect survive more evidence?",
            "description": ('THE result, after widening the corpus 6x. The same tariff question asked with more and more historical examples. A real effect would get stronger; this one evaporates, and the strongest sector changes every time. Only the smallest sample looks significant. Run this before quoting any sector number for the tariff scenario.'),
            "query": '/* Does the tariff effect survive more evidence?\n *\n * One analog per 7-day block, so no two 5-day windows can share a trading day,\n * then the same question asked with progressively more of them.\n *\n * A real effect gets STRONGER as observations accumulate — the standard error\n * falls as 1/sqrt(n), so significance rises. Read the output the other way round:\n * the strongest sector keeps CHANGING and the sign keeps flipping. That is what\n * coincidence looks like.\n *\n * Verified 2026-08-23 on the 3.46M-event corpus: Health Care, Information\n * Technology, Information Technology, Health Care, Information Technology at\n * 8/12/20/40/80 analogs, t between -1.9 and +2.3. One cell of five clears |t|=2,\n * which is roughly what five draws from noise produce.\n */\nLET horizon = 5\nLET ranked = (\n  FOR e IN Event\n    FILTER e.cameo == "163" AND e.actorFromCode == "USA" AND e.actorToCode == "CHN"\n    COLLECT blk = FLOOR(DATE_DIFF("2018-01-01", e.dateAdded, "d") / 7) INTO g\n    LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC RETURN x)\n    SORT best.numMentions DESC\n    RETURN best)\n\nFOR k IN [8, 12, 20, 40, 80]\n  LET analogs = SLICE(ranked, 0, k)\n  LET perSector = (\n    FOR sector IN Sector\n      LET base = DOCUMENT("BaselineStats", CONCAT(sector.proxySymbol, "_", horizon))\n      LET ps   = DOCUMENT("PriceSeries", sector.proxySymbol)\n      LET bm   = DOCUMENT("PriceSeries", "_GSPC")\n      LET obs = (\n        FOR a IN analogs\n          LET i = ps.prev[a.dateAdded]\n          FILTER i != null AND i + horizon < LENGTH(ps.closes)\n          COLLECT idx = i INTO grp\n          LET j = bm.pos[ps.dates[idx]]\n          LET raw = (ps.closes[idx+horizon] - ps.closes[idx]) / ps.closes[idx]\n          LET bench = (base.useAbnormal AND j != null AND j+horizon < LENGTH(bm.closes))\n                    ? (bm.closes[j+horizon] - bm.closes[j]) / bm.closes[j] : 0\n          RETURN raw - bench)\n      LET n = LENGTH(obs)\n      FILTER n > 1\n      LET sd = STDDEV_SAMPLE(obs)\n      LET ex = AVERAGE(obs) - base.mean\n      LET t  = sd == 0 ? 0 : ex / (sd / SQRT(n))\n      SORT ABS(t) DESC\n      LIMIT 1\n      RETURN { s: sector.name, mv: ROUND(ex*10000)/100, t: ROUND(t*100)/100, n: n })\n  LET top = FIRST(perSector)\n  RETURN {\n    analogsUsed: top.n,\n    strongestSector: top.s,\n    movePct: top.mv,\n    tStat: top.t,\n    looksSignificant: ABS(top.t) >= 2 }',
        },
        {
            "name": "GDELT · Tone and attention over time",
            "description": ("Tone and coverage on the same graph as the what-if traversal, "
                            "for one relationship, month by month, straight off the Event "
                            "documents. Also shows "
                            "why a tone series needs context: the 2020 collapse is COVID, "
                            "not tariffs."),
            "query": "/* TONE AND ATTENTION \u2014 sentiment and coverage over time, on the same graph.\n *\n * Tone and attention for one relationship, month by month, straight off the same\n * Event documents the what-if queries traverse. No extra collection, no separate pipeline.\n *\n * `avgTone` is GDELT's own tone score for the event's coverage, roughly -10 to\n * +10; `gkgTone` is the average over the articles the GKG join actually linked,\n * which is the better number where it exists and null where no article resolved.\n *\n * Read the output before trusting the narrative. Tone drifts from -1.46 to about\n * -2.0 through 2018 as tariffs escalate, and the conflictual share of events rises\n * from 22% to 30% \u2014 that much is the trade war. The sharp fall to -3.14 in February\n * 2020 is NOT: it is COVID coverage entering the same US-China channel. A tone\n * series read without context will attribute a pandemic to a tariff, which is worth\n * demonstrating on the same slide that offers the capability.\n */\nFOR e IN Event\n  FILTER e.actorFromCode == \"USA\" AND e.actorToCode == \"CHN\"\n  FILTER e.dateAdded >= \"2018-03-01\" AND e.dateAdded <= \"2020-03-01\"\n  COLLECT month = SUBSTRING(e.dateAdded, 0, 7)\n    AGGREGATE events    = COUNT(1),\n              coverage  = SUM(e.numMentions),\n              tone      = AVERAGE(e.avgTone),\n              conflict  = SUM(e.quadClass >= 3 ? 1 : 0)\n  SORT month\n  RETURN {\n    month,\n    events,\n    coverage,\n    avgTone:      ROUND(tone * 100) / 100,\n    conflictPct:  ROUND(conflict * 1000 / events) / 10,\n    // A crude sparkline, because a column of numbers hides the shape that is the\n    // whole point of a tone-over-time view.\n    shape: CONCAT(REPEAT(\"\u2581\", FLOOR(ABS(tone))), tone < 0 ? \" negative\" : \" positive\")\n  }\n",
        },
        {
            "name": "GDELT · Class vs instance — the whole argument in one table",
            "description": ("Three class questions and three single days, measured the "
                            "same way. Every class question says no; the two genuine "
                            "shocks are unmistakable; and Section 301 measured as one day "
                            "still says no, so this is not a yes-machine. Run this if you "
                            "only have time for one query."),
            "query": "/* Class vs instance \u2014 the demo's whole argument in one table.\n *\n * Two ways to ask a question, measured the same way.\n *\n * A CLASS question (\"what do US tariff threats against China do?\") averages over\n * every matching event. An INSTANCE question (\"what did 2020-03-09 do?\") measures\n * one day against that asset's own normal range.\n *\n * The class rows report t across 80 independent analogs; the instance rows report\n * z for a single day. Different statistics, deliberately: with n=1 there is no\n * significance test, only \"is this outside the range this series produces\".\n *\n * The finding is the gap between them. No event CLASS in this corpus predicts a\n * market move \u2014 swept across every dyad, CAMEO code, horizon and target, 405,987\n * tests returned 2.61% at |t| >= 2.5, about the chance rate, and none survives correction. Specific large\n * surprises are unmistakable.\n *\n * READ THE FOURTH AND SIXTH ROWS TOGETHER. \"Yemen attacks Saudi Arabia with\n * military force\" is the class the Abqaiq strike BELONGS TO \u2014 GDELT has no actor\n * code for the Houthis and resolves them to YEM, so the 2019-09-16 attack is the\n * sixth-ranked analog the class retrieves. The class returns nothing. The single\n * event returns z = +5.1.\n *\n * The same event, asked about two ways, gives both answers. That is the whole\n * argument, and it is not a selection artefact: the shock is inside the evidence\n * set, averaged with 79 other Houthi attacks that hit nothing important.\n */\nLET horizon = 5\n\nLET classRows = (\n  FOR spec IN [\n      { a: \"USA\", b: \"CHN\", c: \"163\", label: \"US tariff threat -> China\" },\n      { a: \"RUS\", b: \"USA\", c: \"130\", label: \"Russia threatens the US\" },\n      { a: \"IRN\", b: \"SAU\", c: \"190\", label: \"Iran uses force -> Saudi\" },\n      { a: \"YEM\", b: \"SAU\", c: \"190\", label: \"Yemen attacks Saudi (CONTAINS Abqaiq)\" }]\n    LET analogs = (\n      FOR e IN Event\n        FILTER e.cameo == spec.c\n        FILTER e.actorFromCode == spec.a AND e.actorToCode == spec.b\n        COLLECT blk = FLOOR(DATE_DIFF(\"2018-01-01\", e.dateAdded, \"d\") / 7) INTO g\n        LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC RETURN x)\n        SORT best.numMentions DESC\n        LIMIT 80\n        RETURN best)\n    LET perTarget = (\n      FOR sector IN Sector\n        LET base = DOCUMENT(\"BaselineStats\", CONCAT(sector.proxySymbol, \"_\", horizon))\n        LET ps   = DOCUMENT(\"PriceSeries\", sector.proxySymbol)\n        LET bm   = DOCUMENT(\"PriceSeries\", \"_GSPC\")\n        LET obs = (\n          FOR x IN analogs\n            LET i = ps.prev[x.dateAdded]\n            FILTER i != null AND i + horizon < LENGTH(ps.closes)\n            FILTER ps.closes[i] > 0 AND ps.closes[i + horizon] > 0\n            COLLECT idx = i\n            LET j = bm.pos[ps.dates[idx]]\n            LET raw = (ps.closes[idx+horizon] - ps.closes[idx]) / ps.closes[idx]\n            LET bench = (base.useAbnormal AND j != null AND j+horizon < LENGTH(bm.closes) AND bm.closes[j] > 0)\n                      ? (bm.closes[j+horizon] - bm.closes[j]) / bm.closes[j] : 0\n            RETURN raw - bench)\n        LET n = LENGTH(obs)\n        FILTER n > 1\n        LET sd = STDDEV_SAMPLE(obs)\n        LET ex = AVERAGE(obs) - base.mean\n        LET t  = sd == 0 ? 0 : ex / (sd / SQRT(n))\n        SORT ABS(t) DESC\n        LIMIT 1\n        RETURN { target: sector.name, mv: ex, stat: t, n: n })\n    LET top = FIRST(perTarget)\n    FILTER top != null\n    RETURN { ask: \"class \u2014 averaged over analogs\", question: spec.label,\n             strongest: top.target, movePct: ROUND(top.mv*10000)/100,\n             stat: ROUND(top.stat*100)/100, statIs: \"t\", observations: top.n,\n             verdict: ABS(top.stat) >= 3 ? \"detectable\" : \"no\" })\n\nLET instanceRows = (\n  FOR spec IN [\n      { d: \"2020-03-09\", label: \"Saudi-Russia price war\" },\n      { d: \"2019-09-14\", label: \"Abqaiq/Khurais attack\" },\n      { d: \"2018-03-22\", label: \"Section 301 memo signed\" }]\n    LET perTarget = (\n      FOR b IN BaselineStats\n        FILTER b.horizonDays == 1\n        LET ps = DOCUMENT(\"PriceSeries\", b.symbol)\n        LET i = ps.prev[spec.d]\n        FILTER i != null AND i + 1 < LENGTH(ps.closes)\n        FILTER ps.closes[i] > 0 AND ps.closes[i + 1] > 0\n        LET bm = DOCUMENT(\"PriceSeries\", \"_GSPC\")\n        LET j = bm.prev[spec.d]\n        LET raw = (ps.closes[i + 1] - ps.closes[i]) / ps.closes[i]\n        LET spx = (j != null AND j + 1 < LENGTH(bm.closes) AND bm.closes[j] > 0)\n                ? (bm.closes[j + 1] - bm.closes[j]) / bm.closes[j] : 0\n        LET mv = b.useAbnormal ? raw - spx : raw\n        LET z  = b.stdev == 0 ? null : (mv - b.mean) / b.stdev\n        FILTER z != null\n        SORT ABS(z) DESC\n        RETURN { target: SPLIT(b.target, \"/\")[1], mv: mv, stat: z })\n    LET top = FIRST(perTarget)\n    FILTER top != null\n    LET near = LENGTH(FOR x IN perTarget FILTER ABS(x.stat) >= 2 RETURN 1)\n    RETURN { ask: \"instance \u2014 one day\", question: spec.label,\n             strongest: top.target, movePct: ROUND(top.mv*10000)/100,\n             stat: ROUND(top.stat*100)/100, statIs: \"z\", observations: 1,\n             seriesPast2sd: near,\n             verdict: ABS(top.stat) >= 3 ? \"detectable\"\n                    : (near >= 2 ? \"something small \u2014 below bar\" : \"no\") })\n\nFOR r IN APPEND(classRows, instanceRows)\n  RETURN r\n",
        },
        {
            "name": "GDELT · Oil shock — the same method on a real shock (E2)",
            "description": ("Nine documented supply events, WTI forward returns from "
                            "each. No analog retrieval and no averaging — these are "
                            "individual events measured directly, which is the whole "
                            "point of the contrast with the tariff sweep."),
            "query": '''/* The same machinery on shocks that ARE real.  (E2)

 * Nine documented oil-supply events, WTI forward returns from each. No analog
 * retrieval and no averaging: these are individual events, measured directly.
 *
 * Windows anchor on `prev` — the last close STRICTLY BEFORE the event — so the
 * event day's own reaction is inside the window. This was `pos` until 2026-08-23,
 * which for an event on a trading day started the window AFTER the reaction and
 * measured the following days instead. It changed signs, not just magnitudes: the
 * Russian-oil ban read -12.13% and now reads +3.60%. The -12.13% was never the
 * ban; it was the UAE output announcement that broke the rally the next day.
 *
 * An earlier version of this comment drew a lesson from that contrast — that
 * anticipation, not size, decides whether an event is measurable. That lesson was
 * an artefact of the off-by-one and has been withdrawn. The ban probably WAS
 * priced in, but these numbers no longer demonstrate it.
 *
 * What the table does show: two events dominate, and both arrived when the market
 * was shut. The Saudi-Russia price war is the largest move in the corpus at
 * -24.59%; Abqaiq is +14.68%. The remaining seven sit inside +/-6% at one day,
 * which is ordinary for crude.
 *
 * Read the 10-day column with care. The price war's -45.66% runs straight into the
 * COVID drawdown, so that cell is two events, not one. The 1-day figure is the
 * clean one.
 */
LET episodes = [
  { d: "2019-05-12", label: "Fujairah tanker attacks" },
  { d: "2019-06-13", label: "Gulf of Oman tanker attacks" },
  { d: "2019-06-20", label: "US surveillance drone shot down" },
  { d: "2019-07-19", label: "Stena Impero seizure" },
  { d: "2019-09-14", label: "Abqaiq/Khurais attack" },
  { d: "2020-01-03", label: "Soleimani strike" },
  { d: "2020-03-09", label: "Saudi-Russia price war" },
  { d: "2022-02-24", label: "Russia invades Ukraine" },
  { d: "2022-03-08", label: "US ban on Russian oil" }]

LET wti = DOCUMENT("PriceSeries", "CL=F")
FOR e IN episodes
  LET i = wti.prev[e.d]
  FILTER i != null
  LET mv = (FOR h IN [1, 3, 5, 10]
              FILTER i + h < LENGTH(wti.closes)
              FILTER wti.closes[i] > 0 AND wti.closes[i+h] > 0
              RETURN ROUND(((wti.closes[i+h] - wti.closes[i]) / wti.closes[i]) * 10000) / 100)
  SORT ABS(mv[0]) DESC
  RETURN { event: e.label, date: e.d, anchoredOn: wti.dates[i],
           wti_1d: mv[0], wti_3d: mv[1], wti_5d: mv[2], wti_10d: mv[3] }''',
        },
        {
            "name": "GDELT · Theme→Sector density (the honest caveat)",
            "description": ("Shows why the cited analogs do not currently discriminate the "
                            "ranking: Theme->Sector is complete bipartite. "
                            "Have this ready; someone careful will ask."),
            "query": """/* Every theme correlates with every sector: structure carries no
   selectivity at that hop, only the weights do. Run this rather than
   explaining it — 16 themes x 11 sectors x 4 horizons = 704. */
LET toSector = (
  FOR e IN correlatesWith
    FILTER IS_SAME_COLLECTION("Sector", DOCUMENT(e._to))
    RETURN e)
RETURN {
  themes:   LENGTH(UNIQUE(toSector[*]._from)),
  sectors:  LENGTH(UNIQUE(toSector[*]._to)),
  horizons: UNIQUE(toSector[*].horizonDays),
  edges:    LENGTH(toSector),
  completeBipartite: LENGTH(toSector) ==
      LENGTH(UNIQUE(toSector[*]._from)) * LENGTH(UNIQUE(toSector[*]._to)),
  confidentEdges: LENGTH(FOR e IN toSector FILTER ABS(e.tStat) >= 2 RETURN 1) }""",
        },
        {
            "name": "GDELT · Portfolio exposure by sector (table)",
            "description": ("The portfolio overlay as a table — every holding resolved to a "
                            "sector, including the ETF sleeve, with how it resolved."),
            "query": """/* Cross-graph join: demo portfolio -> holdings -> instrument -> sector.
 *
 * Two resolution routes, because the schema models them differently on purpose:
 *   Company holdings  -> traverse partOf (Company->Sector).
 *   Sector-ETF holdings -> read Index.sector. There is deliberately NO
 *     Sector<->ETF edge: docs/schema.md keeps the tradeable proxy as a field
 *     (Sector.proxySymbol / Index.sector) rather than a new edge class, and
 *     partOf runs Sector->^GSPC (the benchmark), not Sector->its own ETF.
 * Resolving only the first route left 37% of portfolio weight (XLF/XLE/XLK)
 * showing sector:null. `sectorVia` reports which route answered, so the join
 * stays auditable rather than merely complete. */
WITH Portfolio, Holding, Company, Index, Sector
FOR p IN Portfolio
  FOR h IN 1..1 OUTBOUND p holds
    FOR inst IN 1..1 OUTBOUND h holds
      LET viaPartOf = FIRST(FOR s IN 1..1 OUTBOUND inst partOf
                              FILTER IS_SAME_COLLECTION("Sector", s) RETURN s.name)
      LET sector = NOT_NULL(viaPartOf, inst.sector)
      SORT sector, -h.weight
      RETURN {
        portfolio: p.name, holding: h.ticker, weight: h.weight,
        instrument: inst._id, sector: sector,
        sectorVia: viaPartOf != null ? "partOf edge"
                 : (inst.sector != null ? "Index.sector (ETF proxy)" : "UNRESOLVED") }""",
        },
        {
            "name": "GDELT · Portfolio exposure ROLLED UP by sector",
            "description": ("The one number a client asks for: total portfolio weight per "
                            "sector, so the what-if's sector moves land on real exposure."),
            "query": """/* Rolled up: weight per sector, plus the holdings behind each row.
 * Resolves both routes (partOf for companies, Index.sector for the ETF sleeve),
 * so `SUM(weight)` reconciles to 1.00 rather than silently dropping 37%. */
WITH Portfolio, Holding, Company, Index, Sector
FOR p IN Portfolio
  FOR h IN 1..1 OUTBOUND p holds
    FOR inst IN 1..1 OUTBOUND h holds
      LET viaPartOf = FIRST(FOR s IN 1..1 OUTBOUND inst partOf
                              FILTER IS_SAME_COLLECTION("Sector", s) RETURN s.name)
      LET sector = NOT_NULL(viaPartOf, inst.sector)
      COLLECT sec = sector INTO rows = {t: h.ticker, w: h.weight}
      LET weight = SUM(rows[*].w)
      SORT weight DESC
      RETURN { sector: sec, weight: weight,
               pctOfBook: ROUND(weight * 1000) / 10,
               holdings: rows[*].t }""",
        },
    ]


# --------------------------------------------------------------------------
# Canvas actions — right-click. Name carries the applicable types
# --------------------------------------------------------------------------
def action_name(types: list[str], base: str) -> str:
    return f"[{', '.join(types)}] {base}"


def canvas_actions(w: str, edge_list: str) -> list[dict]:
    return [
        {
            "types": ["*"], "base": "Expand 2 hops (any relationship)",
            "description": "Generic explorer — every edge type, both directions, capped at 100.",
            "query": f"""{w}
FOR node IN @nodes
  FOR v, e IN 1..2 ANY node GRAPH "{GRAPH_NAME}"
    LIMIT 100
    RETURN e""",
        },
        {
            "types": ["Event"], "base": "Themes → market impact (5-day)",
            "description": ("The causal chain out of one event: its GKG themes and the assets "
                            "those themes move, as complete 2-hop paths (~102). 5-day horizon "
                            "only, or the four horizons quadruple the canvas."),
            "query": f"""{w}
// 2..2 in ONE traversal so p carries Event->Theme AND Theme->asset. Two nested
// 1-hop traversals would drop the first edge and the chain would render broken.
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Event", node)
  FOR asset, corr, p IN 2..2 OUTBOUND node taggedWith, correlatesWith
    FILTER corr.horizonDays == 5
    LIMIT 200
    RETURN p""",
        },
        {
            "types": ["Event"], "base": f"Themes → CONFIDENT impact (|tStat| ≥ {CONFIDENT:.0f}, 5-day)",
            "description": ("Same hop, only statistically defensible edges — ~22 paths against "
                            "~102. Use this the moment someone asks how much to trust it."),
            "query": f"""{w}
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Event", node)
  FOR asset, corr, p IN 2..2 OUTBOUND node taggedWith, correlatesWith
    FILTER corr.horizonDays == 5 AND ABS(corr.tStat) >= {CONFIDENT}
    LIMIT 200
    RETURN p""",
        },
        {
            "types": ["Event"], "base": "Actors, location and themes",
            "description": "The event's own structural context — who, where, what it was about.",
            "query": f"""{w}
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Event", node)
  FOR v, e, p IN 1..1 ANY node mentions, locatedIn, taggedWith
    LIMIT 60
    RETURN p""",
        },
        {
            "types": ["Event"], "base": "Temporal neighbours (precedes, coOccursWith)",
            "description": ("What happened just before and after: the day-to-day spine forward "
                            "and back, plus other events the same day involving the same "
                            "country pair."),
            "query": f"""{w}
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Event", node)
  FOR v, e, p IN 1..2 ANY node precedes, coOccursWith
    LIMIT 60
    RETURN p""",
        },
        {
            "types": ["Theme"], "base": "Market impact (all horizons)",
            "description": "Every asset this theme correlates with, at all four horizons (1/3/5/10d).",
            "query": f"""{w}
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Theme", node)
  FOR v, e, p IN 1..1 OUTBOUND node correlatesWith
    LIMIT 80
    RETURN p""",
        },
        {
            "types": ["Sector"], "base": "Trace back to events (why this sector)",
            "description": ("Reverse the chain: which themes reached this sector and which "
                            "events carried them — the audit trail behind one row of the "
                            "answer. Best-supported event-theme links first."),
            "query": f"""{w}
// 1..2 keeps BOTH the Sector<-Theme edge and the Theme<-Event edge in p; a nested
// pair of 1-hop traversals would lose the correlatesWith edge off the canvas.
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Sector", node)
  FOR v, e, p IN 1..2 INBOUND node correlatesWith, taggedWith
    FILTER p.edges[0].horizonDays == 5
    SORT LENGTH(p.edges), p.edges[1].articleCount DESC
    LIMIT 60
    RETURN p""",
        },
        {
            "types": ["Sector", "Index", "Company"], "base": "Portfolio exposure (join)",
            "description": "Cross-graph join down to the demo portfolio's holdings.",
            "query": f"""{w}
FOR node IN @nodes
  FOR v, e, p IN 1..3 INBOUND node partOf, holds
    FILTER IS_SAME_COLLECTION("Portfolio", v) OR IS_SAME_COLLECTION("Holding", v)
       OR IS_SAME_COLLECTION("Company", v)
    LIMIT 60
    RETURN p""",
        },
        {
            "types": ["Commodity", "MacroVariable"], "base": "Second-order impact (affects)",
            "description": "Propagation into sectors — the oil and rates/inflation channels.",
            "query": f"""{w}
FOR node IN @nodes
  FOR v, e, p IN 1..1 OUTBOUND node affects
    LIMIT 60
    RETURN p""",
        },
        {
            "types": ["Actor"], "base": "Events involving this actor",
            "description": "Canonical CAMEO actor back to the events that mention it.",
            "query": f"""{w}
FOR node IN @nodes
  FILTER IS_SAME_COLLECTION("Actor", node)
  FOR v, e, p IN 1..1 INBOUND node mentions
    LIMIT 60
    RETURN p""",
        },
    ]


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------
def ensure_default_viewpoint(database) -> str:
    ensure_collection(database, "_viewpoints")
    col = database.collection("_viewpoints")
    for probe in ({"graphId": GRAPH_NAME, "name": "Default"}, {"graphId": GRAPH_NAME}):
        found = list(col.find(probe))
        if found:
            return found[0]["_id"]
    ts = now_iso()
    return col.insert({"graphId": GRAPH_NAME, "name": "Default",
                       "description": f"Default viewpoint for {GRAPH_NAME}",
                       "createdAt": ts, "updatedAt": ts})["_id"]


def upsert(col, doc: dict) -> str:
    """Idempotent _key-based upsert that preserves createdAt."""
    ts = now_iso()
    existing = col.get(doc["_key"])
    doc = {**doc, "updatedAt": ts, "createdAt": (existing or {}).get("createdAt", ts)}
    if existing:
        col.replace(doc, check_rev=False)
    else:
        col.insert(doc)
    return doc["_key"]


def link(edge_col, vp_id: str, target_id: str) -> None:
    if not list(edge_col.find({"_from": vp_id, "_to": target_id})):
        ts = now_iso()
        edge_col.insert({"_from": vp_id, "_to": target_id, "createdAt": ts, "updatedAt": ts})


MANAGED_BY = "install_visualizer_assets"


def reconcile(database, col_name: str, link_name: str, keep: set[str]) -> list[str]:
    """Drop docs THIS SCRIPT installed on a previous run that no longer exist.

    Scoped to `managedBy == MANAGED_BY` deliberately. An earlier version matched on
    graphId alone and deleted the Visualizer's own built-in "Fetch edges of type
    @@edgeType (default)" query and "Find 2-hop neighbors (default)" action — assets
    this script never created and has no business removing. Renames of our own
    assets still reconcile, because the marker travels with them.
    """
    col, links = database.collection(col_name), database.collection(link_name)
    removed = []
    for doc in list(col.find({"graphId": GRAPH_NAME, "managedBy": MANAGED_BY})):
        if doc["_key"] not in keep:
            for e in links.find({"_to": doc["_id"]}):
                links.delete(e["_key"])
            col.delete(doc["_key"])
            removed.append(doc.get("name", doc["_key"]))
    return removed


# The Visualizer seeds these per graph; an earlier reconcile bug removed them.
# Restored idempotently, with @edgeType repointed at a collection that still
# exists — the seeded value was "AFFECTS", which the camelCase rename invalidated.
BUILTIN_QUERY = {
    "name": "Fetch edges of type @@edgeType (default)",
    "description": "Fetch all edges of the specified edge type",
    "queryText": "FOR doc IN @@edgeType LIMIT 100 RETURN doc",
    "bindVariables": {"@edgeType": "affects"},
}
BUILTIN_ACTION = {
    "name": "Find 2-hop neighbors (default)",
    "description": "Find 2-hop neighbors of the selected nodes",
    "queryText": f"""
      FOR node IN @nodes
        FOR v, e IN 1..2
          ANY node
          GRAPH "{GRAPH_NAME}"
          LIMIT 100
        RETURN e""",
    "bindVariables": {"nodes": []},
}


def restore_builtins(database, vp_id: str) -> list[str]:
    """Re-create the two stock assets if absent. Never marked managedBy."""
    restored = []
    for col_name, link_name, spec in (
            ("_queries", "_viewpointQueries", BUILTIN_QUERY),
            ("_canvasActions", "_viewpointActions", BUILTIN_ACTION)):
        col, links = database.collection(col_name), database.collection(link_name)
        if list(col.find({"graphId": GRAPH_NAME, "name": spec["name"]})):
            continue
        ts = now_iso()
        doc = {**spec, "graphId": GRAPH_NAME, "createdAt": ts, "updatedAt": ts}
        new_id = col.insert(doc)["_id"]
        link(links, vp_id, new_id)
        restored.append(spec["name"])
    return restored


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print names + AQL, write nothing")
    ap.add_argument("--test", action="store_true",
                    help="execute every query against the live graph; write nothing")
    args = ap.parse_args()

    database = db()
    vertex_colls, edge_colls = graph_schema(database, GRAPH_NAME)
    w = with_clause(vertex_colls, edge_colls)
    edge_list = ", ".join(sorted(edge_colls))

    panel, editor, actions = panel_queries(w), editor_queries(), canvas_actions(w, edge_list)

    if args.dry_run:
        for group, items in (("_queries", panel), ("_editor_saved_queries", editor)):
            print(f"\n=== {group} ===")
            for q in items:
                print(f"\n--- {q['name']} ---\n{q['query']}")
        print("\n=== _canvasActions ===")
        for a in actions:
            print(f"\n--- {action_name(a['types'], a['base'])} ---\n{a['query']}")
        return 0

    if args.test:
        failures = 0
        print(f"testing {len(panel)} panel + {len(editor)} editor + {len(actions)} action queries\n")
        for q in panel + editor:
            try:
                n = len(list(database.aql.execute(q["query"], count=True)))
                flag = "OK " if n else "EMPTY"
                failures += 0 if n else 1
                print(f"  {flag} {n:>5} rows  {q['name']}")
            except Exception as e:
                failures += 1
                print(f"  FAIL        {q['name']}\n        {str(e)[:160]}")
        # Canvas actions need @nodes. Probe deterministically, and for Event use the
        # demo's own analogs — a random Event carries few themes, which understates
        # the all-vs-confident contrast the presenter will actually see.
        for a in actions:
            t = a["types"][0]
            probe = "Event" if t == "*" else t
            if probe == "Event":
                pick = (f'FOR x IN Event FILTER x.cameo == "{DEMO_CAMEO}" '
                        f'AND x.actorFromCode == "{DEMO_FROM}" AND x.actorToCode == "{DEMO_TO}" '
                        'SORT x.numMentions DESC LIMIT 1 RETURN x._id')
            elif probe == "Sector":
                pick = f'RETURN DOCUMENT("Sector/{DEMO_SECTOR}")._id'
            else:
                pick = f"FOR x IN {probe} SORT x._key LIMIT 1 RETURN x._id"
            nodes = list(database.aql.execute(pick))
            try:
                n = len(list(database.aql.execute(a["query"], bind_vars={"nodes": nodes})))
                flag = "OK " if n else "EMPTY"
                failures += 0 if n else 1
                print(f"  {flag} {n:>5} rows  {action_name(a['types'], a['base'])}")
            except Exception as e:
                failures += 1
                print(f"  FAIL        {action_name(a['types'], a['base'])}\n        {str(e)[:160]}")
        print(f"\n{failures} failing/empty" if failures else "\nall queries return rows")
        return 1 if failures else 0

    for name in ("_queries", "_editor_saved_queries", "_canvasActions"):
        ensure_collection(database, name)
    for name in ("_viewpointQueries", "_viewpointActions"):
        ensure_collection(database, name, edge=True)

    vp_id = ensure_default_viewpoint(database)
    for name in restore_builtins(database, vp_id):
        print(f"  restored built-in: {name}")
    q_col = database.collection("_queries")
    e_col = database.collection("_editor_saved_queries")
    a_col = database.collection("_canvasActions")
    vq_col = database.collection("_viewpointQueries")
    va_col = database.collection("_viewpointActions")

    # 1. Visualizer Queries panel — queryText + _viewpointQueries edge.
    panel_keys = set()
    for q in panel:
        key = upsert(q_col, {
            "_key": slugify(f"{GRAPH_NAME}_{q['name']}"),
            "graphId": GRAPH_NAME, "name": q["name"], "title": q["name"],
            "description": q["description"], "queryText": q["query"], "bindVariables": {},
            "managedBy": MANAGED_BY,
        })
        panel_keys.add(key)
        link(vq_col, vp_id, f"_queries/{key}")
    print(f"✓ {len(panel_keys)} Visualizer panel queries -> _queries (+ viewpoint links)")

    # 2. AQL editor — content AND value, never queryText. Not viewpoint-linked.
    for q in editor:
        upsert(e_col, {
            "_key": slugify(f"{GRAPH_NAME}_{q['name']}"),
            "graphId": GRAPH_NAME, "name": q["name"], "title": q["name"],
            "description": q["description"],
            "content": q["query"], "value": q["query"],
            "bindVariables": {}, "databaseName": database.name,
            "managedBy": MANAGED_BY,
        })
    print(f"✓ {len(editor)} editor queries -> _editor_saved_queries (content + value)")

    # 3. Canvas actions — queryText + @nodes + _viewpointActions edge.
    action_keys = set()
    for a in actions:
        name = action_name(a["types"], a["base"])
        key = upsert(a_col, {
            "_key": slugify(f"{GRAPH_NAME}_{name}"),
            "graphId": GRAPH_NAME, "name": name, "description": a["description"],
            "applicableTypes": a["types"], "queryText": a["query"],
            "bindVariables": {"nodes": []}, "managedBy": MANAGED_BY,
        })
        action_keys.add(key)
        link(va_col, vp_id, f"_canvasActions/{key}")
    print(f"✓ {len(action_keys)} canvas actions -> _canvasActions (+ viewpoint links)")

    for label, col_name, link_name, keep in (
            ("queries", "_queries", "_viewpointQueries", panel_keys),
            ("actions", "_canvasActions", "_viewpointActions", action_keys)):
        gone = reconcile(database, col_name, link_name, keep)
        if gone:
            print(f"  reconciled {len(gone)} stale {label}: {', '.join(gone)}")

    print(f"\n  viewpoint: {vp_id}   graph: {GRAPH_NAME}   db: {database.name}")
    print("  Reload the Visualizer: Queries panel = 7 numbered demo steps; "
          "right-click a node = Canvas Actions.")
    print("  Verify with: python scripts/install_visualizer_assets.py --test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
