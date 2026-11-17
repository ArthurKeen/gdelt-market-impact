#!/usr/bin/env python3
"""Merge the continuous pull with the existing episode-targeted corpus.

W4/G12 replaces episode targeting with a continuous pull across the E1 span, but
the existing `data/*.jsonl` also holds **E2 oil episodes running to 2022-03** that
the continuous window does not cover. A plain re-pull would silently drop them —
`gdelt_http_pull.py` opens its output with `"w"`.

So: keep every existing row whose pull-day falls outside the continuous window,
add every continuous row, and de-duplicate. Events key on GLOBALEVENTID, GKG rows
on their record id, so a day present in both contributes each record once.

    python scripts/merge_corpus.py --continuous data/continuous --existing data --out data
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil


def load(path: pathlib.Path) -> list[dict]:
    """Read a whole JSONL file into memory. Small inputs only — see stream().

    Kept only for callers that genuinely need a list. Do NOT use it on a corpus
    file: at 5.5M events they are multi-gigabyte, and JSON inflates 4-6x as
    Python objects.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stream(path: pathlib.Path):
    """Yield rows one at a time. Re-iterable — the merge walks the input twice."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def first_row(path: pathlib.Path) -> dict | None:
    for row in stream(path):
        return row
    return None


def resolve_key(sample: dict, wanted: str, name: str) -> str:
    """Pick the unique-id field, refusing to guess badly.

    Fail loudly rather than silently collapsing. An earlier draft fell back to
    `_pullDate`, which would have deduplicated GKG to ONE ROW PER DAY.
    """
    if wanted in sample:
        return wanted
    alt = next((k for k in sample
                if k.lower().endswith("recordid") or k.lower().endswith("eventid")), None)
    if alt is None:
        raise SystemExit(
            f"{name}: no unique key found (wanted {wanted!r}); refusing to merge "
            f"— available keys: {sorted(sample)[:12]}")
    print(f"  {name}: key {wanted!r} absent, using {alt!r}")
    return alt


def merge_to_file(existing: pathlib.Path, continuous: pathlib.Path,
                  dest: pathlib.Path, key: str, lo: str, hi: str) -> dict[str, int]:
    """Stream the merge to `dest`. Continuous rows win inside [lo, hi].

    The previous implementation built a dict of every surviving ROW and returned
    it as a list. Combined with reading both inputs as lists, merging a 5.5M-event
    corpus meant ~9M row dicts resident at once — tens of gigabytes, and on
    2026-08-24 a contributor to taking a 32GB machine into swap.

    Only the KEYS are held now, in a set used to suppress duplicates, and rows go
    straight to disk in the same order the old code emitted them: existing rows
    from outside the window first, then continuous. Roughly 1GB of keys at this
    scale against tens of gigabytes of rows.
    """
    seen: set[str] = set()
    kept_outside = added = n_existing = n_continuous = 0

    with dest.open("w", encoding="utf-8") as out:
        for row in stream(existing):
            n_existing += 1
            day = row.get("_pullDate", "")
            if lo <= day <= hi:
                continue                       # superseded by the continuous pull
            k = str(row.get(key))
            if k in seen:
                continue
            seen.add(k)
            kept_outside += 1
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
        for row in stream(continuous):
            n_continuous += 1
            k = str(row.get(key))
            if k in seen:
                continue
            seen.add(k)
            added += 1
            out.write(json.dumps(row, separators=(",", ":")) + "\n")

    return {"kept_outside_window": kept_outside, "from_continuous": added,
            "existing": n_existing, "continuous": n_continuous,
            "merged": kept_outside + added}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--continuous", default="data/continuous")
    ap.add_argument("--existing", default="data")
    ap.add_argument("--out", default="data")
    ap.add_argument("--from", dest="lo", default="2018-02-27")
    ap.add_argument("--to", dest="hi", default="2020-03-31")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    cont, exist, out = (pathlib.Path(args.continuous), pathlib.Path(args.existing),
                        pathlib.Path(args.out))
    out.mkdir(parents=True, exist_ok=True)

    for name, key in (("events.jsonl", "GLOBALEVENTID"), ("gkg.jsonl", "GKGRECORDID")):
        sample = first_row(cont / name)
        if sample is None:
            print(f"  {name}: no continuous rows — skipped")
            continue
        key = resolve_key(sample, key, name)
        if not args.no_backup and (exist / name).exists() and exist == out:
            shutil.copy2(exist / name, exist / f"{name}.pre-w4")
        # Write to a sibling temp file and rename, so an interrupted merge leaves
        # the previous corpus intact rather than a half-written one. This matters:
        # the GKG half of a merge was killed mid-run on 2026-08-24 and the only
        # reason nothing was lost is that the real file had not been touched yet.
        tmp = out / f"{name}.tmp"
        stats = merge_to_file(exist / name, cont / name, tmp, key, args.lo, args.hi)
        tmp.replace(out / name)
        print(f"  {name}: {stats['existing']:,} existing + {stats['continuous']:,} "
              f"continuous -> {stats['merged']:,}  (kept outside window "
              f"{stats['kept_outside_window']:,}, key={key})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
