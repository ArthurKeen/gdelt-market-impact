"""Create the target ArangoDB database if it does not already exist.

`_common.db()` connects straight to ARANGO_DATABASE, which 404s (ERR 1228) when
the database has not been created yet. This connects to `_system` first and
creates it — the one step that gated the whole load.

Idempotent: safe to re-run.
"""
from __future__ import annotations

import argparse
import os

from _common import DB_NAME, db


def create(name: str) -> bool:
    """Create `name` if absent. Returns True if it was created by this call."""
    system = db("_system")
    if name in system.databases():
        print(f"[create_db] '{name}' already exists")
        return False
    system.create_database(name)
    print(f"[create_db] created '{name}'")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default=DB_NAME, help=f"database name (default {DB_NAME})")
    args = ap.parse_args()

    endpoint = os.environ.get("ARANGO_ENDPOINT", "(unset)")
    print(f"[create_db] endpoint={endpoint} target={args.name}")
    create(args.name)

    # Prove the target itself is reachable, not just _system.
    print(f"[create_db] verified: {db(args.name).name} reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
