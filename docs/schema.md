# Physical schema — ArangoDB (property graph)

A multi-collection property graph: one collection per kind of thing, rather than a single `Node` collection with labels.
Named graph: **`gdelt_market`**. All collections below are created by
`scripts/build_graph.py`.

## Node (document) collections

| Collection | `_key` strategy | Key fields | Notes |
|---|---|---|---|
| `Event` | GDELT `GLOBALEVENTID` | `cameo`, `cameoRoot`, `goldstein`, `avgTone`, `gkgTone`, `date`, `dateAdded`, `year`, `month`, `actorFromCode`, `actorToCode`, `geoCountry`, `themes[]`, `isDayAnchor` | one row per GDELT event. **`date` = when it happened, `dateAdded` = when GDELT published it**; the event study keys off `dateAdded` (that is when a market could react). Actor codes, `geoCountry` and `themes[]` are denormalised onto the document so the analog view can facet on one record. |
| `Actor` | canonical CAMEO actor code | `code`, `name`, `country`, `type`, `aliases[]`, `mentionCount`, `resolution` | e.g. `USAGOV`, `CHNGOV`. Keyed on the CAMEO code (GDELT's own canonical id) with observed name spellings folded in as `aliases` — the measured duplication is name variants, not code collisions. `arango-entity-resolution` is the upgrade seam for one-entity-many-codes. |
| `Location` | geo id (ADM1/country) | `name`, `countryCode`, `lat`, `lng` | GDELT geography |
| `Theme` | theme tag | `name` | GKG themes, e.g. `ECON_TARIFFS`, `ENV_OIL` |
| `Source` | domain/url hash | `domain` | GKG source outlet |
| `Company` | ticker | `name`, `sector`, `country` | ticker/sector reference |
| `Sector` | sector name | `name`, `proxySymbol` | `proxySymbol` is the sector ETF (XLE, XLK, …) whose realised move the event study attributes to this sector — kept as a field rather than a new edge class |
| `Index` | symbol | `name` | S&P 500, sector ETFs |
| `Commodity` | symbol | `name` | WTI, Brent, gold |
| `MacroVariable` | FRED series id | `name` | 10y/2y Treasury yields, 10y breakeven, broad dollar — daily only |
| `Holding` | `<portfolio>_<ticker>` | `ticker`, `weight` | client position |
| `Portfolio` | portfolio id | `name`, `client` | |
| `Exposure` | derived | `sector`, `weight` | rollup |

## Edge collections

**Edge `_key` strategy (added 2026-08-12).** Every edge carries an explicit,
deterministic `_key` built by `build_graph.edge_key()` from its identifying parts:
`correlatesWith` is (theme, asset, horizon) → `Theme_ARMEDCONFLICT--Commodity_BZ=F--h1`,
`affects` is (source, sector), `mentions` is (event, actor, role), and the rest are
(from, to). This is load-bearing, not cosmetic: writes use
`import_bulk(on_duplicate="update")`, which can only match an existing row by
`_key`, so without one a rebuild *inserts a duplicate set* instead of updating —
measured at `correlatesWith` 1,088 → 2,176 and a full re-load doubling all 983,341
edges, with plausible-looking counts and silently double-counted traversals.
Verified collision-free across all 983,520 generated edges. `build_graph.py`
refuses a no-`--drop` load while any collection still holds pre-migration
random-key rows.

| Collection | from → to | Class | Notes |
|---|---|---|---|
| `mentions` | Event → Actor/Company | structural | deterministic; carries `role` (`actor1`/`actor2`) |
| `taggedWith` | Event → Theme | structural | GKG theme tagging, carries `articleCount` (how many articles tied this theme to this event). **Split out of `mentions` on 2026-08-11**: it is the first hop of the causal chain, so the demo traversal must not walk 336k Event→Actor edges and discard them. |
| `locatedIn` | Event → Location | structural | |
| `partOf` | Company → Sector, Sector → Index | structural | |
| `holds` | Portfolio → Holding → Company | structural | |
| `precedes` | Event → Event | temporal | **day-anchor spine**: most-mentioned event per (dyad, day), chained across consecutive days; carries `lagDays`, `dyad` |
| `coOccursWith` | Event → Event | temporal | same day + same dyad, **chained not cliqued**; carries `date`, `dyad` |
| `similarTo` | Event → Event | inferred | analog score in edge (`score`, `method`) |
| `correlatesWith` | **Theme** → Sector/Index/Commodity | inferred | **weighted**: `move`, `horizonDays`, `n`, `consistency` (event-study). The edge definition also permits `Event` as a source, but **none are built** — an analog reaches an asset via `Event -taggedWith-> Theme -correlatesWith-> …`, never straight out of the Event. |
| `affects` | Commodity/MacroVariable → Sector | inferred | second-order propagation weight |

## The exposure graph — `gdelt_exposure` (2026-11-17)

A second named graph, built by `scripts/build_exposure_graph.py` from the curated
figures in `scripts/exposure_reference.py`, so `build_graph.py` rebuilding
`gdelt_market` cannot drop its definitions. It answers "who depends on what passes
through where":

    Event -locatedIn-> Location -inRegion-> Chokepoint <-flowsThrough- Good
    Good <-dependsOn- Country <-listedIn- Index | Company
    Good <-produces- Country
    Good | Chokepoint <-uses- Company <-suppliedBy- Company <-holds- Holding

| Collection | Kind | Key | Notes |
|---|---|---|---|
| `Chokepoint` | node | `hormuz`, `bab_el_mandeb`, `suez` | `box` — the lat/lng region; `hormuz` is the box pre-registered in docs/exposure-hypotheses.md |
| `Good` | node | `crude`, `lng`, `helium`, `urea`, `ammonia`, `sulfur` | `order` for display; crude `pricedBy` Commodity/BZ=F |
| `Country` | node | ISO3 | the eight importers and producers in the reference data |
| `inRegion` | edge | Location → Chokepoint | **computed** from coordinates; stale edges pruned on rebuild |
| `flowsThrough` | edge | Good → Chokepoint | `share` or `lo`/`hi`, `measure`, `source`, `sourceUrl`, `asOf` |
| `dependsOn` | edge | Country → Good | as above, plus `via` — the route the figure describes |
| `produces` | edge | Country → Good | as above |
| `uses` | edge | Company → Good / Chokepoint | `role`: consumes, sells, competes (gains when Gulf supply is cut), transports, transits |
| `suppliedBy` | edge | Company → Company | `customer` name; reaches the portfolio's holdings (NVIDIA, Apple → TSMC) |
| `listedIn` | edge | Index / Company → Country | where each exposure market trades |
| `sameAs` | edge | Country → Actor | bridges to the corpus actors where the Actor exists |

Every published figure carries its source. Where sources disagree the edge holds a
range; where nothing was verified the share is null and the `measure` says so.

## Measured scale (1,494 continuous days, 2018-02-27..2022-03-31)
`Event` 5,521,304 · `Actor` 851 · `Location` 29,638 · `Theme` 16 · `Source` 135,038 ·
market+portfolio layers 50 · `mentions` 11,042,608 · `taggedWith` 6,423,136 ·
`locatedIn` 5,519,553 · `coOccursWith` 4,986,253 · `precedes` 516,703 ·
`correlatesWith` 1,088 · `affects` 77 · `partOf` 16 · `holds` 16 ·
`similarTo` 0 (not built — analog retrieval runs at query time by design) ·
`MacroVariable` 4 (since 2026-11-16: DGS10, DGS2, T10YIE, DTWEXBGS — daily series; the
monthly CPIAUCSL and FEDFUNDS were removed).
34,175,182 documents total.

Dyad-filtered to any two of USA/CHN/IRN/SAU/RUS/ISR/YEM/LBN/SYR/IRQ/PSE. The
last five are the regional theatres where non-state actors operate: GDELT has no
"Houthi" or "Hezbollah" actor and resolves them to a country code, so excluding
YEM excluded the Abqaiq attack itself (`YEM -> SAU`, CAMEO 190) and left only the
diplomatic aftermath.
69.5% of events carry themes (via the GKG `CAMEOEVENTIDS` join).
`mentions` + `taggedWith` = the 645,960 previously reported as `mentions` alone.

> **Density caveat on `correlatesWith`.** The Theme→Sector block is **complete
> bipartite**: 16 themes × 11 sectors × 4 horizons (1/3/5/10d) = exactly 704 edges,
> plus 192 each to `Index` and `Commodity`. Every theme correlates with every
> sector, so the *structure* carries no selectivity at that hop — all discrimination
> lives in the edge weights. Consequence for the what-if: any analog set reaches all
> 11 sectors, and the sector ranking is close to analog-independent. Structurally
> correct and auditable, but it means "tariffs" and "sanctions" currently produce
> near-identical sector orderings. Sparsifying by significance (drop edges whose
> `consistency`/`n` is weak) is the obvious next modelling step.

## Indexes
Created by `build_graph.py` (`INDEXES` dict — kept in code so it stays in sync):
- `Event`: persistent `[cameo]`, `[year, month]`, `[dateAdded]`, `[avgTone]`,
  `[actorFromCode, actorToCode]`, `[isDayAnchor, dateAdded]`.
- `Actor[code]`, `Theme[name]`, `Location[countryCode]`, `Holding[ticker]`.
- Temporal: `[dateAdded]` on `Event` serves the *windowed* half of R3;
  `precedes[lagDays]` serves the *sequential* half. `mentions[role]`,
  `taggedWith[articleCount]` (rank an event's themes by article support).
- Analog search: **ArangoSearch view** `event_analog` over `Event`+`Theme`+`Actor`
  facets (cameo, theme, actorFrom/To, geo, goldstein bucket); **vector index** on
  an event embedding (tone/text) for the hybrid re-rank.
- Vertex-centric indexes on the inferred edges once the traversal shape settles
  (they are engaged by direct-edge queries, not `p.edges[*]` traversals).

## Modeling notes
- **Structural half = mechanical** (table→collection, FK→edge): a plain
  relational-to-graph mapping that a generic loader could produce.
- **Inferred half = bespoke** (similarity + event-study) → lives in this repo.
- Keep the event graph queryable three ways: Policy-Reaction (tone over time),
  Global-Trends (macro risk), and the new causal/what-if traversal (S4).
