# Build it yourself — one month of geopolitics, on your laptop

This builds the whole system locally: a month of GDELT news events, nine years of
market prices, an ArangoDB graph joining them, and the same web app — the one
that says whether a geopolitical shock moved markets, and says so when it did not.

It takes a few minutes — under three on a fast connection, most of it downloading —
and needs **no accounts, no keys and no licences**.
GDELT publishes its daily files openly, Yahoo's price history needs no key, and
ArangoDB runs from its free Docker image.

## What you need

- Docker, running
- Python 3.11+ with this repo's requirements: `pip install -r requirements.txt`
- About 3 GB of disk while it downloads; it keeps far less

## Run it

```bash
starter/run.sh
```

Then open <http://127.0.0.1:8811>. Seven steps run first, each one a script from
`../scripts` — the production pipeline, pointed at a local database:

| step | what happens |
|---|---|
| 1 | ArangoDB starts in Docker on port 8539 and a `gdelt_starter` database is created |
| 2 | GDELT events and news coverage for September 2019 — the month of the Abqaiq strike — filtered to eleven countries |
| 3 | Daily prices 2016–2024 for the S&P, the eleven sectors, crude, gold and five stocks |
| 4 | The graph: events, actors, places, news themes, sectors and a sample portfolio |
| 5 | Price series, "normal day" baselines, and the event-study edges |
| 6 | Who acted and what was hit, read from the news coverage |
| 7 | The app starts |

## Try these

- **Pick "one specific event", choose *Abqaiq/Khurais attack*.** Brent crude
  +14.61%, five times a normal day, while the S&P barely moved. Your portfolio is
  measured beside it.
- **Type *"What if the Houthis attack Saudi Aramco?"*** The question is read into
  the form, shown back, and asked only when you confirm.
- **Ask a class question**, such as *"What if the US imposes tariffs on China?"*,
  and expect *not detectable*, or *too few events* on one month of data. That
  is the point: the app tells you when it does not know.

## Make it yours

- **A different month:** `STARTER_FROM=2020-03-01 STARTER_TO=2020-03-31 starter/run.sh`
  (the Saudi-Russia price war). Stay inside 2018-02-27 .. 2022-03-31 for the
  baselines to cover it.
- **Your own holdings:** edit `DEMO_PORTFOLIO` in `scripts/build_graph.py` (tickers
  and weights), add the tickers to `UNIVERSE` in `scripts/market_data_pull.py`, and
  re-run.
- **Rates and inflation expectations:** set a free `FRED_API_KEY` before running
  and the single-day view gains Treasury yields, breakevens and the dollar.
- **More history:** the full system runs the same scripts over 13 years and 21
  million events; [docs/operations.md](../docs/operations.md) describes it.

## Stop or start over

```bash
starter/run.sh --serve    # serve what is already built
starter/run.sh --down     # stop ArangoDB and delete the starter data
```

## What one month cannot do

The **class** questions — "what do events like this do?" — need years of analogs.
On one month most come back *too few events to test*. The full corpus answers
them, and its answer is mostly *no*: 405,987 variants tested, none survives
correction for how many were tried. Single shocks are a different matter, and
one month is enough to see one.
