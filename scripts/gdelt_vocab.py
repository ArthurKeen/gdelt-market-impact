"""GDELT theme vocabulary + scenario episode calendars — single source of truth.

Both pullers (bigquery_pull.py, gdelt_http_pull.py) import from here so the
curated slice is defined once.

WHY THIS FILE EXISTS (validated 2026-08-11 against a real GKG day):
The original curated theme list did not survive contact with the data.
Measured on `data.gdeltproject.org/gkg/20180322.gkg.csv` (2018-03-22, the day of
the Section 301 tariff memo — the most tariff-dense day in the E1 corpus;
157,784 articles):

  ECON_TARIFFS          ->      0 hits.  NOT a GDELT theme token. Filtering on it
                                 returns an empty slice.
  ECON_SANCTIONS        ->      0 hits.  The real token is bare `SANCTIONS`.
  EPU_POLICY_MONETARY   ->      0 hits.  The real token is `EPU_POLICY_MONETARY_POLICY`.
  TAX_FNCACT            -> 128,386 hits (81% of ALL articles). This is GDELT's
                                 "functional actor" tag — it fires on any job title
                                 ("artist", "coordinator"). As a filter it is a
                                 stopword: it turns the "curated slice" into the
                                 full firehose.
  ENV_OIL               ->  4,837 hits.  Valid.
  ARMEDCONFLICT         -> 20,934 hits.  Valid.

Counts below are from that same day and are indicative, not contractual.
"""
from __future__ import annotations

import re

# Theme groups. Keys are our own labels; values are real GDELT theme tokens.
THEME_GROUPS: dict[str, list[str]] = {
    # E1 — tariffs / trade war. `ECON_TRADE_DISPUTE` is the workhorse token that
    # `ECON_TARIFFS` was supposed to be.
    "trade": [
        "ECON_TRADE_DISPUTE",                    # 2,598
        "EPU_CATS_TRADE_POLICY",                 # 2,319
        "WB_775_TRADE_POLICY_AND_INTEGRATION",   # 2,182
        "ECON_FREETRADE",                        # 1,005
        "TAX_ECON_FREETRADEAGREEMENTS",          #   929
        "EPU_POLICY_WORLD_TRADE_ORGANIZATION",   #   779
        "WB_778_NON_TARIFF_MEASURES",            #   253  (closest literal "tariff" token)
    ],
    # E2 — oil / Hormuz.
    "energy": [
        "ENV_OIL",                               # 4,837
    ],
    "sanctions": [
        "SANCTIONS",                             # 2,763
    ],
    "conflict": [
        "ARMEDCONFLICT",                         # 20,934
    ],
    # E4 — monetary policy surprise (deliberately low-confidence demo).
    "monetary": [
        "EPU_CATS_MONETARY_POLICY",              # 3,874
        "ECON_INTEREST_RATES",                   # 2,894
        "ECON_CENTRALBANK",                      # 1,874
        "EPU_POLICY_MONETARY_POLICY",            #   800
    ],
    # Market-reaction context (useful for the Policy-Reaction pattern, S4).
    "market": [
        "ECON_STOCKMARKET",                      # 9,863
        "ECON_WORLDCURRENCIES",                  # 5,877
    ],
}

# Themes rejected during validation, kept so nobody re-adds them.
REJECTED_THEMES: dict[str, str] = {
    "ECON_TARIFFS": "0 hits on the peak tariff day — not a GDELT theme token",
    "ECON_SANCTIONS": "0 hits — use bare SANCTIONS",
    "EPU_POLICY_MONETARY": "0 hits — use EPU_POLICY_MONETARY_POLICY",
    "TAX_FNCACT": "81% of all articles (job-title tag) — stopword, not a filter",
}

CURATED_THEMES: list[str] = [t for group in THEME_GROUPS.values() for t in group]


def theme_regex(themes: list[str] | None = None) -> str:
    """Anchored alternation matching whole theme tokens.

    GDELT theme fields are `;`-delimited. GKG 1.0 is bare tokens (`ENV_OIL;...`);
    GKG 2.0 V2Themes appends a char offset (`ENV_OIL,1234;...`). Anchoring on
    `(^|;)` ... `(,|;|$)` matches a whole token in both, which a bare
    `|`-joined alternation does not: unanchored, `SANCTIONS` also matches
    `WB_1234_SANCTIONS_REGIME` and `ENV_OIL` matches `ENV_OILSPILL`.
    """
    toks = "|".join(re.escape(t) for t in (themes or CURATED_THEMES))
    return f"(^|;)({toks})(,|;|$)"


def compiled_theme_regex(themes: list[str] | None = None):
    return re.compile(theme_regex(themes))


# --- Scenario episode calendars -----------------------------------
# Known policy dates used to target the curated pull. Analog retrieval is *not*
# limited to these — they define which days we ingest, so the corpus actually
# contains the episodes the demo reasons about.

EPISODES: dict[str, list[tuple[str, str]]] = {
    # E1 · Tariff shock (priority)
    "e1": [
        ("2018-03-01", "Section 232 steel/aluminium tariffs announced"),
        ("2018-03-22", "Section 301 memorandum signed (China IP)"),
        ("2018-04-03", "USTR publishes $50B tariff list"),
        ("2018-06-15", "$50B list confirmed"),
        ("2018-07-06", "First $34B tariffs take effect"),
        ("2018-08-23", "$16B tranche takes effect"),
        ("2018-09-17", "$200B tranche announced"),
        ("2018-12-01", "G20 Buenos Aires truce"),
        ("2019-05-05", "Threat to raise $200B to 25%"),
        ("2019-05-10", "25% rate takes effect"),
        ("2019-06-29", "G20 Osaka truce"),
        ("2019-08-01", "$300B tranche threatened"),
        ("2019-08-23", "China retaliation + US rate increase"),
        ("2019-09-01", "15% tranche takes effect"),
        ("2019-12-13", "Phase One deal announced"),
        ("2020-01-15", "Phase One agreement signed"),
    ],
    # E2 · Oil / Strait of Hormuz (priority)
    "e2": [
        ("2019-05-12", "Fujairah tanker attacks"),
        ("2019-06-13", "Gulf of Oman tanker attacks"),
        ("2019-06-20", "US surveillance drone shot down"),
        ("2019-07-19", "Stena Impero seizure"),
        ("2019-09-14", "Abqaiq/Khurais attack (largest supply shock)"),
        ("2020-01-03", "Soleimani strike"),
        ("2022-03-08", "US ban on Russian oil imports"),
    ],
}
