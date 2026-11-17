# gdelt-market-impact

What-if market-impact reasoning over **GDELT** global-event data, fused with market
and portfolio graphs in **ArangoDB**. Ask a question such as *"what do US tariff
threats against China do to markets?"* or *"what happened on 2019-09-16?"* and get
an answer **with the evidence behind it**: the events retrieved, the measurement,
and the subgraph, not a black-box number.

**Including, most of the time, the answer "we can't tell you."** That is the main
finding, not a disclaimer. Sweeping every question the 21,320,124-event corpus can
express (every country pair, every CAMEO event code, every horizon, every market)
gives **405,987 tests, and not one survives correction for a search that wide.**
The strongest evidence anywhere (Palestine→Israel, 10 days, Brent, n = 37) misses
the corrected bar of 1 in 8.1 million by **3.4×**. No *class* of event in thirteen
years of news reliably moves a market.

Individual shocks that hit physical supply are another matter. The Abqaiq strike
(2019-09-14) moved WTI **+14.68%** at the next open while the S&P 500 fell 0.31%,
and the Saudi-Russia price war (2020-03-09) moved WTI **−24.59%** in one session.
So the useful output is a system that tells answerable questions from unanswerable
ones, and shows its working either way.

## Build it yourself in a few minutes

```bash
git clone https://github.com/ArthurKeen/gdelt-market-impact.git
cd gdelt-market-impact
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
starter/run.sh
```

`starter/run.sh` builds the whole system on a laptop: a month of GDELT (September
2019), nine years of prices, ArangoDB in Docker, and the app. No accounts or keys.
It reproduces the Abqaiq answer exactly. See [starter/README.md](starter/README.md).

## How it works

Two questions with different shapes, and they behave differently.

```
CLASS      "what do US tariff threats against China do?"
             ─▶ retrieve analogs ─▶ measure each against the price series
                ─▶ aggregate ─▶ t across n events        ─▶ reliably: nothing

INSTANCE   "what happened on 2020-03-09?"
             ─▶ measure that day against the asset's own baseline
                ─▶ z for one day                          ─▶ sometimes: a lot
```

- **Analog retrieval** is ArangoSearch-first (CAMEO, actor, theme and geography
  facets, each term inspectable), with one analog per 7-day block so no two
  measurement windows share a trading day.
- **Every move is measured from the last close strictly before the event**
  (`PriceSeries.prev`), so the event day's own reaction is inside the window.
  Anchoring on the event day's close measures the days *after* the reaction and
  can flip the sign; that was a real bug here.
- **The multiple-testing sweep** (`scripts/multiple_testing.py`) ranks results by
  exact Student-t probability, not by |t|, which flatters tiny samples.
- **Named entities from the coverage.** GDELT files the Houthis under Yemen and
  Hezbollah under Lebanon. `scripts/extract_entities.py` reads the organisations,
  people and places named in the linked articles back onto each event, so "Houthis
  hit Abqaiq, operated by Saudi Aramco" is a question the graph can answer. A
  retrieval facet, not a causal claim: [docs/entity-extraction.md](docs/entity-extraction.md).
- **Plain-English questions** are read by rules, not a language model, shown back
  to you, and run only when you confirm. A question about something the corpus does
  not cover is refused with the reason.

## Who else should feel it: the exposure graph

A second named graph, `gdelt_exposure`, links a chokepoint (Hormuz, Bab el-Mandeb,
Suez) to the goods that pass through it, the countries that depend on them, the
markets listed there and the companies that use them, down to a sample portfolio's
holdings. Every figure carries its published source (EIA, USGS, METI, S&P Global,
Pew, NDSU): Japan took 95% of its crude from the Middle East in every month of
2024; Qatar produces 35% of the world's helium, which chip fabs use, and TSMC makes
Apple's and NVIDIA's chips.

Whether Japan, Korea or TSMC is actually hit harder was then **tested with
predictions committed before they ran**. All failed, including the oil control
that checks the event days were real supply shocks, both on the biggest Gulf
conflict-news days ([H1–H4](docs/exposure-hypotheses.md)) and on days whose
coverage names an oil facility ([H5](docs/exposure-hypotheses-h5.md)). The event
sets do not isolate supply shocks yet, so neither result says Asia is unexposed.
The paths explain why a market is worth watching; they are not evidence it moved.
(The commits that fixed each prediction before it ran are in the original working
repository, which is not published with this copy; each write-up says so.)

## Data sources

All free.

- **GDELT** Events and GKG over plain HTTP (`data.gdeltproject.org`), no
  credentials. Filtered to events between any two of USA, CHN, IRN, SAU, RUS, ISR,
  YEM, LBN, SYR, IRQ and PSE. `scripts/bigquery_pull.py` is an alternative path
  through BigQuery's public `gdelt-bq.gdeltv2` dataset for larger pulls; it needs a
  Google Cloud project.
- **Prices** from Yahoo Finance via `yfinance` (no key): the S&P 500, Nasdaq-100,
  VIX, the 11 sector ETFs, WTI and Brent, gold, a few stocks, and fifteen series for
  who else could feel a Gulf shock (Asian indices, the yen, TSMC, Samsung,
  fertilizer, shipping and industrial-gas companies).
- **FRED** (free key, optional): 10-year and 2-year Treasury yields, 10-year
  breakeven inflation and the broad dollar. Rates move in basis points, never as a
  percentage of the rate.

This repository ships **no price data**: every user pulls their own, under each
provider's terms. Choices, limits and the licensed-vendor path are in
[docs/market-data.md](docs/market-data.md).

## The full build

```bash
cp .env.example .env          # ARANGO_* for your database (+ FRED_API_KEY, optional)
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

python scripts/gdelt_http_pull.py --scenarios e1,e2      # GDELT, no credentials
python scripts/market_data_pull.py --from 2013-01-01 --to 2026-10-31

python scripts/db_admin.py create        # create the database if absent
python scripts/build_graph.py            # collections, graph, structural and temporal edges
python scripts/build_inferred_edges.py   # correlatesWith / affects from the prices
python scripts/build_conditional_baselines.py
python scripts/extract_entities.py       # named groups, operators, facilities
python scripts/build_exposure_graph.py   # chokepoints -> goods -> countries -> companies
python scripts/multiple_testing.py       # the sweep

python app/main.py --port 8000           # http://localhost:8000
```

The full corpus (2013-04-01 to 2026-10-31, 4,936 days) is about 130 million
documents and takes hours to load. Keeping it current, backups and the checks that
stop a refresh from silently moving a published figure:
[docs/operations.md](docs/operations.md). The schema: [docs/schema.md](docs/schema.md).

## Layout

```
app/        the UI and API: FastAPI + Cytoscape.js (main.py, queries.py, nlq.py, paths.py, static/)
starter/    one-command local build (Docker ArangoDB, a month of GDELT)
scripts/    pulls · graph build · inferred edges · baselines · entity extraction ·
            exposure graph · multiple_testing (the sweep) · exposure_hypotheses
            (pre-registered tests; --h5) · refresh_corpus · headline_figures ·
            verify_provenance · db_admin / db_backup.sh · install_visualizer*
queries/    the tariff what-if in AQL, kept as an audit trail
docs/       schema · market data · operations · entity extraction ·
            exposure hypotheses and results · headline-figures register
tests/      test_pure.py (no database) · test_graph.py (needs a loaded database)
```

## Tests

```bash
.venv/bin/python -m pytest tests/test_pure.py   # no database, seconds
.venv/bin/python -m pytest tests/               # also needs a loaded database
```

They are invariants, not coverage: each exists because getting it wrong already
produced a plausible wrong number once. See [tests/README.md](tests/README.md).

## What this is not

It does not predict markets and does not prove causation. Edges named
`correlatesWith` and `affects` are associational, on purpose. Its value is saying
what it can and cannot detect, with the evidence shown.

## License

Apache License 2.0, copyright 2026 ArangoDB GmbH. See [LICENSE](LICENSE). GDELT, Yahoo Finance and FRED data are
subject to their own terms.
