#!/usr/bin/env python3
"""Load built collections via `arangoimport` in a Docker container.

`build_graph.py` loads with `import_bulk` in 5,000-row batches over HTTP. That is
fine at the 1.2M-document scale the episode-targeted corpus produced. The
continuous pull is several times larger, and `arangoimport` is built for
exactly this: it streams a file, parallelises across threads, and does not pay the
per-batch round trip that a Python loop does.

No local install needed — the official image ships the binary:

    docker run --rm -v <dir>:/in arangodb/arangodb:latest arangoimport ...

Endpoint translation matters. `.env` holds a browser-style URL
(`https://host`), and arangoimport wants `http+ssl://host:port`. Anything else
fails with a connection error that reads like a network problem rather than a
config one.

Usage
-----
    from arangoimport_load import import_collections
    import_collections({"Event": [...], "mentions": [...]}, edge={"mentions"})

or from the command line, over JSONL already on disk:

    python scripts/arangoimport_load.py --dir build/collections
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

from dotenv import dotenv_values

IMAGE = "arangodb/arangodb:latest"
REPO = pathlib.Path(__file__).resolve().parent.parent


def _conn() -> dict[str, str]:
    """Connection details, with the PROCESS ENVIRONMENT taking precedence.

    This read `.env` directly and ignored os.environ, which made
    `ARANGO_DATABASE=staging python build_graph.py --via-arangoimport` a silent
    lie: the Python side honoured the override and created its schema in the
    staging database, while the import — the half that actually writes the rows —
    went to whatever `.env` said. A 12M-event corpus intended for a staging
    database landed in the production one that way on 2026-09-01.

    os.environ last, so a deliberate override wins and `.env` remains the default.
    """
    env = {**dotenv_values(REPO / ".env"),
           **{k: v for k, v in os.environ.items() if k.startswith("ARANGO_")}}
    u = urlparse(env["ARANGO_ENDPOINT"].rstrip("/"))
    scheme = "http+ssl" if u.scheme == "https" else "tcp"
    return {
        "endpoint": f"{scheme}://{u.hostname}:{u.port or 8529}",
        "database": env.get("ARANGO_DATABASE", "gdelt_market"),
        "username": env.get("ARANGO_USER", "root"),
        "password": env["ARANGO_PASSWORD"],
    }


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE],
                          capture_output=True).returncode == 0


def import_file(path: pathlib.Path, collection: str, *, edge: bool = False,
                threads: int = 8, conn: dict[str, str] | None = None) -> dict[str, int]:
    """Import one JSONL file. Returns the created/updated/errors counts."""
    c = conn or _conn()
    cmd = [
        "docker", "run", "--rm", "-v", f"{path.parent}:/in", IMAGE, "arangoimport",
        "--server.endpoint", c["endpoint"],
        "--server.database", c["database"],
        "--server.username", c["username"],
        "--server.password", c["password"],
        "--file", f"/in/{path.name}",
        "--type", "jsonl",
        "--collection", collection,
        "--create-collection", "true",
        # Matches build_graph's import_bulk(on_duplicate="update") so a re-run is
        # idempotent rather than doubling every edge.
        "--on-duplicate", "update",
        "--threads", str(threads),
        "--progress", "false",
    ]
    if edge:
        cmd += ["--create-collection-type", "edge"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    stats = {"created": 0, "updated": 0, "errors": 0}
    for line in (out.stdout + out.stderr).splitlines():
        low = line.lower().strip()
        for key, prefix in (("created", "created:"), ("updated", "updated/replaced:"),
                            ("errors", "warnings/errors:")):
            if low.startswith(prefix):
                try:
                    stats[key] = int(low.split(":", 1)[1].strip())
                except ValueError:
                    pass
    if out.returncode != 0 and not any(stats.values()):
        raise RuntimeError(f"arangoimport failed for {collection}:\n"
                           f"{(out.stderr or out.stdout)[-800:]}")
    return stats


def import_collections(built: dict[str, "list[dict] | pathlib.Path"],
                       edge: set[str] | None = None,
                       threads: int = 8) -> dict[str, dict[str, int]]:
    """Import each collection, given either rows or an already-written JSONL file.

    A value may be a list of documents (written to a temp file here) or a
    `pathlib.Path` to a file already in arangoimport's format. The Path form
    exists so `build_graph.py` can spool edge documents straight to disk as it
    derives them: at 5.5M events the edge collections are ~28M documents, and
    materialising them as Python lists just to serialise them here is ~17GB for
    no benefit. Anything already on disk is imported in place.
    """
    edge = edge or set()
    conn = _conn()
    results: dict[str, dict[str, int]] = {}
    with tempfile.TemporaryDirectory(prefix="gdelt-import-") as tmp:
        d = pathlib.Path(tmp)
        for name, rows in built.items():
            if not rows:
                continue
            if isinstance(rows, pathlib.Path):
                f = rows
            else:
                f = d / f"{name}.jsonl"
                with f.open("w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            results[name] = import_file(f, name, edge=name in edge,
                                        threads=threads, conn=conn)
            s = results[name]
            print(f"  [import] {name:<16} created {s['created']:>9,}  "
                  f"updated {s['updated']:>9,}  errors {s['errors']}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True,
                    help="directory of <collection>.jsonl files")
    ap.add_argument("--edge", default="",
                    help="comma-separated collection names to create as edge collections")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    if not docker_available():
        print(f"docker or the {IMAGE} image is unavailable — "
              f"run `docker pull {IMAGE}`")
        return 1

    edge = {x for x in args.edge.split(",") if x}
    conn = _conn()
    print(f"[import] {conn['endpoint']} db={conn['database']}")
    for f in sorted(pathlib.Path(args.dir).glob("*.jsonl")):
        s = import_file(f, f.stem, edge=f.stem in edge, threads=args.threads, conn=conn)
        print(f"  [import] {f.stem:<16} created {s['created']:>9,}  "
              f"updated {s['updated']:>9,}  errors {s['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
