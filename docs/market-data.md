# Market price data — provider choice and tradeoffs

The price history is a
**hard dependency**: `build_inferred_edges.py` derives the
`correlatesWith`/`affects` edge weights (magnitude, direction, time-to-impact)
from realized moves after each historical analog. No prices → no causal weights.

## Decision

| Leg | Provider | Key needed | What it supplies |
|---|---|---|---|
| Prices | **yfinance** | none | Daily OHLCV for indices, sector ETFs, commodity futures, company tickers |
| Macro | **FRED** (St. Louis Fed) | free API key | UST10Y, UST2Y, 10y breakeven, broad dollar (daily) |

Both are free. Implemented in [`scripts/market_data_pull.py`](../scripts/market_data_pull.py)
and validated live on 2026-08-11.

**Why yfinance:** it needs no account, no key, and no procurement
conversation, and it covers all four asset classes the §5 scenarios reference in
one interface. The success criteria here are about *explainability of the graph*,
not about tick fidelity — daily adjusted closes are the correct granularity for
an event study measured in days-after. Paying a vendor at this stage would buy
precision the method does not consume.

## What is on disk

`data/prices.jsonl` — 49,161 rows, **2016-01-04 → 2024-12-30**:

| assetClass | Symbols | Rows |
|---|---|---|
| `index` | `^GSPC`, `^NDX`, `^VIX` | 6,789 |
| `sector_etf` | all 11 SPDR sectors (`XLE`…`XLC`) | 24,274 |
| `commodity` | `CL=F` (WTI), `BZ=F` (Brent), `GC=F` (gold) | 6,783 |
| `company` | `AAPL`, `NVDA`, `XOM`, `JPM`, `BA` (examples) | 11,315 |
| `macro` | `DGS10`, `DGS2`, `T10YIE`, `DTWEXBGS` | 13,822 (2013-01 → 2026-10, added 2026-11-16) |

Nine years of daily history, bracketing the GDELT corpus window
(2018-02 → 2022-03) with pre/post buffer on both sides for estimation windows.

## Who else felt it: the exposure series (2026-11-17)

Fifteen Yahoo series, `assetClass: "exposure"`, each tagged with a `name` and a
`group` saying why it is there (`EXPOSURE` in `scripts/market_data_pull.py`):

| group | series |
|---|---|
| Asia | Nikkei 225, KOSPI, Taiwan TAIEX, Nifty 50, Shanghai Composite, Hang Seng, yen per dollar |
| Semiconductors | TSMC (2330.TW), Samsung Electronics (005930.KS) |
| Fertilizer | CF Industries, Yara |
| Shipping | Maersk, Frontline |
| Helium proxy (weak) | Linde, Air Products — helium has no traded price, and it is a small part of their business |

All run from 2013. Dutch TTF gas is not available from Yahoo. They are context on
the single-day view and are excluded from every baseline, verdict, edge and the
sweep. They are measured raw, not net of the S&P, because they trade at other
hours, and each from its own calendar, so a market shut for a holiday shows its
next session. Some are beneficiaries, not victims: a US fertilizer maker gains
when Gulf supply is cut.

## Daily series only, and rates in basis points (2026-11-16)

The key arrived with the October refresh, which pulled the original four series —
CPI, Fed funds, the 10-year yield and the 10-year breakeven — straight into the
percentage-return path every price uses. Two things were wrong in kind:

- **A rate's day is a change in level.** 0.05% → 0.08% is +3bp; as a "return" it
  is +60%, and near the zero bound Fed funds produced exactly those numbers.
- **CPI and Fed funds are monthly.** Correlating a monthly print with a daily
  sector return measured nothing; CPI → Energy read 0.39 and meant nothing.

So the pull is now daily series only, each row carries a `unit` (`percent` for
yields and breakevens, `index` for the dollar), and `affects` correlates *changes*
for `percent` series (`method: daily-change-correlation`). The 22 edges from the
monthly series are gone, and `build_inferred_edges.py` now prunes any `affects`
edge it no longer produces. The four are Federal Reserve Board series — US
government works, public domain — so the licensing analysis below still holds.

What the channel shows: the 10-year breakeven moves with Energy (+0.40), the dollar
against Materials (−0.34). And on the day, the single-event view puts the rates
beside the answer: Abqaiq moved crude +14.68% and the 10-year breakeven **0bp** —
the bond market read it as transient — while the 2020 price war took breakevens
down **32bp, 9.7×** a normal day. Context only: these series never enter a
verdict, a baseline or the sweep.

To add or change a series without a full event reload:

```bash
python scripts/build_graph.py --data data/full --market-only   # MacroVariable nodes
python scripts/build_inferred_edges.py --data data/full --window delivered
python scripts/build_inferred_edges.py --data data/full --window full --baselines-only
```

## The FRED leg is not optional in practice

`commodity_affects()` selects `assetClass in ("commodity", "macro")` as the source
side of every `affects` edge
([`build_inferred_edges.py:286`](../scripts/build_inferred_edges.py#L286)). With
zero macro rows, that entire edge type is currently derived from three
oil-and-gold futures.

For a **tariff** scenario specifically, the rates-and-inflation-expectations
channel into sectors is arguably the more economically meaningful transmission
mechanism than crude — tariffs are a price-level shock before they are an energy
shock. Adding `UST10Y`, `T10YIE`, `CPIAUCSL` and `FEDFUNDS` as propagation
sources materially widens what E1 can explain.

A secondary benefit matters for success criterion R4 (auditable, not a black
box): FRED series are official, permanently addressable, and revision-tracked, so
an edge weight derived from them is citable in a way one derived from a scraped
endpoint is not.

Get a key at <https://fred.stlouisfed.org/docs/api/api_key.html> — free account,
issued immediately, no payment details.

## Licensing and terms

Reviewed 2026-08-11 against <https://fred.stlouisfed.org/docs/api/terms_of_use.html>
and <https://fred.stlouisfed.org/legal>. **Conclusion: yes, we can live with FRED's
conditions** — for our four series and our usage pattern the terms are
substantially less restrictive than yfinance's. Note that both pages block
automated fetching, so a human should re-read them before relying on this;
the assessment below is from knowledge of the terms, not a fetched copy.

The conditions, and why each is satisfied here:

| Condition | Our position |
|---|---|
| **Attribution required** — cite FRED / the underlying source | Cheap to satisfy. `assetClass: "macro"` rows carry `symbol` (the FRED series id), so provenance is already in the graph; add a source line to any demo output. |
| **Third-party copyrighted series** — FRED redistributes some proprietary series and pushes responsibility for the source's copyright onto the user. **This is the clause that actually bites.** | **Does not apply to our four.** `DGS10`, `DGS2`, `T10YIE` and `DTWEXBGS` are Federal Reserve Board / Treasury (the earlier `CPIAUCSL` was BLS). All are US federal government works, hence public domain (17 U.S.C. §105) and uncopyrighted. Confirm the per-series copyright notice before adding any series from a private provider (e.g. index or survey data). |
| **No implied endorsement** — do not use the Federal Reserve Bank of St. Louis's name or logo to suggest it backs the product | Trivially satisfied; keep FRED out of branding and describe it only as a data source. |
| **Rate limit** (on the order of ~120 req/min per key) | Irrelevant. Four series, one historical pull, cached to `prices.jsonl`. |
| **No warranty; data "as is"; terms may change; access may be revoked** | Acceptable for research. Mitigated by the fallback below — the same series exist outside FRED. |
| **Redistribution** | **We do not redistribute the series.** What lands in ArangoDB and what the what-if answer returns are *derived statistics* — daily-return correlation coefficients on `affects` edges. That is a computed aggregate, not republication of the underlying observations. This is the main reason the terms are comfortable here. |

Net: FRED is *less* encumbered than the price leg. yfinance carries no
redistribution right at all and no contract of any kind; FRED gives explicit
permission with attribution, for public-domain federal series.

### Zero-terms fallback

If a legal review prefers no third-party terms whatsoever, every series we
use is available from its primary federal source, all public domain:

| Series | Primary source |
|---|---|
| `CPIAUCSL` | BLS public API |
| `FEDFUNDS`, `DGS10` | Federal Reserve Board H.15 release (CSV) |
| `T10YIE` | Derived from Treasury par-yield and TIPS series (`fiscaldata.treasury.gov` API) |

This costs a modest amount of adapter code and removes FRED from the dependency
chain entirely. Not worth doing for a prototype; worth knowing it exists if
procurement asks.

## Where yfinance stops being adequate

Worth stating plainly, because these surface late:

- **No SLA, no support, no contract.** It is an unofficial client against Yahoo
  Finance's internal endpoints. It breaks without notice when Yahoo changes
  response shapes — historically several times a year.
- **No redistribution right.** Yahoo's terms do not grant onward distribution.
  Fine for research and a demo you run yourself; not fine if the price data is
  republished. This repository ships no price data: every user pulls their own.
- **Corporate actions are best-effort.** `adjClose` handling for splits and
  dividends is less rigorous than a paid vendor's, and delisted tickers thin out —
  a survivorship bias if the universe is ever expanded to a historical index
  membership.
- **No point-in-time index membership.** The SPDR sector constituents are today's,
  not those as of the event date.
- **Daily only, effectively.** Intraday endpoints exist but are rate-limited and
  short-history.

None of these invalidate the research. All of them become blocking for a
production deployment, an SLA, or redistribution.

## Production path

For a licensed source, in order of fit for *this* workload (daily
bars, broad symbol coverage, real licensing). Pricing moves — treat these as
order-of-magnitude and confirm current rates:

| Provider | Fit | Notes |
|---|---|---|
| **Polygon.io** | Best overall | Clean REST, ~20 yrs equities/ETF history, proper licensing. Entry equities tier is low tens of USD/month. No meaningful futures coverage. |
| **Financial Modeling Prep** | Cheapest full coverage | Adds fundamentals; corporate-action rigor somewhat below Polygon. |
| **EODHD** | Good global breadth | Strong non-US coverage, inexpensive. |
| **Databento** | Only for intraday / real futures | CME-direct, pay-as-you-go, priced for serious volume. |

### Cost tip: swap futures for ETF proxies

**Futures licensing is the expensive line item** in any market-data contract —
exchange entitlements (CME for WTI/gold, ICE for Brent) are charged separately
from equities and often dwarf the base subscription.

If we move to a licensed equities-only vendor, replace the futures symbols in
`UNIVERSE` ([`market_data_pull.py:31`](../scripts/market_data_pull.py#L31)):

| Current | Proxy |
|---|---|
| `CL=F` (WTI) | `USO` |
| `BZ=F` (Brent) | `BNO` |
| `GC=F` (gold) | `GLD` |

`commodity_affects()` uses **daily-return correlation**, which is near-identical
between a front-month future and its tracking ETF over a daily horizon. The
substitution keeps the whole pipeline inside a plain equities entitlement. (The
ETFs carry roll and expense drag in *levels*, so this substitution is safe for
correlation-based edges and would need revisiting if a future metric depended on
absolute price paths.)

## Environment variables

```bash
MARKET_DATA_PROVIDER=yfinance   # documentation only — no code reads it
MARKET_DATA_API_KEY=            # unused by yfinance; FRED fallback slot
FRED_API_KEY=<free key>         # checked first by pull_fred()
```

`pull_fred()` reads `FRED_API_KEY` and falls back to `MARKET_DATA_API_KEY`
([`market_data_pull.py:69`](../scripts/market_data_pull.py#L69)). Prefer the
dedicated name. Without either, the macro pull is skipped with a warning and the
run still succeeds.

## Re-pulling

```bash
. .venv/bin/activate                      # `pip` exists only inside the venv
pip install fredapi
python scripts/market_data_pull.py --from 2016-01-01 --to 2024-12-31
python scripts/build_inferred_edges.py    # rebuild affects with macro sources
```

Note that `market_data_pull.py` opens the output with `"w"` for prices and `"a"`
for macro, so a full re-run rewrites the file cleanly; running only the FRED half
would append to an existing file.
