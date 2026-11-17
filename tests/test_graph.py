"""Invariants that need a loaded `gdelt_market`. Skipped cleanly without one.

These check the shape of the data the published figures describe. A rebuild that
silently produces a different corpus — a dyad filter regression, a dropped join, an
un-rerun inferred-edge step — shows up here rather than in a figure nobody re-derived.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture(scope="module")
def db():
    try:
        from _common import db as _db
        d = _db()
        d.collection("Event").count()
        return d
    except Exception as exc:                       # no .env, no cluster, empty DB
        pytest.skip(f"no reachable gdelt_market ({type(exc).__name__})")


def test_corpus_is_the_one_the_documents_describe(db):
    n = db.collection("Event").count()
    assert n > 5_000_000, f"only {n:,} events — the documents cite 5.5M"


def test_the_dyad_filter_is_not_hub_and_spoke(db):
    """It was, silently, and every non-US pair was missing. 24% of the corpus now
    involves no US actor; a regression here would read as 0%."""
    pct = list(db.aql.execute("""
      WITH Event
      LET tot = LENGTH(FOR e IN Event RETURN 1)
      LET nonus = LENGTH(
        FOR e IN Event
          FILTER e.actorFromCode NOT LIKE "USA%" AND e.actorToCode NOT LIKE "USA%"
          FILTER e.actorFromCode != null AND e.actorToCode != null
          RETURN 1)
      RETURN nonus / tot"""))[0]
    assert pct > 0.15, f"only {pct:.1%} of events avoid the US — dyad filter regressed?"


def test_the_abqaiq_perpetrator_is_retrievable(db):
    """GDELT resolves the Houthis to YEM. Without the regional actors the strike
    itself is absent and only its diplomatic aftermath is in the corpus."""
    n = list(db.aql.execute("""
      WITH Event
      RETURN LENGTH(
        FOR e IN Event
          FILTER e.actorFromCode == "YEM" AND e.actorToCode == "SAU"
          FILTER e.cameo == "190"
          FILTER e.dateAdded >= "2019-09-14" AND e.dateAdded <= "2019-09-17"
          RETURN 1)"""))[0]
    assert n > 0, "the YEM->SAU strike on Abqaiq is missing from the corpus"


def test_price_series_carry_both_date_indexes(db):
    """`pos` maps a calendar date to the last trading day ON OR BEFORE it; `prev`
    to the last one STRICTLY BEFORE. Measuring from `pos` is the sign-flip defect."""
    ps = db.collection("PriceSeries").get("CL=F")
    assert ps and "pos" in ps and "prev" in ps
    # 2019-09-16 is itself a trading day: pos points at it, prev at the Friday.
    assert ps["dates"][ps["pos"]["2019-09-16"]] == "2019-09-16"
    assert ps["dates"][ps["prev"]["2019-09-16"]] < "2019-09-16"


def test_similarto_is_empty_by_design(db):
    """Analog retrieval runs at query time so the score stays inspectable. A
    non-zero count means someone materialised it and the explainability argument needs re-arguing."""
    assert db.collection("similarTo").count() == 0


def test_inferred_edges_were_rebuilt_with_the_corpus(db):
    """correlatesWith and affects come from a separate script. Loading the graph
    without re-running it leaves the previous corpus's statistics in place."""
    assert db.collection("correlatesWith").count() > 0
    assert db.collection("affects").count() > 0
    assert db.collection("BaselineStats").count() > 0


def test_the_headline_single_event_still_reads_as_documented(db):
    """Abqaiq: WTI +14.68% on the first session after the strike. Every write-up
    quotes it."""
    ps = db.collection("PriceSeries").get("CL=F")
    i = ps["prev"]["2019-09-14"]
    move = ps["closes"][i + 1] / ps["closes"][i] - 1
    assert move == pytest.approx(0.1468, abs=0.0005), f"got {move:.4%}, documents say +14.68%"


def test_coverage_entities_were_extracted(db):
    """scripts/extract_entities.py is a separate step after build_graph.py, and a
    rebuild that forgets it leaves the UI unable to name a perpetrator or a target."""
    got = list(db.aql.execute("""
      WITH Event
      LET fac = LENGTH(FOR e IN Event FILTER LENGTH(e.targetFacilities) > 0 RETURN 1)
      LET op  = LENGTH(FOR e IN Event FILTER LENGTH(e.targetOperators) > 0 RETURN 1)
      LET who = LENGTH(FOR e IN Event FILTER LENGTH(e.perpetrators) > 0 RETURN 1)
      RETURN {fac, op, who}"""))[0]
    # Measured 2026-08-27: 8,547 facility / 6,300 operator / 25,786 perpetrator.
    # The floors are well under those so ordinary corpus drift does not fail the
    # build, but a missing extract_entities.py run drops every one to zero.
    assert got["fac"] > 5_000, f"only {got['fac']:,} name a facility — extract_entities.py run?"
    assert got["op"] > 3_000, f"only {got['op']:,} name an operator"
    assert got["who"] > 10_000, f"only {got['who']:,} name a group"


def test_abqaiq_names_its_perpetrator_and_its_target(db):
    """The whole point of reading the coverage: CAMEO says YEM -> SAU code 190, and
    every GDELT actor field says YEMEN. Only the coverage says Houthis, Abqaiq and
    Saudi Aramco — and the demo shows this day."""
    rows = list(db.aql.execute("""
      WITH Event
      FOR e IN Event
        FILTER e.dateAdded IN ["2019-09-14", "2019-09-16"]
        FILTER LENGTH(e.perpetrators) > 0
        SORT e.numMentions DESC
        LIMIT 5
        RETURN {who: e.perpetrators, fac: e.targetFacilities,
                op: e.targetOperators, m: e.numMentions}"""))
    assert rows, "no event on the Abqaiq days names a perpetrator"
    assert any("Houthis" in r["who"] for r in rows), f"got {[r['who'] for r in rows]}"
    assert any("Abqaiq" in (r["fac"] or []) for r in rows)
    assert any("Saudi Aramco" in (r["op"] or []) for r in rows)


# --- baselines ---------------------------------------------------------------

def test_no_series_is_silently_dropped_from_a_single_event(db):
    """BaselineStats.symbol is the raw ticker; PriceSeries is keyed by the
    sanitised form, because "^" is illegal in an ArangoDB key. Joining on the raw
    symbol returned 14 rows of 17 and quietly lost ^GSPC, ^NDX and ^VIX — so the
    panel could not show the S&P comparison the Abqaiq argument rests on.
    """
    import sys, pathlib as _p
    sys.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
    from app import queries as Q
    # Two measurement windows share BaselineStats, and SINGLE_EVENT correctly
    # returns only the one it was asked for — so count that window, not the lot.
    want = len(list(db.aql.execute(
        'FOR b IN BaselineStats FILTER b.horizonDays == 1 '
        'FILTER NOT_NULL(b.windowId, "delivered") == "delivered" RETURN 1')))
    rows = list(db.aql.execute(Q.SINGLE_EVENT,
                               bind_vars={"date": "2019-09-16", "horizon": 1,
                                          "windowId": "delivered", "windowSuffix": ""}))
    assert len(rows) == want, f"{want} baselines but {len(rows)} rows — a join is failing"
    assert any(r["symbol"] == "^GSPC" for r in rows), "the S&P row is missing"


def test_conditional_baselines_cover_every_studied_series(db):
    missing = list(db.aql.execute("""
      FOR b IN BaselineStats
        LET c = DOCUMENT("ConditionalBaseline",
                         CONCAT(SUBSTITUTE(b.symbol, "^", "_"), "_", b.horizonDays))
        FILTER c == null
        RETURN b._key"""))
    assert not missing, f"no conditional baseline for {missing}"


def test_the_three_baselines_agree_that_abqaiq_is_detectable(db):
    """Not that they give the same number — they do not, and that is the point —
    but that the headline event survives every yardstick. A result that holds on
    only one is telling you about the yardstick."""
    import sys, pathlib as _p
    sys.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
    from app import queries as Q
    rows = list(db.aql.execute(Q.SINGLE_EVENT,
                               bind_vars={"date": "2019-09-16", "horizon": 1,
                                          "windowId": "delivered", "windowSuffix": ""}))
    oil = [r for r in rows if r["symbol"] == "CL=F"][0]
    for field in ("z", "zTrailing", "zRegime"):
        assert oil[field] is not None and abs(oil[field]) >= 3, \
            f"WTI at Abqaiq is {oil[field]} under {field}"


def test_volatility_regimes_are_detected_not_assumed(db):
    """The detector should find the 2020 variance regime without being told, and
    it should not be the only one — the corpus has several."""
    cond = db.collection("ConditionalBaseline").get("CL=F_1")
    regs = cond["regimes"]
    assert len(regs) >= 4, f"only {len(regs)} regimes — detection looks too blunt"
    loud = max(regs, key=lambda r: r["sd"])
    assert loud["from"] <= "2020-03-01" <= loud["to"], \
        f"loudest regime is {loud['from']}..{loud['to']}"
    assert loud["sd"] > 3 * min(r["sd"] for r in regs)


def test_the_delivered_window_still_reproduces_its_baselines(db):
    """The published numbers must not drift when the corpus is extended.

    Every originally published figure was measured over 2018-02-27..2022-03-10.
    An extended build adds a second window beside it; this asserts the first one
    still regenerates to the last decimal from the price data on disk, so a change
    to the window machinery cannot quietly move a number already published.
    """
    import pathlib as _p
    import sys
    root = _p.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    # Which price file the database was built from is a fact about the database,
    # so ask it rather than guess a directory — a hardcoded candidate list goes
    # stale the next time the corpus moves, and then reports drift belonging to
    # the file vintage instead of to the window machinery. yfinance revises
    # adjusted closes between pulls, so the wrong vintage always "fails".
    # Match the END as well as the start: a corpus refresh leaves two vintages on
    # disk that begin on the same day (data/full.prev and data/full), and matching
    # the start alone picked the old one and failed the 2026-11 refresh on drift of
    # one part in a million that belonged to Yahoo, not to the window.
    series = [ps["dates"] for ps in db.collection("PriceSeries").all() if ps.get("dates")]
    if not series:
        pytest.skip("no PriceSeries loaded")
    live_span = (min(d[0] for d in series), max(d[-1] for d in series))
    prices_file = None
    for cand in sorted(root.glob("data/**/prices.jsonl")):
        with cand.open() as fh:
            dates = [json.loads(line)["date"] for line in fh]
        if (min(dates), max(dates)) == live_span:
            prices_file = cand
            break
    if prices_file is None:
        pytest.skip(f"no prices.jsonl on disk spans {live_span[0]}..{live_span[1]}, "
                    "the span the database was built from")
    from build_inferred_edges import baseline_docs, load_jsonl
    fresh = {d["_key"]: d for d in baseline_docs(load_jsonl(prices_file), "delivered")}
    live = {b["_key"]: b for b in db.collection("BaselineStats").all()
            if (b.get("windowId") or "delivered") == "delivered"}
    assert fresh and live, "no delivered-window baselines to compare"
    drifted = [k for k, f in fresh.items()
               if k in live and (abs(f["stdev"] - live[k]["stdev"]) > 1e-9
                                 or abs(f["mean"] - live[k]["mean"]) > 1e-9)]
    assert not drifted, f"delivered-window baselines moved: {drifted[:5]}"


def test_delivered_baseline_keys_carry_no_suffix(db):
    """The delivered window keeps the bare key on purpose: a database built before
    windows existed has to keep resolving, and the app falls back to it."""
    keys = [b["_key"] for b in db.collection("BaselineStats").all()
            if (b.get("windowId") or "delivered") == "delivered"]
    assert keys, "no delivered baselines"
    assert not [k for k in keys if k.endswith(("_full", "_delivered"))]
