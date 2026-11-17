"""Pull a curated GDELT slice from BigQuery -> local JSONL (ingest, stage 1).

GDELT v2 public dataset: `gdelt-bq.gdeltv2.events` and `gdelt-bq.gdeltv2.gkg`.
For a curated (actor + theme + date) slice this queries BigQuery directly and
streams rows to `data/events.jsonl` + `data/gkg.jsonl` — no GCS staging needed
at this scale. (For the full 30-yr archive, switch to an EXPORT DATA -> GCS step.)

Structural note: this stage + build_graph.py are the *mechanical* half
(tables -> collections, FK -> edges), kept declarative so it can be swapped for a
generic relational-to-graph loader.

Auth: set GOOGLE_APPLICATION_CREDENTIALS (+ GOOGLE_CLOUD_PROJECT) in .env.
Use --dry-run to print the compiled SQL + params without hitting BigQuery.

Examples:
  python scripts/bigquery_pull.py --from 2016-01 --to 2024-12 \
      --actors USAGOV,CHNGOV --limit 200000 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib

from dotenv import load_dotenv
from gdelt_vocab import CURATED_THEMES, theme_regex

load_dotenv()

# Curated themes live in gdelt_vocab.py (validated against real GKG data — the
# original list included tokens that do not exist and one that matches 81%
# of all articles; see that module's header).
DEFAULT_THEMES = CURATED_THEMES

EVENTS_SQL = """
SELECT GLOBALEVENTID, SQLDATE, MonthYear, Year,
       Actor1Code, Actor1Name, Actor1CountryCode,
       Actor2Code, Actor2Name, Actor2CountryCode,
       EventCode, EventRootCode, QuadClass,
       GoldsteinScale, NumMentions, AvgTone,
       ActionGeo_FullName, ActionGeo_CountryCode,
       ActionGeo_ADM1Code, ActionGeo_Lat, ActionGeo_Long, ActionGeo_FeatureID,
       DATEADDED, SOURCEURL
FROM `gdelt-bq.gdeltv2.events`
WHERE SQLDATE BETWEEN @start AND @end
  AND ( Actor1Code IN UNNEST(@actors)
     OR Actor2Code IN UNNEST(@actors)
     OR Actor1CountryCode IN UNNEST(@actors)
     OR Actor2CountryCode IN UNNEST(@actors) )
{limit}
"""

# NOTE (source parity): GKG 2.0 drops the v1 `CAMEOEVENTIDS` column, which is the
# article<->Event join key the HTTP path uses to attach Themes/tone to Events. The
# BigQuery equivalent is a join through `gdelt-bq.gdeltv2.eventmentions`
# (MentionIdentifier = DocumentIdentifier -> GLOBALEVENTID). build_graph.py falls
# back to parsing CAMEOEVENTIDS/V2Themes itself, so rows without it still load —
# they just carry no Event->Theme edges until that join is added here.
GKG_SQL = """
SELECT GKGRECORDID, DATE, SourceCommonName, DocumentIdentifier,
       V2Themes, V2Tone, V2Locations, V2Persons, V2Organizations
FROM `gdelt-bq.gdeltv2.gkg`
WHERE DATE BETWEEN @gkgstart AND @gkgend
  AND REGEXP_CONTAINS(V2Themes, @themeRegex)
{limit}
"""


def _yyyymm_to_sqldate(ym: str, *, end: bool) -> int:
    """'2016-01' -> 20160101 (start) or 20160131 (end, coarse)."""
    y, m = ym.split("-")
    return int(f"{y}{m}{'31' if end else '01'}")


def _yyyymm_to_gkgdate(ym: str, *, end: bool) -> int:
    """'2016-01' -> 20160101000000 (start) or 20160131235959 (end)."""
    y, m = ym.split("-")
    return int(f"{y}{m}{'31235959' if end else '01000000'}")


def _write_jsonl(rows, path: pathlib.Path) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), default=str) + "\n")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM")
    ap.add_argument("--actors", default="USAGOV,CHNGOV", help="comma-separated actor/country codes")
    ap.add_argument("--theme", action="append", dest="themes", default=None, help="GKG theme; repeatable")
    ap.add_argument("--limit", type=int, default=None, help="row cap per table (POC)")
    ap.add_argument("--out", default="data", help="output dir")
    ap.add_argument("--dry-run", action="store_true", help="print SQL + params, do not query")
    args = ap.parse_args()

    actors = [a.strip() for a in args.actors.split(",") if a.strip()]
    themes = args.themes or DEFAULT_THEMES
    # Anchored on token boundaries — an unanchored alternation makes SANCTIONS also
    # match WB_1234_SANCTIONS_REGIME and ENV_OIL match ENV_OILSPILL.
    themes_regex = theme_regex(themes)
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""

    events_sql = EVENTS_SQL.format(limit=limit_clause)
    gkg_sql = GKG_SQL.format(limit=limit_clause)
    params = {
        "start": _yyyymm_to_sqldate(args.start, end=False),
        "end": _yyyymm_to_sqldate(args.end, end=True),
        "gkgstart": _yyyymm_to_gkgdate(args.start, end=False),
        "gkgend": _yyyymm_to_gkgdate(args.end, end=True),
        "actors": actors,
        "themeRegex": themes_regex,
    }

    if args.dry_run:
        print("=== EVENTS SQL ===", events_sql)
        print("=== GKG SQL ===", gkg_sql)
        print("=== PARAMS ===", json.dumps(params, indent=2))
        return 0

    from google.cloud import bigquery

    client = bigquery.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    def run(sql, qp):
        cfg = bigquery.QueryJobConfig(query_parameters=qp)
        return client.query(sql, job_config=cfg).result()

    ev_params = [
        bigquery.ScalarQueryParameter("start", "INT64", params["start"]),
        bigquery.ScalarQueryParameter("end", "INT64", params["end"]),
        bigquery.ArrayQueryParameter("actors", "STRING", actors),
    ]
    gkg_params = [
        bigquery.ScalarQueryParameter("gkgstart", "INT64", params["gkgstart"]),
        bigquery.ScalarQueryParameter("gkgend", "INT64", params["gkgend"]),
        bigquery.ScalarQueryParameter("themeRegex", "STRING", themes_regex),
    ]

    n_ev = _write_jsonl(run(events_sql, ev_params), outdir / "events.jsonl")
    print(f"[bigquery_pull] events -> {outdir/'events.jsonl'} ({n_ev:,} rows)")
    n_gkg = _write_jsonl(run(gkg_sql, gkg_params), outdir / "gkg.jsonl")
    print(f"[bigquery_pull] gkg    -> {outdir/'gkg.jsonl'} ({n_gkg:,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
