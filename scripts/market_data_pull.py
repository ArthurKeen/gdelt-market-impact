"""Pull years of market price history -> data/prices.jsonl.

Feeds the event-study `correlatesWith`/`affects` edges (build_inferred_edges.py):
realized moves of sector ETFs / indices / commodities in the days after each
historical analog. Free sources (see docs/market-data.md):
  - yfinance  : indices, sector ETFs, commodities, company tickers (daily OHLCV)
  - FRED      : daily yields, breakeven, dollar — optional, needs a free
                FRED API key in FRED_API_KEY (or MARKET_DATA_API_KEY).

Output rows: {symbol, assetClass, date, open, high, low, close, adjClose, volume}.

Examples:
  python scripts/market_data_pull.py --from 2016-01-01 --to 2024-12-31
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

# The core assets (indices, S&P sector ETFs, energy/gold commodities) + a
# couple of example tickers. Extend per portfolio.
UNIVERSE: dict[str, list[str]] = {
    "index": ["^GSPC", "^NDX", "^VIX"],
    "sector_etf": ["XLE", "XLF", "XLK", "XLI", "XLY", "XLP", "XLV", "XLU", "XLB", "XLRE", "XLC"],
    "commodity": ["CL=F", "BZ=F", "GC=F"],          # WTI, Brent, gold futures
    "company": ["AAPL", "NVDA", "XOM", "JPM", "BA"],  # examples
}
# Who ELSE feels a Gulf shock (Phase 1 of the exposure work, 2026-11-17). The US
# imports little Gulf crude; Japan, Korea, India and China import a great deal.
# Qatar supplies roughly a third of the world's helium, which chip fabs need. The
# Gulf exports much of the world's seaborne urea, ammonia and sulfur. Ships carry
# all of it through Hormuz, Bab el-Mandeb and Suez.
#
# Class "exposure" on purpose: these are shown BESIDE a single-day answer and
# never inside it. study_targets() ignores the class, so they enter no baseline,
# no verdict, no correlatesWith edge and no sweep — adding 15 series to the
# verdict would make "detectable" easier by luck and move published answers.
# Group says why each is here; the helium proxies are weak and labelled so.
EXPOSURE: dict[str, tuple[str, str]] = {
    "^N225":       ("Nikkei 225", "Asia"),
    "^KS11":       ("KOSPI", "Asia"),
    "^TWII":       ("Taiwan TAIEX", "Asia"),
    "^NSEI":       ("Nifty 50", "Asia"),
    "000001.SS":   ("Shanghai Composite", "Asia"),
    "^HSI":        ("Hang Seng", "Asia"),
    "JPY=X":       ("Yen per dollar", "Asia"),
    "2330.TW":     ("TSMC", "Semiconductors"),
    "005930.KS":   ("Samsung Electronics", "Semiconductors"),
    "LIN":         ("Linde", "Helium proxy (weak)"),
    "APD":         ("Air Products", "Helium proxy (weak)"),
    "CF":          ("CF Industries", "Fertilizer"),
    "YAR.OL":      ("Yara", "Fertilizer"),
    "MAERSK-B.CO": ("Maersk", "Shipping"),
    "FRO":         ("Frontline", "Shipping"),
}
UNIVERSE["exposure"] = list(EXPOSURE)
# FRED macro series (only pulled if a FRED key is present). DAILY series only:
# CPI and Fed funds are monthly, and correlating a monthly print with a daily
# sector return measures nothing. Rates are in percent, so a day's move is a
# change in basis points, never a percentage of the rate — 0.05% -> 0.08% is
# +3bp, not +60%. `unit` carries that to every consumer.
FRED_SERIES = {
    "DGS10":    ("UST10Y", "percent"),        # 10-year Treasury yield
    "DGS2":     ("UST2Y", "percent"),         # 2-year Treasury yield
    "T10YIE":   ("Breakeven10Y", "percent"),  # 10-year breakeven inflation
    "DTWEXBGS": ("USD broad", "index"),       # trade-weighted dollar
}


def pull_yfinance(symbols_by_class: dict[str, list[str]], start: str, end: str, out) -> int:
    import yfinance as yf

    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for asset_class, symbols in symbols_by_class.items():
            for sym in symbols:
                df = yf.download(sym, start=start, end=end, auto_adjust=False,
                                 progress=False, threads=False)
                if df is None or df.empty:
                    print(f"  [yfinance] {sym}: no data")
                    continue
                for ts, row in df.iterrows():
                    def val(col):
                        v = row.get(col)
                        try:
                            return float(v.iloc[0]) if hasattr(v, "iloc") else (float(v) if v == v else None)
                        except Exception:
                            return None
                    extra = ({"name": EXPOSURE[sym][0], "group": EXPOSURE[sym][1]}
                             if sym in EXPOSURE else {})
                    fh.write(json.dumps({
                        "symbol": sym, "assetClass": asset_class, **extra,
                        "date": ts.strftime("%Y-%m-%d"),
                        "open": val("Open"), "high": val("High"), "low": val("Low"),
                        "close": val("Close"), "adjClose": val("Adj Close"), "volume": val("Volume"),
                    }) + "\n")
                    n += 1
                print(f"  [yfinance] {sym} ({asset_class}): {len(df):,} rows")
    return n


def pull_fred(series: dict[str, tuple[str, str]], start: str, end: str, out) -> int:
    key = os.environ.get("FRED_API_KEY") or os.environ.get("MARKET_DATA_API_KEY")
    if not key:
        print("  [fred] no FRED_API_KEY — skipping macro series (optional)")
        return 0
    try:
        from fredapi import Fred
    except ImportError:
        print("  [fred] fredapi not installed — skipping (pip install fredapi)")
        return 0
    fred = Fred(api_key=key)
    n = 0
    with out.open("a", encoding="utf-8") as fh:
        for series_id, (name, unit) in series.items():
            s = fred.get_series(series_id, observation_start=start, observation_end=end)
            for ts, v in s.items():
                if v != v:
                    continue
                fh.write(json.dumps({"symbol": series_id, "assetClass": "macro",
                                     "name": name, "unit": unit,
                                     "date": ts.strftime("%Y-%m-%d"),
                                     "close": float(v)}) + "\n")
                n += 1
            print(f"  [fred] {series_id} ({name}): {len(s):,} obs")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbols", default=None, help="comma-separated override (assetClass=company)")
    ap.add_argument("--out", default="data/prices.jsonl")
    args = ap.parse_args()

    universe = {"company": [s.strip() for s in args.symbols.split(",")]} if args.symbols else UNIVERSE
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    n_px = pull_yfinance(universe, args.start, args.end, out)
    n_macro = pull_fred(FRED_SERIES, args.start, args.end, out)
    print(f"[market_data_pull] {n_px:,} price rows + {n_macro:,} macro rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
