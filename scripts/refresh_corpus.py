#!/usr/bin/env python3
"""Bring the corpus up to date: pull the days since it ends, rebuild what depends on them.

Manual, on purpose. Answers here are read from a decade of history, so a month of
new days barely moves any of them, and the refresh changes published figures — the
sweep and the full-window baselines — which somebody then has to carry into the
documents. That is a decision to take deliberately, not on a timer.

It is the 2026-09-29 extension, scripted. Every stage is the command that was run
then, in the order it was run, with the three traps from that run checked rather
than remembered (docs/operations.md):

  * the GDELT pull exits 0 with days missing — failed and empty days are retried,
    and the refresh stops if any are still missing;
  * the price file has to cover the new span — prices are re-pulled to the end
    date and checked;
  * an un-rerun derived step leaves the old corpus's statistics in place — every
    step that reads the corpus runs again, and tests/test_graph.py gates the end.

Stages, each logged to its own file in the run directory:

  pull       new GDELT days -> <run>/pull          (no credentials; ~10 MB/s)
  prices     yfinance 2013-01-01 .. end            (no key)
  merge      data/full + new days -> data/next     (streams; ~36 GB of disk)
  load       build_graph --via-arangoimport        (upsert; the service keeps serving)
  edges      correlatesWith/affects, both windows' baselines
  baselines  conditional baselines, both windows
  entities   who acted and what was hit, from the coverage
  exposure   which chokepoint region each new location falls in
  sweep      the multiple-testing sweep -> SweepResult/latest
  verify     corpus summary, tests/test_graph.py, headline figures vs the register
  rotate     data/next -> data/full, the old one kept as data/full.prev

The load is an upsert, never a drop: the new corpus is a strict superset and every
edge carries a deterministic key, so the live service answers throughout. The
app needs no restart: it reads everything from the database.

A full run takes hours, almost all of it the load. Start it detached:

    python scripts/refresh_corpus.py --dry-run                  # plan only
    python scripts/_detach.py data/refresh.log python scripts/refresh_corpus.py
    python scripts/refresh_corpus.py --resume data/refresh/<run>   # after a stop

`verify` exits non-zero if a PINNED figure moved — a delivered-window number or a
raw price move that a refresh has no business changing. Rotation does not happen
then; find out why first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

PY = sys.executable
# The actor set every pull since 2026-08-24 has used: the six original countries
# plus the five regional ones that make proxy conflicts visible (Abqaiq is YEM->SAU).
ACTORS = "USA,CHN,IRN,SAU,RUS,ISR,YEM,LBN,SYR,IRQ,PSE"
PRICES_FROM = "2013-01-01"
STAGES = ["pull", "prices", "merge", "load", "edges", "baselines",
          "entities", "exposure", "sweep", "verify", "rotate"]
RETRIES = 2
# GDELT publishes a day's file the morning after; two days back is always there.
SETTLE_DAYS = 2

_DAY_LINE = re.compile(r"\[(events|gkg)\] (\d{4}-\d{2}-\d{2})(?: FAILED|: ([\d,]+) matched)")


def log(msg: str) -> None:
    print(f"[refresh {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --- planning ----------------------------------------------------------------

def plan_window(corpus_to: str, today: dt.date, until: str | None) -> tuple[str, str]:
    """The days to pull: the one after the corpus ends, through `until`."""
    start = dt.date.fromisoformat(corpus_to) + dt.timedelta(days=1)
    end = (dt.date.fromisoformat(until) if until
           else today - dt.timedelta(days=SETTLE_DAYS))
    return start.isoformat(), end.isoformat()


def missing_days(pull_log: str, start: str, end: str) -> list[str]:
    """Days the pull failed or returned nothing for, from its own log.

    Both count. FAILED is a transient HTTP error; zero matched means GDELT served
    an empty or absent file, and with eleven actors including the US no real day
    is empty. Either way the day is missing and would go unnoticed in the summary.
    """
    lo, hi = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    want = {(lo + dt.timedelta(days=i)).isoformat() for i in range((hi - lo).days + 1)}
    good: dict[str, set[str]] = {"events": set(), "gkg": set()}
    for m in _DAY_LINE.finditer(pull_log):
        label, day, n = m.groups()
        if n is not None and int(n.replace(",", "")) > 0:
            good[label].add(day)
    return sorted(want - (good["events"] & good["gkg"]))


# --- running -----------------------------------------------------------------

def sh(stage_log: pathlib.Path, *cmd: str) -> str:
    """Run a step, teeing its output to the stage log and to ours. Raises on failure."""
    log("$ " + " ".join(cmd))
    out = []
    with stage_log.open("a", encoding="utf-8") as fh:
        p = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            fh.write(line)
            out.append(line)
            print("    " + line, end="", flush=True)
        if p.wait() != 0:
            raise SystemExit(f"step failed (exit {p.returncode}) — see {stage_log}")
    return "".join(out)


def append_jsonl(src: pathlib.Path, dest: pathlib.Path) -> None:
    """Retried days go on the end. Duplicates are fine: the merge keys on id."""
    if src.exists():
        with dest.open("a", encoding="utf-8") as out, src.open(encoding="utf-8") as fh:
            shutil.copyfileobj(fh, out)


class Run:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.state_file = path / "state.json"
        self.state = json.loads(self.state_file.read_text())

    @classmethod
    def create(cls, root: pathlib.Path, **state) -> "Run":
        path = root / f"{state['from']}_{state['to']}"
        if path.exists():
            raise SystemExit(f"{path} exists — pass --resume {path} to continue it")
        path.mkdir(parents=True)
        (path / "state.json").write_text(json.dumps({**state, "done": []}, indent=2))
        return cls(path)

    def done(self, stage: str) -> bool:
        return stage in self.state["done"]

    def mark(self, stage: str) -> None:
        self.state["done"].append(stage)
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def stage_log(self, stage: str) -> pathlib.Path:
        return self.path / f"{stage}.log"


# --- stages ------------------------------------------------------------------

def stage_pull(run: Run) -> None:
    s, out = run.state, run.path / "pull"
    text = sh(run.stage_log("pull"), PY, "scripts/gdelt_http_pull.py",
              "--from", s["from"], "--to", s["to"], "--actors", ACTORS,
              "--dyad-only", "--out", str(out), "--workers", "6")
    missing = missing_days(text, s["from"], s["to"])
    for attempt in range(1, RETRIES + 1):
        if not missing:
            break
        log(f"{len(missing)} day(s) missing, retry {attempt}: {', '.join(missing)}")
        retry = run.path / f"retry{attempt}"
        text = sh(run.stage_log("pull"), PY, "scripts/gdelt_http_pull.py",
                  "--dates", ",".join(missing), "--actors", ACTORS,
                  "--dyad-only", "--out", str(retry), "--workers", "3")
        for name in ("events.jsonl", "gkg.jsonl"):
            append_jsonl(retry / name, out / name)
        missing = sorted(set(missing) & set(
            missing_days(text, min(missing), max(missing))))
    if missing and not s.get("acceptMissing"):
        raise SystemExit(
            f"still missing after {RETRIES} retries: {', '.join(missing)}. Re-run "
            f"with --resume {run.path} later, or add --accept-missing to go on "
            f"without them (they can be pulled into a later refresh).")
    s["missingDays"] = missing


def stage_prices(run: Run) -> None:
    s = run.state
    dest = run.path / "pull" / "prices.jsonl"
    end = (dt.date.fromisoformat(s["to"]) + dt.timedelta(days=1)).isoformat()
    sh(run.stage_log("prices"), PY, "scripts/market_data_pull.py",
       "--from", PRICES_FROM, "--to", end, "--out", str(dest))
    rows = [json.loads(ln) for ln in dest.open(encoding="utf-8") if ln.strip()]
    old = [json.loads(ln) for ln in (REPO / s["base"] / "prices.jsonl").open()
           if ln.strip()]
    first, last = min(r["date"] for r in rows), max(r["date"] for r in rows)
    lost = {r["symbol"] for r in old} - {r["symbol"] for r in rows}
    log(f"prices: {len(rows):,} rows {first} .. {last} (was {len(old):,})")
    if lost:
        raise SystemExit(f"price pull lost series {sorted(lost)} — yfinance hiccup? "
                         f"re-run with --resume {run.path}")
    if first > "2013-04-01" or last < (dt.date.fromisoformat(s["to"])
                                       - dt.timedelta(days=7)).isoformat():
        raise SystemExit(f"prices span {first}..{last} does not cover the corpus "
                         f"{s['corpusFrom']}..{s['to']}")


def stage_merge(run: Run) -> None:
    s = run.state
    nxt = REPO / s["next"]
    if nxt.exists():
        shutil.rmtree(nxt)                   # a half-written merge from a stopped run
    sh(run.stage_log("merge"), PY, "scripts/merge_corpus.py",
       "--continuous", str(run.path / "pull"), "--existing", s["base"],
       "--out", s["next"], "--from", s["from"], "--to", s["to"], "--no-backup")
    shutil.copy2(run.path / "pull" / "prices.jsonl", nxt / "prices.jsonl")


def stage_load(run: Run) -> None:
    sh(run.stage_log("load"), PY, "scripts/build_graph.py", "--data", run.state["next"],
       "--via-arangoimport", "--import-threads", "8", "--replication-factor", "2")


def stage_edges(run: Run) -> None:
    d = run.state["next"]
    sh(run.stage_log("edges"), PY, "scripts/build_inferred_edges.py",
       "--data", d, "--window", "delivered")
    sh(run.stage_log("edges"), PY, "scripts/build_inferred_edges.py",
       "--data", d, "--window", "full", "--baselines-only")


def stage_baselines(run: Run) -> None:
    for window in ("delivered", "full"):
        sh(run.stage_log("baselines"), PY, "scripts/build_conditional_baselines.py",
           "--data", run.state["next"], "--window", window)


def stage_entities(run: Run) -> None:
    sh(run.stage_log("entities"), PY, "scripts/extract_entities.py",
       "--data", run.state["next"])


def stage_exposure(run: Run) -> None:
    # New days bring new Locations; which chokepoint region each falls in is
    # computed, so it has to be recomputed. The curated half is idempotent.
    sh(run.stage_log("exposure"), PY, "scripts/build_exposure_graph.py")


def stage_sweep(run: Run) -> None:
    sh(run.stage_log("sweep"), PY, "scripts/multiple_testing.py")


def stage_verify(run: Run) -> None:
    from app.main import corpus_summary
    c = corpus_summary()                     # recomputes: the event count changed
    log(f"corpus now {c['events']:,} events, {c['from']} .. {c['to']}, {c['days']:,} days")
    if c["to"] < run.state["from"]:
        raise SystemExit(f"corpus still ends {c['to']} — the load did not land")
    sh(run.stage_log("verify"), PY, "-m", "pytest", "-q", "tests/test_graph.py")
    report = run.path / "figures.json"
    p = subprocess.run([PY, "scripts/headline_figures.py", "--report", str(report)],
                       cwd=REPO, text=True, capture_output=True)
    with run.stage_log("verify").open("a") as fh:
        fh.write(p.stdout + p.stderr)
    print(p.stdout, end="", flush=True)
    if p.returncode == 2:
        raise SystemExit("a PINNED figure moved — see above. Not rotating; find out "
                         "why before touching any document.")
    if p.returncode:
        raise SystemExit(f"headline_figures failed:\n{p.stderr}")


def stage_rotate(run: Run) -> None:
    base, nxt = REPO / run.state["base"], REPO / run.state["next"]
    prev = base.with_name(base.name + ".prev")
    if prev.exists():
        shutil.rmtree(prev)
    base.rename(prev)
    nxt.rename(base)
    log(f"{run.state['next']} is now {run.state['base']}; the old corpus is {prev.name} "
        f"— delete it once you are happy with the refresh")


STAGE_FN = {name: globals()[f"stage_{name}"] for name in STAGES}


# --- main --------------------------------------------------------------------

def preflight(base: pathlib.Path) -> None:
    from _common import DB_NAME, env
    log(f"target: {env('ARANGO_ENDPOINT')}  database {DB_NAME}")
    if not (base / "events.jsonl").exists():
        raise SystemExit(f"{base} holds no corpus — pass --base")
    need = sum(f.stat().st_size for f in base.glob("*.jsonl")) * 1.15
    free = shutil.disk_usage(base).free
    log(f"disk: {free / 1e9:,.0f} GB free, the merge needs ~{need / 1e9:,.0f} GB")
    if free < need:
        raise SystemExit("not enough disk for the merge")
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        raise SystemExit("docker is not running — the load uses arangoimport in Docker")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="data/full", help="the corpus the database was built from")
    ap.add_argument("--to", dest="until", default=None,
                    help=f"last day to pull (default: today minus {SETTLE_DAYS})")
    ap.add_argument("--resume", default=None, help="continue a stopped run directory")
    ap.add_argument("--accept-missing", action="store_true",
                    help="carry on if some days are still missing after retries")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    args = ap.parse_args()
    os.chdir(REPO)

    if args.resume:
        run = Run(pathlib.Path(args.resume))
        if args.accept_missing:
            run.state["acceptMissing"] = True
        log(f"resuming {run.path}: done {run.state['done'] or 'nothing'}")
    else:
        from app.main import corpus_summary
        c = corpus_summary()
        start, end = plan_window(c["to"], dt.date.today(), args.until)
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
        log(f"corpus: {c['events']:,} events, {c['from']} .. {c['to']}")
        if days < 1:
            log(f"nothing to do — the corpus already reaches {c['to']}")
            return 0
        log(f"plan: pull {start} .. {end} ({days} days), then "
            + " -> ".join(STAGES))
        preflight(REPO / args.base)
        if args.dry_run:
            return 0
        run = Run.create(REPO / "data" / "refresh", **{
            "from": start, "to": end, "corpusFrom": c["from"],
            "eventsBefore": c["events"], "base": args.base, "next": "data/next",
            "acceptMissing": args.accept_missing,
            "started": dt.datetime.now().isoformat(timespec="seconds")})

    for stage in STAGES:
        if run.done(stage):
            continue
        log(f"=== {stage} ===")
        t0 = time.time()
        STAGE_FN[stage](run)
        run.mark(stage)
        log(f"{stage} done in {(time.time() - t0) / 60:.1f} min")

    log("refresh complete. Next: carry the moved figures (listed above, also in "
        f"{run.path / 'figures.json'}) into the documents, then accept them with "
        "`python scripts/headline_figures.py --write`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
