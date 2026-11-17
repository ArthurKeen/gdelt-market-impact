"""Prove the local corpus is real: cross-check it against the upstream sources.

The POC's whole pitch is *auditable, not a black box*. That claim should extend to
the corpus itself, so this script re-derives provenance from the authoritative
sources and diffs them field by field against what is on disk:

  events  — `data/events.jsonl` vs the official daily export at
            `data.gdeltproject.org/events/<YYYYMMDD>.export.CSV.zip`. Every field
            the puller extracts is compared, using the puller's OWN index map
            (`gdelt_http_pull.EVENT_FIELDS`) so the two cannot drift apart.
  prices  — `data/prices.jsonl` vs Yahoo Finance's public chart endpoint, daily
            closes compared to the cent.

Neither check needs credentials of any kind. That is the point: GDELT's daily
files and Yahoo's chart endpoint are both public, so anyone — a reviewer, a
customer, CI — can re-run this and get the same verdict. BigQuery
(`gdelt-bq.gdeltv2`) is only a convenience path to the same GDELT data, not the
only one, and it is the only path that needs a GCP project.

    python scripts/verify_provenance.py                    # sample 2 days + 4 symbols
    python scripts/verify_provenance.py --days 5           # more GDELT days
    python scripts/verify_provenance.py --date 2018-03-22  # a specific day
    python scripts/verify_provenance.py --events-only
    python scripts/verify_provenance.py --prices-only

Exit status is 0 only if every comparison matched, so this is usable as a
pre-demo gate or a CI step.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import pathlib
import random
import sys
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Reuse the puller's field map so a change there cannot silently invalidate this
# check — if the extraction contract moves, this moves with it.
from gdelt_http_pull import EVENTS_URL, EVENT_FIELDS  # noqa: E402

REPO = pathlib.Path(__file__).parent.parent
EVENTS_JSONL = REPO / "data/events.jsonl"
PRICES_JSONL = REPO / "data/prices.jsonl"

YAHOO_CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               "?period1={p1}&period2={p2}&interval=1d")
# A deliberately mixed sample: index, single equity, sector ETF, commodity future.
DEFAULT_SYMBOLS = ["^GSPC", "AAPL", "XLE", "CL=F"]


# ── GDELT ────────────────────────────────────────────────────────────
def load_local_events() -> dict[str, list[dict]]:
    """Group local event records by the day-file they were pulled from."""
    by_day: dict[str, list[dict]] = collections.defaultdict(list)
    with EVENTS_JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            day = rec.get("_pullDate")
            if day:
                by_day[day].append(rec)
    return by_day


def fetch_official_events(day: str, timeout: int = 300) -> dict[str, list[str]]:
    """Download one official daily export, keyed by GLOBALEVENTID."""
    ymd = day.replace("-", "")
    url = EVENTS_URL.format(ymd=ymd)
    req = urllib.request.Request(url, headers={"User-Agent": "gdelt-market-impact/provenance"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = resp.read()
    rows: dict[str, list[str]] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 58:
                    rows[parts[0]] = parts
    return rows


def _field_matches(local_value, official: str) -> bool:
    """Compare one extracted field against the official raw string."""
    if local_value is None or local_value == "":
        return True  # the puller drops blanks; nothing asserted
    official = official.strip()
    if isinstance(local_value, bool):
        return str(local_value) == official
    if isinstance(local_value, float):
        try:
            return abs(local_value - float(official)) < 1e-9
        except ValueError:
            return False
    if isinstance(local_value, int):
        try:
            return local_value == int(official)
        except ValueError:
            return False
    return str(local_value).strip() == official


def verify_events(days: list[str]) -> tuple[bool, list[str]]:
    report: list[str] = []
    by_day = load_local_events()
    report.append(f"local corpus: {sum(len(v) for v in by_day.values()):,} events "
                  f"across {len(by_day)} pull-days")
    ok_overall = True

    for day in days:
        local = by_day.get(day)
        if not local:
            report.append(f"  {day}  SKIP — no local events for this day")
            continue
        try:
            official = fetch_official_events(day)
        except Exception as err:  # network / 404 / zip
            report.append(f"  {day}  FETCH FAILED — {type(err).__name__}: {err}")
            ok_overall = False
            continue

        missing = [r for r in local if str(r["GLOBALEVENTID"]) not in official]
        comparisons = 0
        mismatches: collections.Counter = collections.Counter()
        examples: list[str] = []
        for rec in local:
            row = official.get(str(rec["GLOBALEVENTID"]))
            if row is None:
                continue
            for idx, key in EVENT_FIELDS.items():
                value = rec.get(key)
                if value is None or value == "":
                    continue
                comparisons += 1
                if not _field_matches(value, row[idx]):
                    mismatches[key] += 1
                    if len(examples) < 3:
                        examples.append(
                            f"      {rec['GLOBALEVENTID']} {key}: "
                            f"local={value!r} official={row[idx]!r}")
        verdict = "OK" if not missing and not mismatches else "FAIL"
        if verdict == "FAIL":
            ok_overall = False
        report.append(
            f"  {day}  {verdict}  official={len(official):,} events · "
            f"local={len(local):,} · ids found={len(local) - len(missing):,}/{len(local):,} · "
            f"{comparisons:,} field comparisons · mismatches={sum(mismatches.values())}")
        if missing:
            report.append(f"      ids absent upstream: "
                          f"{[str(r['GLOBALEVENTID']) for r in missing[:5]]}")
        if mismatches:
            report.append(f"      by field: {dict(mismatches)}")
            report.extend(examples)
    return ok_overall, report


# ── Prices ───────────────────────────────────────────────────────────
def load_local_prices() -> dict[str, dict[str, float]]:
    by_sym: dict[str, dict[str, float]] = collections.defaultdict(dict)
    with PRICES_JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("close") is not None:
                by_sym[rec["symbol"]][rec["date"]] = rec["close"]
    return by_sym


def fetch_official_closes(symbol: str, start: str, end: str,
                          timeout: int = 60) -> dict[str, float]:
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp())
    url = YAHOO_CHART.format(sym=urllib.parse.quote(symbol), p1=p1, p2=p2)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out: dict[str, float] = {}
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        day = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()
        out[day] = close
    return out


def verify_prices(symbols: list[str], start: str, end: str) -> tuple[bool, list[str]]:
    report: list[str] = []
    local = load_local_prices()
    report.append(f"local corpus: {sum(len(v) for v in local.values()):,} bars "
                  f"across {len(local)} symbols")
    ok_overall = True
    total = matched = 0

    for symbol in symbols:
        bars = local.get(symbol)
        if not bars:
            report.append(f"  {symbol:8s} SKIP — not in the local slice")
            continue
        window = {d: c for d, c in bars.items() if start <= d < end}
        if not window:
            report.append(f"  {symbol:8s} SKIP — no local bars in {start}..{end}")
            continue
        try:
            official = fetch_official_closes(symbol, start, end)
        except Exception as err:
            report.append(f"  {symbol:8s} FETCH FAILED — {type(err).__name__}: {err}")
            ok_overall = False
            continue
        ok = bad = 0
        example = ""
        for day, close in sorted(window.items()):
            if day not in official:
                continue
            total += 1
            # Yahoo serves full float precision; a cent is the meaningful tolerance.
            if abs(official[day] - close) < max(0.01, abs(close) * 1e-6):
                ok += 1
                matched += 1
            else:
                bad += 1
                if not example:
                    example = f"      {day} local={close} official={official[day]}"
        verdict = "OK" if bad == 0 and ok else "FAIL"
        if verdict != "OK":
            ok_overall = False
        report.append(f"  {symbol:8s} {verdict}  {ok} closes match / {bad} differ "
                      f"(local bars in window: {len(window)})")
        if example:
            report.append(example)
    report.append(f"  total: {matched:,}/{total:,} closes match to the cent")
    return ok_overall, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=2,
                    help="how many pull-days to sample for the GDELT check (default 2)")
    ap.add_argument("--date", action="append", metavar="YYYY-MM-DD",
                    help="verify a specific day (repeatable); overrides --days")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="comma-separated symbols for the price check")
    ap.add_argument("--price-start", default="2016-01-01")
    ap.add_argument("--price-end", default="2016-02-01")
    ap.add_argument("--events-only", action="store_true")
    ap.add_argument("--prices-only", action="store_true")
    ap.add_argument("--seed", type=int, default=0,
                    help="seed for day sampling, so a run is reproducible")
    args = ap.parse_args()

    failures = []
    print("=" * 72)
    print("PROVENANCE CHECK — local corpus vs upstream sources (no credentials)")
    print("=" * 72)

    if not args.prices_only:
        if not EVENTS_JSONL.exists():
            print(f"\nGDELT events: SKIP — {EVENTS_JSONL} not present")
        else:
            if args.date:
                days = args.date
            else:
                available = sorted(load_local_events())
                rng = random.Random(args.seed)
                days = sorted(rng.sample(available, min(args.days, len(available))))
            print(f"\nGDELT events  →  {EVENTS_URL.format(ymd='<YYYYMMDD>')}")
            ok, report = verify_events(days)
            print("\n".join(report))
            if not ok:
                failures.append("events")

    if not args.events_only:
        if not PRICES_JSONL.exists():
            print(f"\nMarket prices: SKIP — {PRICES_JSONL} not present")
        else:
            print("\nMarket prices  →  Yahoo Finance chart endpoint")
            ok, report = verify_prices(
                [s.strip() for s in args.symbols.split(",") if s.strip()],
                args.price_start, args.price_end)
            print("\n".join(report))
            if not ok:
                failures.append("prices")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: FAIL — {', '.join(failures)} did not reconcile upstream")
        return 1
    print("RESULT: PASS — every compared field matches the authoritative source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
