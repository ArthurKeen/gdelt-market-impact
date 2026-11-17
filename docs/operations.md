# Operations

Running the full corpus: keeping it current, backing it up, and the traps worth
knowing before you repeat a load. Every command reads its connection from `.env`
(`ARANGO_ENDPOINT`, `ARANGO_USER`, `ARANGO_PASSWORD`, `ARANGO_DATABASE`).

## Corpus span

**2013-04-01 .. 2026-10-31**, 4,936 days, **21,320,124 events**, about 130 million
documents. Starting in 2013 matters for an oil-centric system: it holds the
2014-2016 collapse, when WTI fell 76%. **2014-11-28**, the day after OPEC declined
to cut production, reads Energy −6.42%, Brent −9.77%, WTI −10.23%, the same
mechanism as the Saudi-Russia price war of 2020-03-09.

**Three things to know if you load it yourself:**

- **The GDELT pull exits 0 with days missing.** Broken pipes, incomplete reads and
  timeouts show up only as `FAILED` lines in the log. Grep for them before
  trusting the summary, re-pull those dates with `--dates`, and merge; the merge
  de-duplicates.
- **The price file has to cover the corpus span.** A price file that starts later
  than the corpus leaves years measured against baselines that do not reach them.
  Pull prices for the whole span first.
- **10,864 events will not load, and that is correct.** Every one carries
  `DATEADDED=None`, all from 2013-09-01. An event with no date cannot be placed on
  a timeline, so the transform skips it. Reconcile the count rather than shrugging
  at it.

## Keeping it current: `scripts/refresh_corpus.py`

Nothing updates the corpus on its own. The app only reads; the corpus ends where
the last load ended, and `/about` reports the span live.

A refresh is **manual, on purpose.** Answers come from a decade of history, so a
month of new days barely moves any of them, but a refresh does move published
figures, and somebody has to carry those into the documents. That should be a
decision, not a timer.

```bash
python scripts/refresh_corpus.py --dry-run          # what it would pull; changes nothing
python scripts/_detach.py data/refresh.log \
       python scripts/refresh_corpus.py              # the real run, detached; hours
python scripts/refresh_corpus.py --resume data/refresh/<from>_<to>   # after a stop
```

It pulls from the day after the corpus ends to two days ago, then runs pull →
prices → merge → load → edges → baselines → entities → exposure → sweep → verify →
rotate. Each stage logs to `data/refresh/<from>_<to>/<stage>.log` and is recorded
when it finishes, so `--resume` skips work already done. Missing and empty days are
retried and stop the run if still missing (`--accept-missing` overrides), prices
are re-pulled to the end date and checked, and every derived step re-runs.

The load is an upsert of a strict superset with deterministic edge keys, so the app
keeps serving throughout.

Needs: Docker running (the load uses `arangoimport`) and about 1.15× the corpus
free on disk for the merge (~42 GB at 21M events). GDELT and Yahoo are both public,
so no other credentials.

### What a refresh changes, and what it must not

| Moves, as expected | Must not move |
|---|---|
| corpus size, span, day count, named-event count | the **delivered window** (2018-02-27..2022-03-31): it is pinned |
| the multiple-testing sweep: test count, rate, margin | any **raw price move**: a past close is a past close |
| **full-window** z-scores, a little, as the baseline gains days | |

`scripts/headline_figures.py` checks this. It recomputes every figure the prose
quotes through the same code the UI uses, compares them with the register in
[headline-figures.json](headline-figures.json), and prints each one that moved
with the file and line still quoting the old value. The `verify` stage runs it and
refuses to rotate if a pinned figure moved. If that happens, find out why before
editing any document; a revised Yahoo history is the likeliest cause.

After a refresh: update the lines it lists, accept the new values with
`python scripts/headline_figures.py --write`, and delete `data/full.prev` once you
are satisfied (it is the previous corpus, kept for one rollback).

## Database lifecycle: `scripts/db_admin.py`

```bash
python scripts/db_admin.py create      # idempotent
python scripts/db_admin.py info        # collections, counts, graphs, views
python scripts/db_admin.py drop --confirm <ARANGO_DATABASE>
```

`drop` requires the name retyped in `--confirm`, that it equals `ARANGO_DATABASE`,
and that it is not in `PROTECTED`. On a shared cluster, add the other databases'
names to `PROTECTED`.

## Backup and restore: `scripts/db_backup.sh`

Dump and restore run from the ArangoDB Docker image, so no client tools are needed.

```bash
scripts/db_backup.sh dump                  # -> backups/<db>-<UTC>/
scripts/db_backup.sh list
scripts/db_backup.sh restore backups/<dir>
scripts/db_backup.sh prune [N]             # keep the N newest (default 1)
```

- **Both sides pass `--include-system-collections true`.** Named graph
  definitions live in the `_graphs` system collection, and Visualizer assets in
  others. Without the flag a restore reports success with every document count
  matching, and the graph is gone.
- **`arangorestore` merges.** For a clean point-in-time recovery, drop and
  recreate the database first, then restore, then compare `db_admin.py info`
  against the dump.
- **Pin the image to the server's minor version.** A dump from a mismatched
  arangodump can be refused on restore.
- `backups/` is gitignored: dumps contain the full corpus.

## Bulk import

`build_graph.py` is the supported loader. `arangoimport` is faster when you already
have collection-shaped JSONL, for example to reload one collection:

```bash
docker run --rm -e ARANGO_PASSWORD -v "$PWD/data:/data" \
  --entrypoint sh arangodb:3.12 -c \
  'arangoimport --server.endpoint http+ssl://<host>:443 \
     --server.database <db> --server.username <user> \
     --server.password "$ARANGO_PASSWORD" \
     --file /data/<collection>.jsonl --type jsonl --collection <Collection>'
```

The raw `data/*.jsonl` pulls are source-shaped GDELT records, not collection
documents with `_key`. Only import JSONL that `build_graph.py` has shaped.

## Do not `source .env`

`set -a; . ./.env` silently corrupts any value containing `#` (bash truncates
there) or `<` and `>` (parsed as redirections), so a strong password can arrive
wrong with no error. The shell scripts here parse `.env` with python-dotenv and
re-emit `shlex.quote`d assignments; copy that pattern for new tooling.

## Graph Visualizer assets

```bash
python scripts/install_visualizer.py          # theme
python scripts/install_visualizer_assets.py   # panel queries, editor queries, canvas actions
python scripts/install_visualizer_assets.py --test   # run them all against the live graph
```

They live in system collections (`_graphThemeStore`, `_queries`,
`_editor_saved_queries`, `_canvasActions`, `_viewpoints*`), so `build_graph.py
--drop` does not remove them but a database drop does. Re-run both installers after
restoring into a fresh database, then `--test`.

## Before any rebuild

Take a dump. `build_graph.py --drop` deletes and recreates every collection and
severs the `event_analog` ArangoSearch view's link to `Event`; delete the view and
re-run `build_inferred_edges.py` afterwards. Without `--drop`, edges carry
deterministic keys and are upserted, so a re-run updates rather than duplicates.
