"""Who depends on what passes through the Gulf: the curated half of the exposure graph.

Everything here is a published figure with its source, checked 2026-11-17. Where
sources disagree the edge carries a range (`lo`/`hi`) rather than a chosen point,
and where no figure was verified the share is `None` and the note says so — a
qualitative edge is honest; an invented percentage is not. tests/test_pure.py
holds every edge to having a source.

The regions are the boxes stated in docs/exposure-hypotheses.md before any test
ran. They are defined once, here, so the hypothesis script and the graph cannot
disagree about what "in the Gulf" means.
"""
from __future__ import annotations

AS_OF = "2026-11-17"

EIA_HORMUZ = ("EIA, Today in Energy: the Strait of Hormuz (2024 data)",
              "https://www.eia.gov/todayinenergy/detail.php?id=65504")
EIA_REDSEA = ("EIA, Today in Energy: Red Sea diversions (Vortexa data)",
              "https://www.eia.gov/TODAYinENERGY/detail.php?id=63446")
USGS_QATAR = ("USGS National Minerals Information Center: Qatar",
              "https://www.usgs.gov/centers/national-minerals-information-center/qatar")
METI_JAPAN = ("METI monthly crude import statistics, via Arab News (2024)",
              "https://www.arabnews.pk/node/2588618")
SPG_ASIA = ("S&P Global Commodity Insights, Asian refiners and Middle East crude (Nov 2024)",
            "https://www.spglobal.com/energy/en/news-research/latest-news/crude-oil/112024-commodities-2025-asian-refiners-expect-middle-east-crude-suppliers-to-defend-far-east-market-share")
PEW_TAIWAN = ("Pew Research Center, Middle East oil dependence (2024)",
              "https://www.pewresearch.org/chart/sr_26-07-06_usaoil_chart6/")
FERT = ("Fertilizer Daily / NDSU, ICC and Yara estimates of Hormuz fertilizer trade",
        "https://www.fertilizerdaily.com/20260807-un-itc-hormuz-disruption-urea-exports-83-percent-drop/")
TENK = ("Company annual reports (10-K): TSMC named as foundry partner", "https://www.sec.gov/edgar")

# lat/lng boxes. HORMUZ is the pre-registered one; do not tune it.
REGIONS = {
    "hormuz":       {"name": "Strait of Hormuz & the Gulf", "lat": (22.0, 30.5), "lng": (47.5, 60.0)},
    "bab_el_mandeb": {"name": "Bab el-Mandeb & southern Red Sea", "lat": (11.0, 20.5), "lng": (38.0, 45.5)},
    "suez":         {"name": "Suez Canal", "lat": (29.5, 31.5), "lng": (32.0, 33.0)},
}

GOODS = {
    "crude":   {"name": "Crude oil", "pricedBy": "Commodity/BZ=F"},
    "lng":     {"name": "Liquefied natural gas"},
    "helium":  {"name": "Helium", "note": "no traded price"},
    "urea":    {"name": "Urea (nitrogen fertilizer)"},
    "ammonia": {"name": "Ammonia"},
    "sulfur":  {"name": "Sulfur"},
}

COUNTRIES = {
    "JPN": "Japan", "KOR": "South Korea", "TWN": "Taiwan", "IND": "India",
    "CHN": "China", "USA": "United States", "SAU": "Saudi Arabia", "QAT": "Qatar",
}

# (good, chokepoint, share, lo, hi, measure, source)
FLOWS = [
    ("crude", "hormuz", 0.20, None, None,
     "of global petroleum liquids consumption (about 20 million b/d, 2024); "
     "more than a quarter of seaborne oil trade", EIA_HORMUZ),
    ("lng", "hormuz", 0.20, None, None, "of global LNG trade, mostly from Qatar (2024)", EIA_HORMUZ),
    ("helium", "hormuz", 0.35, None, None,
     "of world helium production (2024): Qatar's, exported from Ras Laffan "
     "through the Strait", USGS_QATAR),
    ("urea", "hormuz", None, 0.34, 0.45, "of traded urea; estimates differ by source", FERT),
    ("sulfur", "hormuz", None, 0.44, 0.50, "of traded sulfur; estimates differ by source", FERT),
    ("ammonia", "hormuz", None, 0.20, 0.30, "of traded ammonia; estimates differ by source", FERT),
    ("crude", "bab_el_mandeb", None, None, None,
     "8.7 million b/d in 2023; about 4.0 million b/d in Jan-Aug 2024 after the "
     "Houthi attacks diverted tankers around the Cape", EIA_REDSEA),
]

# Every DEPENDS and PRODUCES figure below describes the Gulf route: Middle East
# crude reaches Asia through Hormuz, not the Red Sea. The edges carry
# via="hormuz" so a Red Sea day does not list Japan's 95% as if it applied there.
VIA = "hormuz"

# (country, good, share, lo, hi, measure, source)
DEPENDS = [
    ("JPN", "crude", 0.95, 0.95, 0.98, "of crude imports from the Middle East, each month of 2024", METI_JAPAN),
    ("KOR", "crude", 0.709, None, None, "of crude imports from the Middle East, Jan-Sep 2024", SPG_ASIA),
    ("IND", "crude", 0.459, None, None, "of crude imports from the Middle East, H1 2024", SPG_ASIA),
    ("TWN", "crude", 0.63, None, None, "of oil consumption from the Middle East (consumption basis), 2024", PEW_TAIWAN),
    ("USA", "crude", 0.07, None, None, "of US crude imports came through Hormuz (2% of consumption), 2024", EIA_HORMUZ),
    ("CHN", "crude", None, None, None,
     "China, India, Japan and South Korea together took 69% of Hormuz crude in 2024; "
     "China's own share was not verified", EIA_HORMUZ),
]

# (country, good, share, measure, source)
PRODUCES = [
    ("SAU", "crude", 0.38, "of crude flows through Hormuz (5.5 million b/d), 2024", EIA_HORMUZ),
    ("QAT", "helium", 0.35, "of world helium production, 2024", USGS_QATAR),
    ("QAT", "lng", None, "most of the LNG through Hormuz, 2024", EIA_HORMUZ),
]

# Exposure markets as graph nodes: (symbol, collection, name, country)
MARKETS = [
    ("^N225", "Index", "Nikkei 225", "JPN"), ("^KS11", "Index", "KOSPI", "KOR"),
    ("^TWII", "Index", "Taiwan TAIEX", "TWN"), ("^NSEI", "Index", "Nifty 50", "IND"),
    ("000001.SS", "Index", "Shanghai Composite", "CHN"), ("^HSI", "Index", "Hang Seng", "CHN"),
    ("^GSPC", "Index", "S&P 500", "USA"),
    ("2330.TW", "Company", "TSMC", "TWN"), ("005930.KS", "Company", "Samsung Electronics", "KOR"),
    ("LIN", "Company", "Linde", None), ("APD", "Company", "Air Products", None),
    ("CF", "Company", "CF Industries", "USA"), ("YAR.OL", "Company", "Yara", None),
    ("MAERSK-B.CO", "Company", "Maersk", None), ("FRO", "Company", "Frontline", None),
]

# What a company does with a good or a route. `role` is the point: a fertilizer
# maker outside the Gulf GAINS when Gulf supply is cut. (company, target, role, note)
USES = [
    ("2330.TW", "Good/helium", "consumes", "chip fabrication uses helium; quantity not public"),
    ("005930.KS", "Good/helium", "consumes", "chip fabrication uses helium; quantity not public"),
    ("LIN", "Good/helium", "sells", "helium is a small part of revenue: a weak proxy"),
    ("APD", "Good/helium", "sells", "helium is a small part of revenue: a weak proxy"),
    ("CF", "Good/urea", "competes", "produces nitrogen fertilizer outside the Gulf; gains when Gulf supply is cut"),
    ("YAR.OL", "Good/ammonia", "competes", "produces nitrogen fertilizer in Europe and elsewhere"),
    ("FRO", "Good/crude", "transports", "crude tankers; rates rise with Gulf risk"),
    ("MAERSK-B.CO", "Chokepoint/bab_el_mandeb", "transits", "container lines reroute around the Cape"),
    ("MAERSK-B.CO", "Chokepoint/suez", "transits", "container lines reroute around the Cape"),
]

# (customer, supplier, customer's name, note) — reaches the demo portfolio's holdings.
SUPPLIED_BY = [
    ("NVDA", "2330.TW", "NVIDIA", "TSMC manufactures NVIDIA's chips"),
    ("AAPL", "2330.TW", "Apple", "TSMC manufactures Apple's chips"),
]
