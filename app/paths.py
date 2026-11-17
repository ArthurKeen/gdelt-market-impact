"""Why these markets: the supply paths from a chokepoint to who depends on it.

For a day with events located in a chokepoint region, walk the exposure graph
(scripts/build_exposure_graph.py) outward:

    Chokepoint <-flowsThrough- Good <-dependsOn- Country <-listedIn- market
                                    <-uses- Company <-suppliedBy- Company (held?)

Every figure on the way carries its published source. This explains why the
"Who else felt it" markets are on screen; it is not evidence that they moved,
and the pre-registered tests (docs/exposure-hypotheses.md, -h5.md) found no
systematic extra exposure for Japan, Korea or TSMC on Gulf conflict days, or on
days whose coverage names an oil facility.
"""
from __future__ import annotations

PATHS_AQL = """
LET touched = (
  FOR ev IN Event
    FILTER ev.dateAdded == @date
    FOR loc IN 1..1 OUTBOUND ev locatedIn
      FOR cp IN 1..1 OUTBOUND loc inRegion
        COLLECT key = cp._key WITH COUNT INTO events
        RETURN { key, events })
LET held = (FOR h IN Holding RETURN h.ticker)
FOR t IN touched
  LET cp = DOCUMENT("Chokepoint", t.key)
  LET goods = (
    FOR g, f IN 1..1 INBOUND cp flowsThrough
      LET importers = (
        FOR c, d IN 1..1 INBOUND g dependsOn
          FILTER d.via == null OR d.via == cp._key
          LET markets = (FOR m IN 1..1 INBOUND c listedIn
                           FILTER IS_SAME_COLLECTION("Index", m)
                           RETURN m.symbol == "^GSPC" ? "S&P 500" : m.name)
          SORT d.share DESC
          RETURN { country: c.name, share: d.share, lo: d.lo, hi: d.hi,
                   measure: d.measure, source: d.source, sourceUrl: d.sourceUrl,
                   markets })
      LET producers = (
        FOR c, p IN 1..1 INBOUND g produces
          FILTER p.via == null OR p.via == cp._key
          RETURN { country: c.name, share: p.share, measure: p.measure,
                   source: p.source, sourceUrl: p.sourceUrl })
      LET companies = (
        FOR co, u IN 1..1 INBOUND g uses
          LET customers = (
            FOR cu, s IN 1..1 INBOUND co suppliedBy
              RETURN { name: NOT_NULL(s.customer, cu.name), ticker: cu.ticker,
                       held: cu.ticker IN held })
          RETURN { name: co.name, role: u.role, note: u.note, customers })
      SORT g.order
      RETURN { good: g.name, share: f.share, lo: f.lo, hi: f.hi, measure: f.measure,
               source: f.source, sourceUrl: f.sourceUrl,
               importers, producers, companies })
  LET transits = (
    FOR co, u IN 1..1 INBOUND cp uses
      RETURN { name: co.name, role: u.role, note: u.note })
  SORT t.events DESC
  RETURN { chokepoint: cp.name, events: t.events, goods, transits }
"""


def day_paths(run, date: str) -> list[dict]:
    """The exposure graph is optional: a database built without it (an older load,
    or a starter build that skipped it) gets no paths panel, not a failed day.
    That failure shipped once: the starter kit predated the graph, and every
    single-day question on it returned a 500."""
    try:
        return run(PATHS_AQL, date=date)
    except Exception as e:  # arango.exceptions.AQLQueryExecuteError
        if getattr(e, "error_code", None) == 1203:   # collection or view not found
            return []
        raise
