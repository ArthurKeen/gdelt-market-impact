"""AQL for the what-if UI. Every query here is one already validated in the demo.

The UI's job is not to compute anything new — it is to put the answer and the
evidence that the answer is or is not real on one screen, which the stock graph
viewer cannot do.
"""
from __future__ import annotations

HORIZONS = (1, 3, 5, 10)

def _entity_clause(group, target) -> str:
    """Optional narrowing by entities read out of the coverage.

    Built into the query text rather than expressed as `@group == null OR ...`
    so the array indexes on perpetrators / targetOperators / targetFacilities are
    actually used. Only the CLAUSE is chosen here — the value stays bound, so
    nothing the caller types reaches the query text.

    This is a retrieval facet. It narrows which events are measured; it does not
    make the measurement causal, and a class selected this way was selected on
    coverage (docs/entity-extraction.md, and scripts/entity_selection_test.py for
    what that is and is not worth).
    """
    out = []
    if group:
        out.append("  FILTER @group IN e.perpetrators")
    if target:
        out.append("  FILTER @target IN e.targetFacilities "
                   "OR @target IN e.targetOperators")
    return "\n".join(out)


def _window_clause(window_from, window_to) -> str:
    """Restrict retrieval to a span of the corpus.

    An extended corpus reaches 2026 while every published number was measured over
    2018-2022. Rather than choose, both are askable and the answer says which span
    it used — so the four demo presets keep reproducing while new questions can
    reach events the delivered window never saw.
    """
    out = []
    if window_from:
        out.append("  FILTER e.dateAdded >= @windowFrom")
    if window_to:
        out.append("  FILTER e.dateAdded <= @windowTo")
    return "\n".join(out)


def analogs(group=None, target=None, window_from=None, window_to=None) -> str:
    parts = [c for c in (_entity_clause(group, target),
                         _window_clause(window_from, window_to)) if c]
    clause = "\n".join(parts)
    # Consume the newline too, so the unfiltered query is byte-identical to the
    # one this replaced — the whole point of the refactor is that it changes
    # nothing when nothing is chosen.
    return ANALOGS.replace("__ENTITY__\n", clause + "\n" if clause else "")


def build(template: str, group=None, target=None,
          window_from=None, window_to=None) -> str:
    """Fill a template's analog block, at both sample sizes it may ask for."""
    a = analogs(group, target, window_from, window_to)
    return (template.replace("__ANALOGS_200__", a.replace("LIMIT @topK", "LIMIT 200"))
                    .replace("__ANALOGS__", a))


# One analog per N-day block, so no two measurement windows share a trading day.
# Block width tracks the horizon: a 5-day window needs >5 trading days of
# separation, and 7 calendar days is the safe rounding.
ANALOGS = """
FOR e IN Event
  FILTER e.cameo == @cameo
  FILTER e.actorFromCode == @actorFrom AND e.actorToCode == @actorTo
__ENTITY__
  COLLECT blk = FLOOR(DATE_DIFF("2018-01-01", e.dateAdded, "d") / @block) INTO g
  LET best = FIRST(FOR x IN g[*].e SORT x.numMentions DESC RETURN x)
  SORT best.numMentions DESC
  LIMIT @topK
  RETURN best
"""

# Outcome computed over the retrieved analogs, not from theme-level averages.
OUTCOME = """
LET analogs = (__ANALOGS__)
FOR sector IN Sector
  LET base = DOCUMENT("BaselineStats", CONCAT(sector.proxySymbol, "_", @horizon))
  LET ps   = DOCUMENT("PriceSeries", sector.proxySymbol)
  LET bm   = DOCUMENT("PriceSeries", "_GSPC")
  LET obs = (
    FOR a IN analogs
      LET i = ps.prev[a.dateAdded]
      FILTER i != null AND i + @horizon < LENGTH(ps.closes)
      COLLECT idx = i INTO grp
      LET j = bm.pos[ps.dates[idx]]
      LET raw = (ps.closes[idx+@horizon] - ps.closes[idx]) / ps.closes[idx]
      LET bench = (base.useAbnormal AND j != null AND j+@horizon < LENGTH(bm.closes))
                ? (bm.closes[j+@horizon] - bm.closes[j]) / bm.closes[j] : 0
      RETURN raw - bench)
  LET n  = LENGTH(obs)
  LET sd = n > 1 ? STDDEV_SAMPLE(obs) : null
  FILTER n > 0
  LET excess = AVERAGE(obs) - base.mean
  LET t = (sd == null OR sd == 0) ? null : excess / (sd / SQRT(n))
  SORT ABS(t == null ? 0 : t) DESC
  // The brief's S1 asks for a magnitude RANGE, not only a point estimate. The
  // theme-level query this replaced returned one; the analog-conditional rewrite
  // dropped it by oversight. Worst and best observed outcome across the retrieved
  // analogs — often the more honest number, since a +0.5% mean over observations
  // running -4% to +5% is not a forecast of anything.
  RETURN { sector: sector.name, movePct: ROUND(excess*10000)/100,
            lowPct: ROUND(MIN(obs)*10000)/100, highPct: ROUND(MAX(obs)*10000)/100,
            tStat: t == null ? null : ROUND(t*100)/100, n: n }
"""

# The verdict. Same question at growing sample sizes: a real effect sharpens,
# noise wanders and fades.
SWEEP = """
LET ranked = (__ANALOGS_200__)
FOR k IN @ladder
  LET analogs = SLICE(ranked, 0, k)
  LET perSector = (
    FOR sector IN Sector
      LET base = DOCUMENT("BaselineStats", CONCAT(sector.proxySymbol, "_", @horizon))
      LET ps   = DOCUMENT("PriceSeries", sector.proxySymbol)
      LET bm   = DOCUMENT("PriceSeries", "_GSPC")
      LET obs = (
        FOR a IN analogs
          LET i = ps.prev[a.dateAdded]
          FILTER i != null AND i + @horizon < LENGTH(ps.closes)
          COLLECT idx = i INTO grp
          LET j = bm.pos[ps.dates[idx]]
          LET raw = (ps.closes[idx+@horizon] - ps.closes[idx]) / ps.closes[idx]
          LET bench = (base.useAbnormal AND j != null AND j+@horizon < LENGTH(bm.closes))
                    ? (bm.closes[j+@horizon] - bm.closes[j]) / bm.closes[j] : 0
          RETURN raw - bench)
      LET n  = LENGTH(obs)
      FILTER n > 1
      LET sd = STDDEV_SAMPLE(obs)
      LET excess = AVERAGE(obs) - base.mean
      LET t = sd == 0 ? 0 : excess / (sd / SQRT(n))
      SORT ABS(t) DESC
      LIMIT 1
      RETURN { sector: sector.name, movePct: ROUND(excess*10000)/100,
                tStat: ROUND(t*100)/100, n: n })
  LET top = FIRST(perSector)
  FILTER top != null
  RETURN { requested: k, n: top.n, sector: top.sector,
            movePct: top.movePct, tStat: top.tStat,
            looksSignificant: ABS(top.tStat) >= 2 }
"""

# The graph behind the answer: analogs -> themes -> sectors, as paths so the UI
# can lay them out in columns itself.
GRAPH = """
WITH Event, Theme, Sector
LET analogs = (__ANALOGS__)
FOR ev IN analogs
  FOR theme IN 1..1 OUTBOUND ev taggedWith
    LIMIT 300
    RETURN { event: ev._key, date: ev.dateAdded, mentions: ev.numMentions,
              quad: ev.quadClass, theme: theme.name,
              // Enough to identify the event on hover. Two events can share a date
              // — on 2019-09-16 Yemen->Saudi has both the Abqaiq strike (940
              // mentions) and a Houthi warning of further attacks (42) — so a date
              // alone does not say which one a node is.
              cameo: ev.cameo, goldstein: ev.goldstein, tone: ev.avgTone,
              actorFrom: ev.actorFromCode, actorTo: ev.actorToCode,
              geo: ev.geoName, geoCountry: ev.geoCountry, url: ev.sourceUrl,
              // Recovered from the coverage, not from CAMEO — see
              // scripts/extract_entities.py and docs/entity-extraction.md.
              perpetrators: ev.perpetrators, operators: ev.targetOperators,
              facilities: ev.targetFacilities, coverage: ev.coverageArticles,
              // Shares, so contested attribution can be shown as contested.
              entityDetail: ev.entityDetail }
"""

# Sector membership for the third column — which sectors the themes reach at all.
SECTORS = """
FOR s IN Sector
  RETURN { name: s.name, proxy: s.proxySymbol }
"""

# Individual named events measured directly — the oil comparison.
DIRECT_EVENTS = """
LET wti = DOCUMENT("PriceSeries", @symbol)
FOR e IN @episodes
  LET i = wti.prev[e.d]
  FILTER i != null
  LET mv = (FOR h IN @horizons
              FILTER i + h < LENGTH(wti.closes)
              RETURN ROUND(((wti.closes[i+h] - wti.closes[i]) / wti.closes[i]) * 10000) / 100)
  SORT ABS(mv[0]) DESC
  RETURN { event: e.label, date: e.d, moves: mv }
"""

CORPUS = """
RETURN {
  events: COLLECTION_COUNT("Event"),
  days: LENGTH(FOR e IN Event COLLECT d = e.dateAdded RETURN 1),
  records: SUM(FOR c IN @colls RETURN COLLECTION_COUNT(c))
}
"""


# --- single-event mode -------------------------------------------------------
# A class of events is judged by whether an average survives more evidence. One
# event has n=1, so there is no average and no t-statistic. The right question is
# how far the move sits from what that asset does on a normal day:
#     z = (move - baseline mean) / baseline stdev
# Abqaiq puts Brent at z=5.1; an ordinary Wednesday puts nothing above 2.
SINGLE_EVENT = """
FOR b IN BaselineStats
  FILTER b.horizonDays == @horizon
  // Two measurement windows can live side by side; rows written before windows
  // existed carry none and are the delivered one.
  FILTER NOT_NULL(b.windowId, "delivered") == @windowId
  // BaselineStats.symbol is the RAW ticker; PriceSeries is keyed by the sanitised
  // form, because "^" is illegal in an ArangoDB key. Joining on the raw symbol
  // silently returned null for ^GSPC, ^NDX and ^VIX — 14 rows of 17 — so the panel
  // could never show the S&P comparison the brief's Abqaiq argument rests on.
  LET seriesKey = SUBSTITUTE(b.symbol, "^", "_")
  LET ps = DOCUMENT("PriceSeries", seriesKey)
  LET cond = DOCUMENT("ConditionalBaseline",
                      CONCAT(seriesKey, "_", @horizon, @windowSuffix))
  LET i = ps.prev[@date]
  FILTER i != null AND i + @horizon < LENGTH(ps.closes)
  FILTER ps.closes[i] > 0 AND ps.closes[i + @horizon] > 0
  LET bm = DOCUMENT("PriceSeries", "_GSPC")
  LET j = bm.prev[@date]
  LET raw = (ps.closes[i + @horizon] - ps.closes[i]) / ps.closes[i]
  // The baseline for a sector ETF is built from MARKET-ADJUSTED returns, so the
  // move compared against it has to be adjusted the same way. Comparing a raw move
  // to an abnormal baseline is a units error, and it is not a harmless one: on
  // 2022-03-08 the S&P rose 2.57%, and the raw ranking put "Information Technology
  // +3.96%" at the top of an OIL EMBARGO. Net of the market that is +1.39% —
  // unremarkable — while Brent's -13.16% becomes -15.73%, which is the event.
  LET spx = (j != null AND j + @horizon < LENGTH(bm.closes) AND bm.closes[j] > 0)
          ? (bm.closes[j + @horizon] - bm.closes[j]) / bm.closes[j] : 0
  LET mv = b.useAbnormal ? raw - spx : raw
  LET z  = b.stdev == 0 ? null : (mv - b.mean) / b.stdev
  // Conditional sigma for the same day. `pos` indexes the kept observations, which
  // are not the price-series indices — a move needs `horizon` days of future to
  // exist, so the tail is absent.
  LET ci = cond == null ? null : cond.pos[ps.dates[i]]
  LET sdT = ci == null ? null : cond.trailSd[ci]
  LET sdR = ci == null ? null : cond.regimeSd[ci]
  LET zT = (sdT == null OR sdT == 0) ? null : (mv - b.mean) / sdT
  LET zR = (sdR == null OR sdR == 0) ? null : (mv - b.mean) / sdR
  FILTER z != null
  SORT ABS(z) DESC
  RETURN { symbol: b.symbol,
           target: SPLIT(b.target, "/")[1],
           kind: SPLIT(b.target, "/")[0],
           movePct: ROUND(raw * 10000) / 100,
           adjustedPct: ROUND(mv * 10000) / 100,
           marketAdjusted: b.useAbnormal,
           z: ROUND(z * 100) / 100,
           zTrailing: zT == null ? null : ROUND(zT * 100) / 100,
           zRegime: zR == null ? null : ROUND(zR * 100) / 100,
           regime: (cond == null OR ci == null) ? null : FIRST(
             FOR r IN cond.regimes
               FILTER r.from <= ps.dates[i] AND r.to >= ps.dates[i]
               RETURN { from: r.from, to: r.to, days: r.days }),
           anchor: ps.dates[i] }
"""

# What was in the news that day — the graph side of a single event.
EVENT_CONTEXT = """
WITH Event, Theme
// Coverage volume alone surfaces the wire stories, and those name the operator but
// not the perpetrator: on 2019-09-16 the six biggest events carry Abqaiq, Khurais
// and Saudi Aramco, and no group at all, while the day as a whole names both the
// Houthis and the IRGC. Ranking purely by mentions therefore hides the attribution
// that makes the event legible. So reserve up to two slots for the best-covered
// events that DO name a group, then fill the rest by the usual ordering.
LET withWho = (
  FOR e IN Event
    FILTER e.dateAdded == @date AND LENGTH(e.perpetrators) > 0
    SORT e.numMentions DESC
    LIMIT 2
    RETURN e)
LET byVolume = (
  FOR e IN Event
    FILTER e.dateAdded == @date
    LET named = (LENGTH(e.perpetrators) + LENGTH(e.targetFacilities)
                 + LENGTH(e.targetOperators)) > 0 ? 1 : 0
    SORT named DESC, e.numMentions DESC
    LIMIT 6
    RETURN e)
FOR e IN SLICE(UNIQUE(APPEND(withWho, byVolume)), 0, 6)
  FOR t IN 1..1 OUTBOUND e taggedWith
    RETURN { event: e._key, date: e.dateAdded, mentions: e.numMentions,
             quad: e.quadClass, theme: t.name,
             cameo: e.cameo, goldstein: e.goldstein, tone: e.avgTone,
             actorFrom: e.actorFromCode, actorTo: e.actorToCode,
             geo: e.geoName, geoCountry: e.geoCountry, url: e.sourceUrl,
             perpetrators: e.perpetrators, operators: e.targetOperators,
             facilities: e.targetFacilities, coverage: e.coverageArticles,
             entityDetail: e.entityDetail }
"""


# ---- entities recovered from the coverage -----------------------------------
# The vocabulary for the two narrowing controls. Day counts matter more than
# event counts here: the event study measures distinct days, so an entity naming
# 3,938 events across 150 days offers 150 observations, not 3,938.
ENTITIES = """
WITH Event
LET pool = (
  FOR e IN Event
__CLASS__
    FILTER LENGTH(e.perpetrators) > 0 OR LENGTH(e.targetFacilities) > 0
        OR LENGTH(e.targetOperators) > 0
    RETURN { p: e.perpetrators, t: APPEND(e.targetFacilities, e.targetOperators),
             d: e.dateAdded })
LET groups = (
  FOR e IN pool
    FOR x IN NOT_NULL(e.p, [])
      COLLECT name = x INTO g
      LET days = LENGTH(UNIQUE(g[*].e.d))
      SORT days DESC
      RETURN { name, days })
LET targets = (
  FOR e IN pool
    FOR x IN NOT_NULL(e.t, [])
      COLLECT name = x INTO g
      LET days = LENGTH(UNIQUE(g[*].e.d))
      SORT days DESC
      RETURN { name, days })
RETURN { groups, targets }
"""


def entities(cameo=None, actor_from=None, actor_to=None) -> str:
    """Vocabulary for the narrowing controls, scoped to the question being asked.

    Scoped with the SAME predicate the analog retrieval uses, so every option
    offered returns at least one event and the day counts are the counts for that
    question rather than for the corpus. Offering "Hamas" under Yemen -> Saudi
    Arabia, or NIOC — an Iranian operator — as the target of an attack ON Saudi
    Arabia, is offering a guaranteed empty result.

    Scoping by actor CODE rather than by geography is deliberate. The entities come
    from the coverage, not from the actor slots, and the two do not line up: the
    Abqaiq strike is coded SAU -> YEM yet names the Houthis as the perpetrator. Any
    rule of the form "groups operating out of the first actor" would drop it.
    """
    f = []
    if cameo:
        f.append("    FILTER e.cameo == @cameo")
    if actor_from:
        f.append("    FILTER e.actorFromCode == @actorFrom")
    if actor_to:
        f.append("    FILTER e.actorToCode == @actorTo")
    return ENTITIES.replace("__CLASS__\n", "\n".join(f) + "\n" if f else "")


# Days matching an entity combination, for the single-event finder. Ranked by
# coverage because the best-covered day is the one worth measuring first.
ENTITY_DAYS = """
WITH Event
FOR e IN Event
__ENTITY_ONLY__
  COLLECT day = e.dateAdded INTO g
  LET mentions = SUM(g[*].e.numMentions)
  LET perps = UNIQUE(FLATTEN(g[*].e.perpetrators))
  LET facs  = UNIQUE(FLATTEN(g[*].e.targetFacilities))
  LET opers = UNIQUE(FLATTEN(g[*].e.targetOperators))
  SORT mentions DESC
  LIMIT 40
  RETURN { day, events: LENGTH(g), mentions,
           perpetrators: perps, facilities: facs, operators: opers }
"""


def entity_days(group=None, target=None) -> str:
    clause = _entity_clause(group, target)
    if not clause:
        clause = "  FILTER LENGTH(e.perpetrators) > 0"
    return ENTITY_DAYS.replace("__ENTITY_ONLY__", clause)
