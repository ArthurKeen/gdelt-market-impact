#!/usr/bin/env python3
"""Every figure the prose quotes, recomputed from the live database.

Every stale number in this project came from writing a figure into prose and then
changing the data underneath it. The service already reads its own figures from
the database (`CorpusSummary`, `SweepResult`); the documents cannot, so this is
the next best thing: a register of what they quote, `docs/headline-figures.json`,
and a check that says which ones moved and which lines still carry the old value.

Single-event figures go through `app.main.event` — the code path the UI uses — so
a figure here cannot disagree with what a reader sees on screen.

What a corpus refresh can and cannot move:
  * corpus size, span, entity counts, the sweep — always move;
  * full-window z-scores — move a little, because the baseline gains new days;
  * delivered-window figures and raw price moves — should NOT move. The window is
    pinned at 2018-02-27..2022-03-31 and a past close is a past close. If one of
    these changes, stop and find out why before editing any document.

    python scripts/headline_figures.py            # compare live vs register
    python scripts/headline_figures.py --write    # accept live values into the register
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
REGISTER = REPO / "docs" / "headline-figures.json"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

# Where a stale figure could be sitting. FROZEN paths are skipped on purpose:
# a document that records what was true on the day it was written belongs here.
# Add your own, comma-separated, in .env: HEADLINE_FROZEN=report-2025,summary-v1
from _common import env  # noqa: E402  (also loads .env)

SEARCH_PATHS = ("README.md", "docs", "app/static")
FROZEN = ("vendor/", "headline-figures.json") + tuple(
    p.strip() for p in (env("HEADLINE_FROZEN") or "").split(",") if p.strip())

# (id, what it is, date, horizon, window, baseline, target, field, pinned)
# `pinned` marks figures a refresh must not move — see the module docstring.
EVENT_FIGURES = [
    ("abqaiq.wti.move", "Abqaiq: WTI move, first session", "2019-09-16", 1,
     "delivered", "global", "CL=F", "movePct", True),
    ("abqaiq.wti.z", "Abqaiq: WTI z, delivered window", "2019-09-16", 1,
     "delivered", "global", "CL=F", "z", True),
    ("abqaiq.brent.z", "Abqaiq: Brent z, delivered window", "2019-09-16", 1,
     "delivered", "global", "BZ=F", "z", True),
    ("pricewar.wti.move", "Price war: WTI move", "2020-03-09", 1,
     "delivered", "global", "CL=F", "movePct", True),
    ("pricewar.wti.z.full", "Price war: WTI z, full window", "2020-03-09", 1,
     "full", "global", "CL=F", "z", False),
    ("opec2014.energy.move", "OPEC Nov 2014: Energy move", "2014-11-28", 1,
     "full", "global", "Energy", "movePct", True),
    ("opec2014.energy.z.full", "OPEC Nov 2014: Energy z, full window", "2014-11-28", 1,
     "full", "global", "Energy", "z", False),
    ("opec2014.brent.move", "OPEC Nov 2014: Brent move", "2014-11-28", 1,
     "full", "global", "BZ=F", "movePct", True),
    ("opec2014.wti.move", "OPEC Nov 2014: WTI move", "2014-11-28", 1,
     "full", "global", "CL=F", "movePct", True),
]


def _fmt(fig_id: str, value) -> list[str]:
    """The spellings a figure takes in prose, for finding the stale ones."""
    if fig_id == "sweep.survivors":
        return []                                 # "0" matches everything
    if fig_id == "corpus.to":
        return [value, dt.date.fromisoformat(value).strftime("%B %Y")]
    if fig_id == "corpus.events":                 # prose rounds it
        return [f"{value:,}", f"{value / 1e6:.1f} million",
                f"{round(value / 1e6)} million", f"{round(value / 1e6)}M"]
    if isinstance(value, str):
        return [value]
    if fig_id.endswith(".z.full") and fig_id.startswith("opec"):
        return [f"{abs(value):.1f}×"]                     # "Energy -6.42% (4.4×)"
    if fig_id in ("sweep.missesBarBy",):
        return [f"{value:.1f}×"]
    if fig_id == "corpus.documentsM":
        return [f"{value:.1f}M"]
    if isinstance(value, int):
        return [f"{value:,}"]
    return [f"{abs(value):.2f}"]


def compute() -> dict:
    from app.main import SingleEvent, corpus_summary, database, event, run

    figs: dict[str, dict] = {}

    def put(fig_id, label, value, pinned=False):
        figs[fig_id] = {"label": label, "value": value, "pinned": pinned,
                        "spellings": _fmt(fig_id, value)}

    cache: dict[tuple, list[dict]] = {}
    for fig_id, label, date, h, window, baseline, target, field, pinned in EVENT_FIGURES:
        k = (date, h, window, baseline)
        if k not in cache:
            cache[k] = event(SingleEvent(date=date, horizon=h, window=window,
                                         baseline=baseline))["rows"]
        row = next((r for r in cache[k] if r["target"] == target), None)
        if row is None:
            raise SystemExit(f"{fig_id}: no {target} row for {date} — the corpus "
                             f"no longer covers a documented event")
        value = row["zUsed"] if field == "z" else row[field]
        put(fig_id, label, round(value, 2), pinned)

    c = corpus_summary()
    put("corpus.events", "Events in the corpus", c["events"])
    put("corpus.days", "Days in the corpus", c["days"])
    put("corpus.from", "First day", c["from"])
    put("corpus.to", "Last day", c["to"])
    put("corpus.namedEvents", "Events whose coverage names a group", c["namedEvents"])
    db = database()
    docs = sum(db.collection(col["name"]).count()
               for col in db.collections() if not col["name"].startswith("_"))
    put("corpus.documentsM", "Documents in the database (millions)", round(docs / 1e6, 1))

    s = run('RETURN DOCUMENT("SweepResult", "latest")')[0] or {}
    put("sweep.tests", "Class questions tested", s.get("tests"))
    put("sweep.clearedLoose", "Tests clearing |t| >= 2.5", s.get("clearedLoose"))
    put("sweep.clearedLoosePct", "Share clearing |t| >= 2.5 (%)", s.get("clearedLoosePct"))
    put("sweep.survivors", "Survivors after correction", s.get("survivors"))
    put("sweep.missesBarBy", "Best result misses the corrected bar by", s.get("missesBarBy"))
    return figs


def stale_lines(spelling: str) -> list[str]:
    """file:line for every non-frozen mention of `spelling`."""
    out = subprocess.run(["grep", "-rnIF", "--", spelling, *SEARCH_PATHS],
                         cwd=REPO, capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines()
            if not any(f in ln.split(":", 1)[0] for f in FROZEN)]


def compare(old: dict, new: dict) -> list[dict]:
    changes = []
    for fig_id, fig in new.items():
        prev = old.get(fig_id)
        if prev is None or prev["value"] == fig["value"]:
            continue
        lines = [ln for sp in prev["spellings"] for ln in stale_lines(sp)]
        changes.append({"id": fig_id, "label": fig["label"], "old": prev["value"],
                        "new": fig["value"], "pinned": fig["pinned"], "lines": lines})
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="accept the live values into docs/headline-figures.json")
    ap.add_argument("--report", default=None,
                    help="also write the comparison as JSON to this path")
    args = ap.parse_args()

    new = compute()
    register = json.loads(REGISTER.read_text()) if REGISTER.exists() else {}
    changes = compare(register.get("figures", {}), new)

    if not register:
        print("  no register yet — run with --write to create it")
    elif not changes:
        print(f"  all {len(new)} figures match the register "
              f"(as of {register.get('asOf')})")
    for ch in changes:
        flag = "  ** PINNED — investigate before editing anything **" if ch["pinned"] else ""
        print(f"\n  {ch['label']}: {ch['old']} -> {ch['new']}{flag}")
        for ln in ch["lines"] or ["(not quoted anywhere outside frozen documents)"]:
            print(f"      {ln[:160]}")
    if args.report:
        pathlib.Path(args.report).write_text(json.dumps(changes, indent=2) + "\n")

    if args.write:
        REGISTER.write_text(json.dumps({
            "asOf": dt.date.today().isoformat(),
            "corpusTo": new["corpus.to"]["value"],
            "figures": new}, indent=2, ensure_ascii=False) + "\n")
        print(f"\n  register written: {REGISTER.relative_to(REPO)}")
    pinned_moved = [c for c in changes if c["pinned"]]
    return 2 if pinned_moved else 0


if __name__ == "__main__":
    raise SystemExit(main())
