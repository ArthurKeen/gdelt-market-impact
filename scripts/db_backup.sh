#!/usr/bin/env bash
# Backup / restore for this repo's ArangoDB database.
#
# The pilot cluster has no arangodump/arangorestore installed locally, so both
# run from the ArangoDB CE Docker image. IMAGE is pinned to 3.12.9-1 to match the
# server exactly (`arangodb:3.12` tag) — a dump taken by a different minor can
# refuse to restore.
#
#   scripts/db_backup.sh dump                  # -> backups/<db>-<UTC timestamp>/
#   scripts/db_backup.sh restore backups/DIR   # into ARANGO_DATABASE
#   scripts/db_backup.sh list
#
# Credentials come from .env. The password is passed to the container as an env
# var rather than an argv flag, so it stays out of the host process list; note it
# is still visible to `docker inspect` while the container runs (seconds).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="arangodb:3.12"
BACKUP_ROOT="$REPO/backups"

# Parse .env with python-dotenv rather than `source`-ing it: a value containing
# <, >, #, spaces or quotes is valid in .env but breaks bash. shlex.quote makes
# the emitted assignments safe to eval.
[ -x "$REPO/.venv/bin/python" ] || { echo "no .venv — run: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2; exit 1; }
eval "$("$REPO/.venv/bin/python" -c '
import shlex, sys
from dotenv import dotenv_values
env = dotenv_values(sys.argv[1])
for k in ("ARANGO_ENDPOINT", "ARANGO_DATABASE", "ARANGO_USER", "ARANGO_PASSWORD"):
    v = env.get(k)
    if v is not None:
        print(f"{k}={shlex.quote(v)}")
' "$REPO/.env")"

: "${ARANGO_ENDPOINT:?not set in .env}"
: "${ARANGO_DATABASE:?not set in .env}"
: "${ARANGO_PASSWORD:?not set in .env}"
ARANGO_USER="${ARANGO_USER:-root}"

# arangodump speaks http+ssl://host:port, not https://host. Default TLS to :443.
endpoint() {
  local e="$ARANGO_ENDPOINT"
  case "$e" in
    https://*) e="http+ssl://${e#https://}" ;;
    http://*)  e="http+tcp://${e#http://}" ;;
  esac
  e="${e%/}"
  # append the default port if the host carries none
  if ! printf '%s' "${e#*://}" | grep -q ':'; then
    case "$ARANGO_ENDPOINT" in https://*) e="$e:443" ;; *) e="$e:8529" ;; esac
  fi
  printf '%s' "$e"
}

run_tool() {           # run_tool <arangodump|arangorestore> <args...>
  local tool="$1"; shift
  docker run --rm \
    -e "ARANGO_PASSWORD=$ARANGO_PASSWORD" \
    -v "$BACKUP_ROOT:/backups" \
    --entrypoint sh "$IMAGE" -c \
    "$tool --server.endpoint '$(endpoint)' \
           --server.database '$ARANGO_DATABASE' \
           --server.username '$ARANGO_USER' \
           --server.password \"\$ARANGO_PASSWORD\" \
           $* 2>&1"
}

cmd_dump() {
  # No Date.now() problems here, but keep timestamps UTC so they sort.
  local stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local name="$ARANGO_DATABASE-$stamp"
  mkdir -p "$BACKUP_ROOT"
  echo "[dump] $ARANGO_DATABASE -> backups/$name"
  # --include-system-collections true is REQUIRED, not optional: named graph
  # definitions live in the `_graphs` system collection. A restore drill on
  # 2026-08-11 came back with all 23 collections intact and `graphs: []` — the
  # graph had to be rebuilt by hand. Keep this true.
  run_tool arangodump --output-directory "/backups/$name" --overwrite true --include-system-collections true
  echo "[dump] size: $(du -sh "$BACKUP_ROOT/$name" | cut -f1)  path: backups/$name"
}

cmd_restore() {
  local dir="${1:?usage: db_backup.sh restore backups/<dir>}"
  local abs; abs="$(cd "$dir" && pwd)"
  [ -d "$abs" ] || { echo "no such backup: $dir" >&2; exit 1; }
  echo "[restore] backups/$(basename "$abs") -> $ARANGO_DATABASE"
  echo "[restore] this MERGES into the existing database; drop it first for a clean restore:"
  echo "          python scripts/db_admin.py drop --confirm $ARANGO_DATABASE && python scripts/db_admin.py create"
  # --include-system-collections true on BOTH sides. arangodump excludes system
  # collections by default AND so does arangorestore; miss it on either and
  # `_graphs` never lands, so the named graph is silently absent after recovery.
  run_tool arangorestore --input-directory "/backups/$(basename "$abs")" \
      --create-database true --include-system-collections true
  echo "[restore] done — verify with: python scripts/db_admin.py info"
}

cmd_list() {
  [ -d "$BACKUP_ROOT" ] || { echo "no backups yet"; return 0; }
  echo "backups in $BACKUP_ROOT:"
  for d in "$BACKUP_ROOT"/*/; do
    [ -d "$d" ] || continue
    printf "  %-44s %s\n" "$(basename "$d")" "$(du -sh "$d" | cut -f1)"
  done
}

cmd_prune() {
  local keep="${1:-1}"
  [ -d "$BACKUP_ROOT" ] || { echo "no backups yet"; return 0; }
  # Names are <db>-<UTC timestamp>, so lexical sort == chronological.
  local all; all=$(cd "$BACKUP_ROOT" && ls -d */ 2>/dev/null | sed 's:/$::' | sort)
  local total; total=$(printf '%s\n' "$all" | grep -c . || true)
  if [ "$total" -le "$keep" ]; then
    echo "[prune] $total backup(s), keeping $keep — nothing to delete"; return 0
  fi
  echo "[prune] keeping the $keep most recent of $total:"
  printf '%s\n' "$all" | tail -n "$keep" | sed 's/^/  KEEP   /'
  printf '%s\n' "$all" | head -n "$((total - keep))" | while read -r d; do
    [ -n "$d" ] || continue
    echo "  DELETE $d ($(du -sh "$BACKUP_ROOT/$d" | cut -f1))"
    rm -rf "$BACKUP_ROOT/$d"
  done
}

case "${1:-}" in
  dump)    cmd_dump ;;
  restore) shift; cmd_restore "$@" ;;
  list)    cmd_list ;;
  prune)   shift; cmd_prune "${1:-1}" ;;
  *) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
