# Naming the protagonist and the target

*Measured 2026-08-27. Implemented by `scripts/extract_entities.py`.*

CAMEO says `YEM -> SAU`, code 190. An analyst wants to know that the Houthis hit
Abqaiq, the world's largest oil processing facility, and that Saudi Aramco
operates it. That gap — between the code and the question — is what this recovers.

## GDELT's own fields cannot name a non-state group

Not a modelling gap. A property of the source, and it is worth stating precisely
because it looks like something a better query would fix. On the Abqaiq strike:

| GDELT field | value |
|---|---|
| `Actor1Code` | `YEM` |
| `Actor1Name` | `YEMEN` / `YEMENI` — never `HOUTHI`, across all 371 strike-dyad events |
| `Actor1KnownGroupCode` | **empty**, on all 65 `YEM -> SAU` rows for 2019-09-14 |
| `Actor1Type1Code` | **empty**, likewise |

The known-group vocabulary exists and is populated — 1,853 of the 102,061 rows on
that day carry one, including `TAL` (Taliban), `ALQ` (al-Qaeda), `UNO` and `NAT`.
It simply has no Houthi code, and none is applied here. GDELT resolves non-state
actors to the country they operate from: the Houthis are `YEM`, Hezbollah `LBN`,
Hamas `PSE`.

## The coverage names them, so read the coverage

GKG carries three fields that were pulled and never loaded:

| field | what it gives |
|---|---|
| `V2Organizations` | the groups and companies the coverage names |
| `V2Persons` | leaders and spokesmen — `abdul malik al-houthi` lands here, not in organisations, so **both** fields have to be read |
| `V2Locations` | every place named, at facility resolution, not the single `ActionGeo` the Events table carries |

GKG links to Events through `CAMEOEVENTIDS`, so an event inherits the entities its
own coverage named. Shares are per **article**, and an entity is recorded when it
appears in at least 15% of an event's coverage (`--floor`), with at least 3 linked
articles (`--min-articles`).

**36,753 events carry at least one named entity** — 25,786 a group, 8,547 a
facility, 6,300 an operator.

| | top names |
|---|---|
| groups | Hamas 14,977 · IRGC/Quds 3,849 · Houthis 3,438 · AQAP 2,989 · Hezbollah 940 |
| operators | Saudi Aramco 5,729 · NIOC 598 · SABIC 192 |
| facilities | Abqaiq 3,938 · Khurais 3,163 · Jazan 3,020 · Yanbu 612 · Ras Tanura 358 |

## What it gives you

The question CAMEO could not pose, now answerable by named target rather than by
event code:

| day | events | target | operator | named group |
|---|---|---|---|---|
| 2019-09-14 | 22 | Abqaiq, Khurais, Shaybah, Ras Tanura, Safaniya | Saudi Aramco | Houthis |
| 2019-09-15 | 37 | Abqaiq, Khurais, Shaybah, Ras Tanura, Yanbu | Saudi Aramco | IRGC/Quds, **Houthis** |
| 2019-09-16 | 44 | Khurais, Abqaiq, Shaybah | Saudi Aramco | Houthis, IRGC/Quds |
| 2019-06-20 | 19 | Jazan | — | Houthis |
| 2018-04-28 | 18 | Jazan | — | Houthis |

**Contested attribution survives, and that is correct.** The Houthis claimed
Abqaiq; the US and Saudi Arabia named Iran. Both appear, with the share of
coverage behind each. A threshold that picked one winner would be asserting an
attribution the sources do not agree on.

## Two things to be careful about

**This is a retrieval facet, not identification.** It is a way to *find* events.
Coverage is written around the market reaction, so an article naming the operator
may do so partly *because* the price moved — selection on the outcome. The test
that would clear it was run and did not: the effect does not replicate on
coverage published before the market opened. Nothing here is offered as a causal claim.

**Coverage volume and attribution disagree.** Ranking a day's events by mentions
surfaces the wire stories, and those name the operator but not the perpetrator: on
2019-09-16 the six biggest events carry Abqaiq, Khurais and Saudi Aramco and *no
group at all*, while the day as a whole names both the Houthis and the IRGC. The
demo query (`EVENT_CONTEXT`) therefore reserves up to two slots for the
best-covered events that do name a group. Without that, the interface shows a
strike with no attacker.

## Exposed in the UI (2026-08-27)

Three places, all of them retrieval rather than claim:

- **Class mode** takes an optional *named group* and *target*, so the question
  becomes "the Houthis hit Saudi Aramco" rather than "Yemen attacks Saudi Arabia".
  Options are ranked by DISTINCT DAYS, not event count — the study measures days,
  so an entity spanning 3 days offers 3 observations however many events carry it.
  Narrowing this way puts a standing note under the verdict saying the class was
  selected on coverage.
- **Single-event mode** has a finder: pick a group and a target, get the matching
  days ranked by coverage, click one to measure it.
- **The graph** labels each analog with what the coverage named — `2019-09-15 · 38
  · Houthis → Abqaiq` instead of a bare date.

### The selection worry, tested

`scripts/entity_selection_test.py` matches each named-entity day against an
unnamed day of comparable coverage in the same class, stratified by year. Named
days are **flat at every horizon** (t = 0.8, −0.57, 0.03, −0.55 at 1/3/5/10 days).
Filtering on coverage-derived entities does not manufacture significance here.

The first run of that test made this project's own mistake and is worth keeping in
mind: matching on coverage volume alone drew 146 control days from 2020 against
100 named ones, and 2020 is the year WTI averaged $40 against 2018's $65. It
printed t = 3.16 at 10 days, which was the year mix.

### What it did expose

A narrow class exhausts the sweep. Houthis + Saudi Aramco yields 8 usable events,
so every rung of the 8/12/20/40/80 ladder returns the same 8, and the verdict read
the identical rows as "the same sector leads at every sample size" — reporting
**Real Estate, t = 3.75, detectable**, off one extra observation between the first
two rungs. Real estate does not respond to attacks on Saudi oil infrastructure.

`verdict()` now refuses a stalled ladder: fewer than three distinct sample sizes
returns **cannot be tested**, and the sweep marks repeated rungs "same events"
rather than showing four green confirmations of nothing. The bug predates the
entity work — any rare pairing reaches it — but the entity filters make narrow
classes easy to reach, which is how it surfaced.

## What this cannot do: sponsors

The extractor names the group in the coverage. It says nothing about who backs
that group, and neither does GDELT. Grouping a sponsor's proxies would turn the
narrowest classes from untestable into testable — Houthis + Saudi Aramco has 8
usable events, the Iran-aligned four against Saudi Arabia have 141 days — so the
temptation is real, and the obvious shortcut is wrong: co-mention with Iran ranks
AQAP, an Iranian adversary, above Hamas. Measured in
`scripts/alignment_comention_test.py`, reasoned through in
[alignment-data.md](alignment-data.md).

## Running it

```bash
python scripts/extract_entities.py --dry-run   # classify and report, write nothing
python scripts/extract_entities.py             # ...and enrich Event documents
```

It is a **separate step after `build_graph.py`**, and a rebuild that skips it
leaves the UI unable to name anything. `tests/test_graph.py` fails loudly in that
case rather than letting the demo quietly degrade.

The gazetteers at the top of the script are deliberately short. Every addition is
a judgement about who counts, and a long list invites tuning until it says what
you hoped.
