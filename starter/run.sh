#!/usr/bin/env bash
# Build the whole thing on your laptop: one month of GDELT, nine years of prices,
# a local ArangoDB, and the same app — in about fifteen minutes, with no accounts.
#
#   starter/run.sh            # build, then serve on http://127.0.0.1:8811
#   starter/run.sh --serve    # just serve what is already built
#   starter/run.sh --down     # stop and delete the starter database
#
# Every script it runs is the production pipeline in ../scripts, pointed at a
# local database. Nothing here is a cut-down copy.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERE="$REPO/starter"
DATA="$HERE/data"
FROM="${STARTER_FROM:-2019-09-01}"     # the month of the Abqaiq strike
TO="${STARTER_TO:-2019-09-30}"
PORT="${STARTER_PORT:-8539}"
APP_PORT="${STARTER_APP_PORT:-8811}"
PY="${PYTHON:-$REPO/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

# Point every script at the local database, explicitly. The scripts also read
# .env, but load_dotenv never overrides a variable already set — so these win,
# and a .env holding cloud credentials cannot be written to by mistake.
export ARANGO_ENDPOINT="http://127.0.0.1:$PORT"
export ARANGO_USER=root
export ARANGO_PASSWORD="${STARTER_PASSWORD:-starter}"
export ARANGO_DATABASE=gdelt_starter
export ARANGO_VERIFY_SSL=false
export STARTER_PORT="$PORT" STARTER_PASSWORD="$ARANGO_PASSWORD"

compose() { docker compose -f "$HERE/docker-compose.yml" -p gdelt-starter "$@"; }

serve() {
  echo "==> serving http://127.0.0.1:$APP_PORT  (database $ARANGO_DATABASE on $ARANGO_ENDPOINT)"
  cd "$REPO" && exec "$PY" app/main.py --port "$APP_PORT"
}

case "${1:-}" in
  --down) compose down -v; rm -rf "$DATA"; echo "starter removed"; exit 0 ;;
  --serve) serve ;;
esac

step() { echo; echo "==> $*"; }

step "1/8 ArangoDB (Docker, port $PORT)"
compose up -d
until curl -fsS -u "root:$ARANGO_PASSWORD" "$ARANGO_ENDPOINT/_api/version" >/dev/null 2>&1; do sleep 2; done
cd "$REPO"
"$PY" scripts/db_admin.py create

step "2/8 GDELT events + news coverage, $FROM .. $TO (public files, no account)"
mkdir -p "$DATA"
"$PY" scripts/gdelt_http_pull.py --from "$FROM" --to "$TO" \
  --actors USA,CHN,IRN,SAU,RUS,ISR,YEM,LBN,SYR,IRQ,PSE --dyad-only \
  --out "$DATA" --workers 6 | grep -E "FAILED|done:" || true

step "3/8 prices, 2016 .. 2024 (Yahoo; FRED too if FRED_API_KEY is set)"
# Nine years, not one month: "is this move unusual?" needs years of ordinary days.
"$PY" scripts/market_data_pull.py --from 2016-01-01 --to 2024-12-31 \
  --out "$DATA/prices.jsonl" | tail -2

step "4/8 the graph: events, actors, places, themes, sectors, portfolio"
"$PY" scripts/build_graph.py --data "$DATA" | tail -4

step "5/8 price series, baselines, and the event-study edges"
"$PY" scripts/build_inferred_edges.py --data "$DATA" --window delivered | tail -2
"$PY" scripts/build_conditional_baselines.py --data "$DATA" --window delivered | tail -1

step "6/8 who acted and what was hit, read from the coverage"
"$PY" scripts/extract_entities.py --data "$DATA" | tail -2

step "7/8 supply paths: chokepoints, the goods through them, who depends on them"
"$PY" scripts/build_exposure_graph.py | tail -2

step "8/8 done — ask it about 2019-09-14 (Abqaiq), or type a question"
serve
