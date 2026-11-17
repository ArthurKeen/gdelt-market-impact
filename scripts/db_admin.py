"""Lifecycle management for this repo's ArangoDB database.

The target is ALWAYS `ARANGO_DATABASE` from .env — never a name passed on the
command line. A cluster is often shared with other projects, so this tool is
deliberately incapable of naming, let alone dropping, a database other than its
own.

  python scripts/db_admin.py create              # idempotent
  python scripts/db_admin.py info                # collections, counts, graphs
  python scripts/db_admin.py drop --confirm gdelt_market

Backup/restore live in scripts/db_backup.sh (arangodump/arangorestore via the
ArangoDB CE Docker image). See docs/operations.md.
"""
from __future__ import annotations

import argparse
import os
import sys

from _common import DB_NAME, db, env

# Databases this tool must never touch even if .env is misconfigured. Belt and
# braces: `drop` also requires the name to equal ARANGO_DATABASE and be retyped.
# On a shared cluster, list the other databases in .env so a misconfigured
# ARANGO_DATABASE can never name one: ARANGO_PROTECTED_DBS=teamA_db,teamB_db
PROTECTED = {"_system"} | {
    n.strip() for n in (env("ARANGO_PROTECTED_DBS") or "").split(",") if n.strip()}


def _target() -> str:
    if DB_NAME in PROTECTED:
        sys.exit(f"refusing to operate on protected database {DB_NAME!r} — check .env")
    return DB_NAME


def cmd_create(_args) -> int:
    name, sys_db = _target(), db("_system")
    if sys_db.has_database(name):
        print(f"[create] {name!r} already exists — nothing to do")
        return 0
    sys_db.create_database(name)
    print(f"[create] created {name!r} on {os.environ['ARANGO_ENDPOINT']}")
    return 0


def cmd_info(_args) -> int:
    name, sys_db = _target(), db("_system")
    if not sys_db.has_database(name):
        print(f"[info] {name!r} does not exist — run: python scripts/db_admin.py create")
        return 1
    target = db(name)
    cols = sorted((c for c in target.collections() if not c["name"].startswith("_")),
                  key=lambda c: c["name"])
    print(f"[info] {name} @ {os.environ['ARANGO_ENDPOINT']}  (server {sys_db.version()})")
    if not cols:
        print("  collections: NONE — not loaded yet (run scripts/build_graph.py)")
    else:
        width = max(len(c["name"]) for c in cols)
        total = 0
        for c in cols:
            n = target.collection(c["name"]).count()
            total += n
            print(f"  {c['name']:<{width}}  {c['type']:>8}  {n:>10,} docs")
        print(f"  {'TOTAL':<{width}}  {'':>8}  {total:>10,} docs")
    graphs = [g["name"] for g in target.graphs()]
    print("  graphs:", ", ".join(graphs) if graphs else "NONE")
    views = [v["name"] for v in target.views()]
    print("  views: ", ", ".join(views) if views else "NONE")
    return 0


def cmd_drop(args) -> int:
    name, sys_db = _target(), db("_system")
    if args.confirm != name:
        sys.exit(f"refusing to drop: pass --confirm {name} to prove intent "
                 f"(got {args.confirm!r})")
    if not sys_db.has_database(name):
        print(f"[drop] {name!r} does not exist — nothing to do")
        return 0
    sys_db.delete_database(name)
    print(f"[drop] dropped {name!r}. Restore with scripts/db_backup.sh restore <dir>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create", help="create ARANGO_DATABASE if absent (idempotent)")
    sub.add_parser("info", help="collections, document counts, graphs, views")
    p_drop = sub.add_parser("drop", help="delete ARANGO_DATABASE (destructive)")
    p_drop.add_argument("--confirm", required=True, metavar="DBNAME",
                        help="must exactly match ARANGO_DATABASE")
    args = ap.parse_args()
    return {"create": cmd_create, "info": cmd_info, "drop": cmd_drop}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
