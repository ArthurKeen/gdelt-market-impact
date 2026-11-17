"""Pull a curated GDELT slice over plain HTTP -> data/*.jsonl (stage 1, no creds).

Why this exists alongside bigquery_pull.py: GDELT publishes the raw CSVs at
`data.gdeltproject.org` with **no credentials and no GCP project** — one file per
day for both Events and GKG (v1 daily archive, 2013-04-01 onward). At POC slice
size that is faster to stand up than BigQuery and unblocks the whole pipeline
without a service account. Measured 2026-08-11: ~13.5 MB/day zipped for Events
(206k rows), ~58.5 MB/day for GKG (158k articles), ~10 MB/s throughput.

Emits the **same JSONL contract as bigquery_pull.py** (BigQuery column names), so
build_graph.py is source-agnostic. BigQuery remains the scale path for the full
30-year archive.

Two GDELT quirks this handles, both verified against real files:
  * Daily files are bucketed by DATEADDED, not SQLDATE — an event dated 2008 can
    appear in the 2018-03-22 file (GDELT dates events by content, not publication).
    Both fields are kept.
  * GKG `CAMEOEVENTIDS` is the Event<->article join key: populated on 47% of
    articles, and 97% of the ids it references resolve inside the same-day Events
    file. That join is what attaches Themes/Source/tone to Events. (Note: GKG 2.0
    in BigQuery drops this column — the equivalent there is a join through
    `gdelt-bq.gdeltv2.eventmentions`.)

Limitation: only the v1 daily archive (>= 2013-04-01) is supported. Earlier dates
ship as yearly/monthly bundles with a 57-column layout (no SOURCEURL); those days
are reported and skipped rather than silently dropped.

Examples:
  # E1 tariff corpus: every episode day +/- 2, Events + GKG
  python scripts/gdelt_http_pull.py --scenario e1 --window 2 --actors USA,CHN

  # explicit range, Events only
  python scripts/gdelt_http_pull.py --from 2018-03-20 --to 2018-03-25 --skip-gkg

  # what would it fetch?
  python scripts/gdelt_http_pull.py --scenario e1 --window 1 --dry-run
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import io
import json
import pathlib
import zipfile

from gdelt_vocab import CURATED_THEMES, EPISODES, THEME_GROUPS, compiled_theme_regex

EVENTS_URL = "http://data.gdeltproject.org/events/{ymd}.export.CSV.zip"
GKG_URL = "http://data.gdeltproject.org/gkg/{ymd}.gkg.csv.zip"

# v1 daily archive coverage starts here; before this GDELT ships yearly/monthly.
DAILY_ARCHIVE_START = dt.date(2013, 4, 1)

# GDELT 1.0 Events: 58 tab-separated fields, no header. Index -> our output key
# (output keys mirror the BigQuery column names for a single downstream contract).
EVENT_FIELDS: dict[int, str] = {
    0: "GLOBALEVENTID", 1: "SQLDATE", 2: "MonthYear", 3: "Year",
    5: "Actor1Code", 6: "Actor1Name", 7: "Actor1CountryCode",
    15: "Actor2Code", 16: "Actor2Name", 17: "Actor2CountryCode",
    26: "EventCode", 28: "EventRootCode", 29: "QuadClass",
    30: "GoldsteinScale", 31: "NumMentions", 34: "AvgTone",
    50: "ActionGeo_FullName", 51: "ActionGeo_CountryCode",
    52: "ActionGeo_ADM1Code", 53: "ActionGeo_Lat", 54: "ActionGeo_Long",
    55: "ActionGeo_FeatureID",
    56: "DATEADDED", 57: "SOURCEURL",
}
EVENT_NUMERIC = {"GoldsteinScale", "AvgTone", "ActionGeo_Lat", "ActionGeo_Long"}
EVENT_INT = {"SQLDATE", "MonthYear", "Year", "QuadClass", "NumMentions", "DATEADDED"}

# GDELT 1.0 GKG: 11 fields, header row present. Index -> output key (GKG 2.0 names).
GKG_FIELDS: dict[int, str] = {
    0: "DATE", 2: "Counts", 3: "V2Themes", 4: "V2Locations",
    5: "V2Persons", 6: "V2Organizations", 7: "V2Tone",
    8: "CAMEOEVENTIDS", 9: "SourceCommonName", 10: "DocumentIdentifier",
}


def _dates_from_args(args) -> list[dt.date]:
    """Resolve --scenario/--window, --from/--to, --dates into a sorted day list."""
    days: set[dt.date] = set()
    if args.scenarios:
        for name in args.scenarios.split(","):
            key = name.strip().lower()
            if key not in EPISODES:
                raise SystemExit(f"unknown scenario {key!r}; known: {sorted(EPISODES)}")
            for iso, _label in EPISODES[key]:
                anchor = dt.date.fromisoformat(iso)
                for off in range(-args.window, args.window + 1):
                    days.add(anchor + dt.timedelta(days=off))
    if args.start and args.end:
        cur, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
        while cur <= end:
            days.add(cur)
            cur += dt.timedelta(days=1)
    if args.dates:
        days.update(dt.date.fromisoformat(d.strip()) for d in args.dates.split(","))
    if not days:
        raise SystemExit("nothing to pull: pass --scenario, --from/--to, or --dates")
    return sorted(days)


def _fetch_zip_lines(url: str, *, timeout: int = 300):
    """Download a GDELT zip and yield its member's decoded lines. None on 404."""
    import requests

    resp = requests.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    name = zf.namelist()[0]
    # GDELT CSVs carry occasional invalid bytes; replace rather than abort a day.
    with zf.open(name) as fh:
        for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
            yield raw.rstrip("\n")


def _num(value: str, key: str):
    if value == "":
        return None
    try:
        return int(value) if key in EVENT_INT else float(value)
    except ValueError:
        return None


def _actor_match(row: dict, actors: list[str], dyad_only: bool) -> bool:
    """Actor codes match by prefix (USA matches USAGOV); country codes exactly."""
    if not actors:
        return True
    a1, a2 = row.get("Actor1Code") or "", row.get("Actor2Code") or ""
    c1, c2 = row.get("Actor1CountryCode") or "", row.get("Actor2CountryCode") or ""
    if dyad_only:
        if len(actors) < 2:
            raise SystemExit("--dyad-only needs at least two --actors")
        # Both sides must be in the set, and they must differ. The earlier rule
        # treated actors[0] as a hub and required the other side to come from the
        # rest, which silently excluded every pair that did not involve it: a
        # six-country pull returned 2,637,001 events and ZERO without the US, so
        # China<->Russia and Iran<->Israel — the entanglement the actor set was
        # widened for — could never match. `--dyad-hub` keeps the old behaviour.
        def side(a, c):
            return next((o for o in actors if a.startswith(o) or c == o), None)
        s1, s2 = side(a1, c1), side(a2, c2)
        return s1 is not None and s2 is not None and s1 != s2
    return any(
        a1.startswith(code) or a2.startswith(code) or c1 == code or c2 == code
        for code in actors
    )


def pull_events_day(day: dt.date, actors: list[str], dyad_only: bool) -> list[dict]:
    lines = _fetch_zip_lines(EVENTS_URL.format(ymd=day.strftime("%Y%m%d")))
    if lines is None:
        return []
    out = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 58:
            continue
        row = {key: parts[idx] for idx, key in EVENT_FIELDS.items()}
        if not _actor_match(row, actors, dyad_only):
            continue
        for key in EVENT_NUMERIC | EVENT_INT:
            row[key] = _num(row[key], key)
        row["_pullDate"] = day.isoformat()
        out.append(row)
    return out


def pull_gkg_day(day: dt.date, theme_re, event_ids: set[str] | None,
                 curated: set[str]) -> list[dict]:
    """Theme-matching GKG articles for one day.

    If `event_ids` is given, an article is kept only when its CAMEOEVENTIDS
    reference at least one Event we actually kept. That is the only GKG we ever
    traverse (it is what carries Themes/Source/tone onto Events), and it is the
    difference between ~38k articles/day and a few hundred — the POC corpus stays
    a corpus instead of a firehose. Pass --all-gkg to keep every theme match.
    """
    lines = _fetch_zip_lines(GKG_URL.format(ymd=day.strftime("%Y%m%d")))
    if lines is None:
        return []
    out = []
    for lineno, line in enumerate(lines):
        if lineno == 0:  # header
            continue
        parts = line.split("\t")
        if len(parts) < 11:
            continue
        if not theme_re.search(parts[3]):
            continue
        linked = [i for i in parts[8].split(",") if i]
        if event_ids is not None:
            linked = [i for i in linked if i in event_ids]
            if not linked:
                continue
        row = {key: parts[idx] for idx, key in GKG_FIELDS.items()}
        row["GKGRECORDID"] = f"{day.strftime('%Y%m%d')}-{lineno}"
        row["_pullDate"] = day.isoformat()
        row["_linkedEventIds"] = linked
        # Resolve matched curated tokens once here so build_graph need not re-parse.
        row["_matchedThemes"] = sorted(
            {t.split(",")[0] for t in parts[3].split(";") if t} & curated
        )
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", dest="scenarios", default=None,
                    help=f"comma-separated episode calendars: {','.join(EPISODES)}")
    ap.add_argument("--window", type=int, default=2,
                    help="days either side of each episode date (default 2)")
    ap.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD")
    ap.add_argument("--dates", default=None, help="explicit comma-separated YYYY-MM-DD")
    ap.add_argument("--actors", default="USA,CHN",
                    help="actor/country codes, prefix-matched; empty string = no filter")
    ap.add_argument("--dyad-hub", action="store_true",
                    help="legacy: require actors[0] on one side (hub-and-spoke) "
                         "rather than any two distinct actors from the set")
    ap.add_argument("--dyad-only", action="store_true",
                    help="require first --actors code on one side and another on the other")
    ap.add_argument("--theme-group", dest="groups", default=None,
                    help=f"restrict GKG themes to groups: {','.join(THEME_GROUPS)}")
    ap.add_argument("--skip-gkg", action="store_true", help="Events only (much faster)")
    ap.add_argument("--all-gkg", action="store_true",
                    help="keep every theme-matching article, not only those linked "
                         "to a kept Event (much larger; see pull_gkg_day)")
    ap.add_argument("--out", default="data")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="list target URLs only")
    args = ap.parse_args()

    days = _dates_from_args(args)
    too_early = [d for d in days if d < DAILY_ARCHIVE_START]
    days = [d for d in days if d >= DAILY_ARCHIVE_START]
    if too_early:
        print(f"[gdelt_http] skipping {len(too_early)} day(s) before "
              f"{DAILY_ARCHIVE_START} (pre-daily-archive: {too_early[0]}..{too_early[-1]}) "
              f"— those need the yearly/monthly bundles (57-col layout)")
    if not days:
        raise SystemExit("no fetchable days remain")

    actors = [a.strip() for a in args.actors.split(",") if a.strip()]
    themes = None
    if args.groups:
        themes = []
        for g in args.groups.split(","):
            key = g.strip().lower()
            if key not in THEME_GROUPS:
                raise SystemExit(f"unknown theme group {key!r}; known: {sorted(THEME_GROUPS)}")
            themes += THEME_GROUPS[key]
    theme_re = compiled_theme_regex(themes)

    if args.dry_run:
        print(f"=== {len(days)} day(s): {days[0]} .. {days[-1]} ===")
        for d in days:
            print("  " + EVENTS_URL.format(ymd=d.strftime('%Y%m%d')))
            if not args.skip_gkg:
                print("  " + GKG_URL.format(ymd=d.strftime('%Y%m%d')))
        print(f"=== actors={actors or '(none)'} dyad_only={args.dyad_only} ===")
        print(f"=== theme regex ===\n{theme_re.pattern}")
        return 0

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    def run(label, fn, path, dedup_key):
        """Fetch all days concurrently, dedupe on dedup_key, write JSONL.

        Returns the set of kept keys (the Event ids feed the GKG link filter).
        """
        seen, written = set(), 0
        with path.open("w", encoding="utf-8") as fh, \
                concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fn, d): d for d in days}
            for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                day = futures[fut]
                try:
                    rows = fut.result()
                # Deliberately broad: one bad day (transient HTTP, truncated zip)
                # must not lose the other 111. Failures are named, never silent.
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{label}] {day} FAILED: {type(exc).__name__}: {exc}")
                    continue
                kept = 0
                for row in rows:
                    key = row.get(dedup_key)
                    if key in seen:
                        continue
                    seen.add(key)
                    fh.write(json.dumps(row) + "\n")
                    written += 1
                    kept += 1
                print(f"  [{label}] {day}: {len(rows):,} matched, {kept:,} new "
                      f"({done}/{len(days)} days)")
        print(f"[gdelt_http] {label} -> {path} ({written:,} rows)")
        return seen

    event_ids = run("events", lambda d: pull_events_day(d, actors, args.dyad_only),
                    outdir / "events.jsonl", "GLOBALEVENTID")
    gkg_ids = set()
    if not args.skip_gkg:
        link_set = None if args.all_gkg else event_ids
        curated = set(themes) if themes else set(CURATED_THEMES)
        gkg_ids = run("gkg", lambda d: pull_gkg_day(d, theme_re, link_set, curated),
                      outdir / "gkg.jsonl", "GKGRECORDID")
    print(f"[gdelt_http] done: {len(event_ids):,} events + {len(gkg_ids):,} gkg articles "
          f"over {len(days)} day(s)"
          + ("" if args.all_gkg or args.skip_gkg else " (gkg linked to kept events)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
