#!/usr/bin/env python3
"""What-if UI — a demo instrument, not a product.

Exists because the stock ArangoDB Graph Visualizer gives no control over layout,
and this demo's argument depends on it: the causal chain has to read as columns
(event → theme → sector), and the answer has to sit beside the evidence rather
than in another tab.

Scope guard, deliberately narrow: read-only, no auth, no persistence, no
multi-user, no write paths. If it needs a login it has left scope.

    python app/main.py            # http://127.0.0.1:8000
    python app/main.py --port 9000
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from _common import db  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import queries as Q  # noqa: E402
import portfolio as P  # noqa: E402
import macro as M  # noqa: E402
import nlq as N  # noqa: E402
import paths as X  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
COUNTED = ["Event", "mentions", "taggedWith", "locatedIn", "coOccursWith",
           "precedes", "correlatesWith", "affects"]

# The oil comparison is a fixed list of documented supply events. Not retrieved —
# these are named incidents measured directly, which is the point of the contrast.
OIL_EPISODES = [
    {"d": "2019-05-12", "label": "Fujairah tanker attacks"},
    {"d": "2019-06-13", "label": "Gulf of Oman tanker attacks"},
    {"d": "2019-06-20", "label": "US surveillance drone shot down"},
    {"d": "2019-07-19", "label": "Stena Impero seizure"},
    {"d": "2019-09-14", "label": "Abqaiq/Khurais attack"},
    {"d": "2020-01-03", "label": "Soleimani strike"},
    # Saudi Arabia and Russia failed to agree output cuts on the Friday; Riyadh cut
    # prices over the weekend and the market gapped on the Monday. The largest
    # single-day move in the corpus, and it needed the RUS-SAU dyad to be
    # retrievable at all — the earlier hub-and-spoke pull had none.
    {"d": "2020-03-09", "label": "Saudi-Russia price war"},
    {"d": "2022-02-24", "label": "Russia invades Ukraine"},
    {"d": "2022-03-08", "label": "US ban on Russian oil"},
    # The newest supply scare in the corpus, and the instructive one: Brent rose
    # 4.0% and that is only 1.4x a normal day against the 60 days before (1.6x
    # against the whole corpus), because two months of war had already made
    # large oil moves ordinary. Equities rotated hard into
    # defensives the same day, in the middle of earnings season, so the app
    # reports that as coinciding with the event, not caused by it.
    {"d": "2026-10-22", "label": "Iran: seven tankers ablaze, US staff leave Saudi"},
]

# Tariff milestones, for the contrast: these are the biggest days of the trade war
# and they still do not move a series beyond its own normal range.
TARIFF_EPISODES = [
    {"d": "2018-03-22", "label": "Section 301 memo signed"},
    {"d": "2018-07-06", "label": "First $34bn tariffs take effect"},
    {"d": "2019-05-10", "label": "Tariff rate raised to 25%"},
    {"d": "2019-08-23", "label": "China retaliates, US raises again"},
    {"d": "2019-12-13", "label": "Phase One deal announced"},
]

APP_VERSION = "1.8.2"

app = FastAPI(title="GDELT what-if", docs_url=None, redoc_url=None)
_db = None


def database():
    global _db
    if _db is None:
        _db = db()
    return _db


_BIND_RE = re.compile(r"@@?([A-Za-z_][A-Za-z0-9_]*)")


def run(aql: str, **binds):
    """Execute *aql*, passing only the bind variables it actually references.

    ArangoDB rejects a bind parameter the query does not declare, so sharing one
    kwargs dict across queries that need different subsets fails at runtime rather
    than at review. Filtering here keeps the call sites readable.
    """
    wanted = set(_BIND_RE.findall(aql))
    return list(database().aql.execute(
        aql, bind_vars={k: v for k, v in binds.items() if k in wanted}))


class WhatIf(BaseModel):
    cameo: str = "163"
    actorFrom: str = "USA"
    actorTo: str = "CHN"
    horizon: int = 5
    topK: int = 8
    # Optional narrowing by entities named in the coverage. GDELT's actor codes
    # cannot express "the Houthis" — it resolves them to YEM — so without these
    # the finest question askable is "Yemen attacks Saudi Arabia".
    group: str | None = None
    target: str | None = None
    window: str = "delivered"


def verdict(sweep: list[dict]) -> dict:
    """Turn the sweep into a plain-language call on whether the answer is real.

    The test is not "is the largest t big" — it is whether significance SURVIVES
    more evidence. A real effect sharpens as n grows because the standard error
    falls as 1/sqrt(n). Noise wanders: the leading sector changes and |t| decays.
    """
    if len(sweep) < 2:
        return {"call": "unknown", "headline": "Not enough analogs to test.", "detail": ""}

    # The ladder asks for 8, 12, 20, 40 and 80 analogs, but a narrow class runs out
    # and every rung returns the same events. That is not a sweep — nothing was
    # added — yet the checks below would read the identical rows as "the same
    # sector leads at every sample size" and call it detectable. It happened:
    # Houthis + Saudi Aramco exhausts at 8 and returned Real Estate, t = 3.75,
    # "holds up as the sample grows", off one extra observation between the first
    # two rungs. The survival test needs the sample to actually grow.
    rungs = sorted({r["n"] for r in sweep})
    if len(rungs) < 3:
        return {"call": "too-few",
                "headline": f"Too few events to test — the class runs out at "
                            f"{rungs[-1]}.",
                "detail": ("Whether an effect is real is decided here by asking the "
                           "same question of more evidence and watching what happens. "
                           f"This class has only {rungs[-1]} usable events, so there "
                           "is no more evidence to ask for and the test cannot run. "
                           "Any apparent result below is one draw, not a finding.")}

    first, last = sweep[0], sweep[-1]
    sectors = {r["sector"] for r in sweep}
    sig = [r for r in sweep if r["looksSignificant"]]
    stable = len(sectors) == 1
    grew = abs(last["tStat"]) > abs(first["tStat"])

    if stable and grew and sig:
        return {"call": "detectable",
                "headline": f"{last['sector']} — holds up as the sample grows.",
                "detail": (f"The same sector leads at every sample size and the signal "
                           f"strengthens ({first['tStat']:+} → {last['tStat']:+}). "
                           f"That is what a real effect looks like.")}
    if sig and not grew:
        return {"call": "not-detectable",
                "headline": "No detectable effect.",
                "detail": (f"Significant at {sig[0]['n']} analogs, gone by "
                           f"{last['n']}. The leading sector changes "
                           f"{len(sectors)} times across the sweep and the signal "
                           f"decays ({first['tStat']:+} → {last['tStat']:+}). "
                           f"That is the signature of coincidence, not an effect.")}
    return {"call": "not-detectable",
            "headline": "No detectable effect.",
            "detail": (f"Nothing clears the bar at any sample size; the leading "
                       f"sector changes {len(sectors)} times across the sweep.")}


@app.get("/healthz")
def healthz():
    """Cheap liveness + identity. `version` is what lets a deploy verifier prove
    the NEW build is serving rather than the old one — a redeploy that silently
    kept the previous bundle looks identical without it. No document counts here:
    this is polled, and counting 12M rows is not free."""
    from _common import DB_NAME
    return {"ok": True, "version": APP_VERSION, "database": DB_NAME}


@app.get("/api/corpus")
def corpus():
    c = run(Q.CORPUS, colls=COUNTED)[0]
    return c


@app.post("/api/whatif")
def whatif(q: WhatIf):
    if q.horizon not in Q.HORIZONS:
        raise HTTPException(400, f"horizon must be one of {Q.HORIZONS}")
    block = max(7, q.horizon + 2)
    binds = dict(cameo=q.cameo, actorFrom=q.actorFrom, actorTo=q.actorTo,
                 horizon=q.horizon, block=block)
    if q.group:
        binds["group"] = q.group
    if q.target:
        binds["target"] = q.target
    if q.window not in WINDOWS:
        raise HTTPException(400, f"window must be one of {sorted(WINDOWS)}")
    w = WINDOWS[q.window]
    if w["from"]:
        binds["windowFrom"] = w["from"]
    if w["to"]:
        binds["windowTo"] = w["to"]
    aql = lambda t: Q.build(t, group=q.group, target=q.target,
                            window_from=w["from"], window_to=w["to"])

    outcome = run(aql(Q.OUTCOME), topK=q.topK, **binds)
    if not outcome:
        raise HTTPException(404, "No analogs found for that combination.")
    ladder = [k for k in (8, 12, 20, 40, 80) if k <= 200]
    sweep = run(aql(Q.SWEEP), ladder=ladder, **binds)
    graph = run(aql(Q.GRAPH), topK=q.topK, **binds)
    return {"outcome": outcome, "sweep": sweep, "graph": graph,
            "verdict": verdict(sweep),
            "selectedOnCoverage": bool(q.group or q.target),
            "window": q.window, "windowLabel": w["label"],
            "analogs": sorted({g["date"] for g in graph}),
            "portfolio": portfolio_view(
                lambda h, s: P.analog_impact(h, s, [g["date"] for g in graph],
                                             q.horizon))}


def portfolio_view(measure) -> dict | None:
    """The demo portfolio measured over whatever the question measured.

    Optional by design: a database built without a Portfolio still answers every
    question, and the panel simply does not appear.
    """
    pf = P.load_holdings(run)
    if not pf["holdings"]:
        return None
    return {"name": pf["name"], "holdings": pf["holdings"],
            **measure(pf["holdings"], P.load_series(run, pf["holdings"]))}


@app.get("/api/parse")
def parse_question(q: str):
    """A plain-English question read into the form's fields (app/nlq.py)."""
    return N.parse(q[:500], corpus_summary().get("targets", []))


@app.get("/api/portfolio")
def portfolio():
    return P.load_holdings(run)


BASELINES = {"global": "z", "trailing": "zTrailing", "regime": "zRegime"}

# Named corpus windows. `delivered` is the span every originally published number
# and all four demo presets were measured over, and it stays the default so an
# extended corpus never silently changes a published answer. Keys for it carry no suffix, so
# a database built before windows existed still resolves.
WINDOWS = {
    "delivered": {"from": "2018-02-27", "to": "2022-03-31", "suffix": "",
                  "label": "2018-2022, the original window"},
    # No bounds: whatever the corpus holds. The label is filled from the live
    # corpus rather than written here, so it cannot drift from the data.
    "full": {"from": None, "to": None, "suffix": "_full",
             "label": "the whole corpus"},
}


class SingleEvent(BaseModel):
    date: str
    horizon: int = 1
    window: str = "delivered"
    # Which sigma the verdict is measured against. All three are always returned;
    # this only chooses which one decides. Switching silently would move every
    # published number in our own favour, so the others stay on screen.
    baseline: str = "global"


# Tickers as a reader says them. The verdict is prose and goes straight into
# screenshots, so "BZ=F" and "Consumer_Staples" do not belong in it.
SERIES_NAMES = {"CL=F": "WTI crude", "BZ=F": "Brent crude", "GC=F": "Gold",
                "_GSPC": "S&P 500", "_NDX": "Nasdaq-100", "_VIX": "VIX"}


def series_name(target: str) -> str:
    return SERIES_NAMES.get(target, target.replace("_", " "))


def single_verdict(rows: list[dict], date: str) -> dict:
    """Detectability for one event: how far did anything move versus a normal day.

    Three sigma is the line. It is not a significance test — with n=1 there is no
    such thing — it is a statement that the move is outside the range that asset
    produces on its own, which is what "this event did something" has to mean.
    """
    if not rows:
        return {"call": "unknown",
                "headline": f"No price data for {date}.",
                "detail": "Outside the covered window, or not a trading day."}
    top = rows[0]
    # Quote the same number the z-score was computed from. `movePct` is the raw
    # move and `adjustedPct` is net of the market; for a sector ETF the baseline is
    # built from market-adjusted returns, so the raw figure sits beside a z it does
    # not correspond to. On 2018-03-22 the S&P fell 2.52%, which made Utilities
    # read "+0.44%, 2.5 standard deviations" — two true numbers describing
    # different quantities, and the pairing is what misleads.
    def moved(r):
        pct = r["adjustedPct"] if r["marketAdjusted"] else r["movePct"]
        return f"{pct:+}%{' net of the market' if r['marketAdjusted'] else ''}"

    big = [r for r in rows if abs(r["z"]) >= 3]
    if big:
        names = ", ".join(f"{series_name(r['target'])} ({r['z']:+})" for r in big[:3])
        return {"call": "detectable",
                "headline": f"{series_name(top['target'])} moved {moved(top)} — {abs(top['z']):.1f}× a normal day.",
                "detail": (f"{len(big)} series moved beyond three standard deviations of "
                           f"their own behaviour: {names}. A single event this far outside "
                           f"the usual range is measurable without needing an average.")}

    # Between "unmistakable" and "nothing" there is a real third state, and calling
    # it nothing is what gets a demo picked apart. On 2018-03-22 Utilities (+2.55),
    # Real Estate (+2.24) and Consumer Staples (+2.02) all rose together while the
    # S&P fell 2.52% — a coherent defensive rotation. None of it clears three sigma,
    # so the honest report is "something small, below the bar", not "nothing moved".
    # Saying nothing when a reader can see three defensives clustered at the top of
    # the table costs more credibility than the finding is worth.
    near = [r for r in rows if abs(r["z"]) >= 2]
    if len(near) >= 2:
        names = ", ".join(f"{series_name(r['target'])} ({r['z']:+})" for r in near[:4])
        up = sum(1 for r in near if r["z"] > 0)
        shape = ("all in the same direction" if up in (0, len(near))
                 else f"{up} up, {len(near) - up} down")
        return {"call": "below-bar",
                "headline": f"Something small — {len(near)} series past two standard deviations, none past three.",
                "detail": (f"{names} — {shape}. That is a real pattern and it is below "
                           f"our bar: at roughly 17 series tested, two or three readings "
                           f"beyond two standard deviations is close to what an ordinary "
                           f"day produces. We report it rather than calling it nothing, "
                           f"and we do not claim it.")}

    return {"call": "not-detectable",
            "headline": "Nothing moved unusually.",
            "detail": (f"The largest move is {series_name(top['target'])} at {moved(top)}, "
                       f"{abs(top['z']):.1f} standard deviations — inside the range that "
                       f"series produces on an ordinary day.")}


@app.post("/api/event")
def event(q: SingleEvent):
    if q.horizon not in Q.HORIZONS:
        raise HTTPException(400, f"horizon must be one of {Q.HORIZONS}")
    if q.baseline not in BASELINES:
        raise HTTPException(400, f"baseline must be one of {sorted(BASELINES)}")
    if q.window not in WINDOWS:
        raise HTTPException(400, f"window must be one of {sorted(WINDOWS)}")
    w = WINDOWS[q.window]
    rows = run(Q.SINGLE_EVENT, date=q.date, horizon=q.horizon,
               windowId=q.window, windowSuffix=w["suffix"])
    field = BASELINES[q.baseline]
    # Re-rank and judge on the chosen sigma. A row with no conditional baseline —
    # too few observations before it — keeps its unconditional z rather than
    # dropping out, so a switch never silently shortens the table.
    for r in rows:
        r["label"] = series_name(r["target"])
        r["zUsed"] = r.get(field) if r.get(field) is not None else r["z"]
        r["zFellBack"] = r.get(field) is None
    rows.sort(key=lambda r: -abs(r["zUsed"]))
    judged = [{**r, "z": r["zUsed"]} for r in rows]
    ctx = run(Q.EVENT_CONTEXT, date=q.date)
    return {"rows": rows, "graph": ctx,
            "macro": M.day_rows(M.load(run), q.date, q.horizon,
                                w["from"], w["to"]),
            # Who else felt it: Asia, chips, fertilizer, shipping. Beside the
            # answer, never in it — see EXPOSURE in scripts/market_data_pull.py.
            "exposure": M.day_rows(M.load(run, "exposure"), q.date, q.horizon,
                                   w["from"], w["to"]),
            # Why those markets: chokepoint -> good -> who depends on it.
            "paths": X.day_paths(run, q.date),
            "portfolio": portfolio_view(
                lambda h, s: P.day_impact(h, s, q.date, q.horizon)),
            "verdict": single_verdict(judged, q.date),
            "baseline": q.baseline, "date": q.date,
            "window": q.window, "windowLabel": w["label"]}


def corpus_summary() -> dict:
    """Span and named-event count, cached in `CorpusSummary`.

    Both are full scans of 21M documents — the optimiser will not use the
    `dateAdded` index for MIN/MAX, planning EnumerateCollectionNode +
    SortNode — so computing them per request cost 15 seconds a page load.

    Keyed on the live document count, so a rebuild invalidates the cache and the
    next request recomputes. Nobody has to remember to refresh it, which is the
    only reason it is safe to cache a number in a project where every stale
    figure so far came from someone not remembering.
    """
    events = database().collection("Event").count()
    cached = run('RETURN DOCUMENT("CorpusSummary", "latest")')[0]
    # `targets` arrived in 1.6.0; a summary without it is stale, not complete.
    if cached and cached.get("events") == events and "targets" in cached:
        return cached
    span = run("WITH Event LET d = (FOR e IN Event RETURN e.dateAdded) "
               "RETURN {from: MIN(d), to: MAX(d), days: LENGTH(UNIQUE(d))}")[0]
    named = run("WITH Event RETURN LENGTH(FOR e IN Event "
                "FILTER LENGTH(e.perpetrators) > 0 RETURN 1)")[0]
    # The parser's target vocabulary (app/nlq.py). Unscoped it is another full
    # scan — 11 s for 19 names — so it is cached here with the rest.
    targets = sorted(t["name"] for t in entities()["targets"])
    doc = {"_key": "latest", "events": events, "namedEvents": named,
           "targets": targets, **span}
    db_ = database()
    if not db_.has_collection("CorpusSummary"):
        db_.create_collection("CorpusSummary")
    db_.collection("CorpusSummary").insert(doc, overwrite=True)
    return doc


@app.get("/api/about")
def about():
    """Everything the explainer page states, read from the database — so the page
    itself carries no figures in its prose."""
    c = corpus_summary()
    return {"corpus": {"events": c["events"], "from": c.get("from"),
                       "to": c.get("to"), "days": c.get("days")},
            "sweep": run('RETURN DOCUMENT("SweepResult", "latest")')[0],
            "namedEvents": c.get("namedEvents"),
            "windows": windows()["windows"], "version": APP_VERSION}


@app.get("/api/windows")
def windows():
    """Which corpus spans this database actually holds baselines for."""
    have = set(run("FOR b IN BaselineStats RETURN DISTINCT "
                   'NOT_NULL(b.windowId, "delivered")'))
    # The unbounded window's span is a fact about the corpus, so read it from the
    # corpus. Writing it into the table means editing two places on every extension
    # and silently mislabelling the data when only one gets done.
    c = corpus_summary()
    span = {"lo": c.get("from"), "hi": c.get("to")}
    out = []
    for k, v in WINDOWS.items():
        if k not in have:
            continue
        row = {"id": k, **{x: y for x, y in v.items() if x != "suffix"}}
        row["from"] = row["from"] or span["lo"]
        row["to"] = row["to"] or span["hi"]
        if k == "full":
            row["label"] = f"{row['from'][:4]}-{row['to'][:4]}, the whole corpus"
        out.append(row)
    return {"windows": out, "default": "delivered"}


@app.get("/api/entities")
def entities(cameo: str | None = None, actorFrom: str | None = None,
             actorTo: str | None = None):
    """Vocabulary for the two narrowing controls, scoped to the current question.

    Counts are DISTINCT DAYS, not events: the study measures days, so an entity
    spanning 3 days offers 3 observations however many events carry it — and
    scoped this way the number says what that choice leaves to measure HERE.
    """
    binds = {}
    if cameo:
        binds["cameo"] = cameo
    if actorFrom:
        binds["actorFrom"] = actorFrom
    if actorTo:
        binds["actorTo"] = actorTo
    return run(Q.entities(cameo, actorFrom, actorTo), **binds)[0]


@app.get("/api/entity-days")
def entity_days(group: str | None = None, target: str | None = None):
    """Days matching an entity combination — the finder for single-event mode."""
    binds = {}
    if group:
        binds["group"] = group
    if target:
        binds["target"] = target
    return {"days": run(Q.entity_days(group, target), **binds)}


@app.get("/api/episodes")
def episodes():
    """Named events worth asking about — the shocks, plus tariff dates for contrast."""
    return {"oil": OIL_EPISODES, "tariff": TARIFF_EPISODES}


@app.get("/api/oil")
def oil(symbol: str = "CL=F"):
    return {"symbol": symbol,
            "horizons": list(Q.HORIZONS),
            "rows": run(Q.DIRECT_EVENTS, symbol=symbol,
                        episodes=OIL_EPISODES, horizons=list(Q.HORIZONS))}


@app.get("/about")
def about_page():
    return FileResponse(HERE / "static" / "about.html")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    import uvicorn
    print(f"  what-if UI -> http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# What the platform entrypoint serves. Locally this is `app` unchanged; on the
# Arango platform SERVICE_URL_PATH_PREFIX is set and the wrapper strips the mount
# prefix before routing.
from app.prefix import with_prefix  # noqa: E402

asgi_app = with_prefix(app)
