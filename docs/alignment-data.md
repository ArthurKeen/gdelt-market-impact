# Sponsor / proxy alignment — a dependency we do not have

**Status: unmet, deliberately. Nothing in the repo asserts who sponsors whom.**

This records what an `alignedWith` relation would buy, why it cannot come from
GDELT, why the obvious shortcut is wrong, and what it would cost to do properly.
Same shape as [market-data.md](market-data.md): a hard external dependency, named
rather than faked.

## What it would buy

Since 2026-08-27 the UI can narrow a class by the group named in the coverage —
"the Houthis", which GDELT cannot express because it resolves them to `YEM`
([entity-extraction.md](entity-extraction.md)). The narrowing is sharp enough to
run out of evidence:

| class | usable events / days |
|---|---|
| Houthis + Saudi Aramco | **8 events — "cannot be tested"** |
| Iran-aligned four, vs Saudi Arabia | 141 days |
| Iran-aligned four, hitting a named energy target | 117 days |
| Iran-aligned four, vs Saudi or Israel | 530 days |

Grouping a sponsor's proxies is the difference between a class the sweep must
refuse and one it can actually judge. That is the whole argument for wanting it.

It would also traverse: `Iran → alignedWith → Houthis → (coverage) → Abqaiq` is a
citable path, which is the POC's stated stance — auditable, not a black box.

## Why not GDELT

GDELT codes *events between actors*, not *relations between actors*. There is no
sponsorship field, and no "Houthi" actor at all. The entity work already had to
reach into GKG's article text to recover the name; sponsorship is a further step
that no column carries.

## Why not derive it from co-mention — the shortcut, measured

The tempting move is to use what we already have: groups named alongside Iran in
the same article are presumably Iran's. `scripts/alignment_comention_test.py`
measures it across all 1,588,446 GKG articles.

| group | articles | also name Iran | lift vs base |
|---|---|---|---|
| Hezbollah | 2,016 | 94.1% | **2.38×** |
| Houthis | 8,960 | 59.9% | 1.52× |
| AQAP / al-Qaeda | 227 | 54.6% | 1.38× |
| Islamic State | 299 | 37.1% | 0.94× |
| Hamas | 21,325 | 24.0% | **0.61×** |

**It fails in both directions at once.** AQAP — an Iranian *adversary* — scores
1.38×, because it appears in Yemen coverage where everyone is named together.
Hamas scores **below the base rate** at 0.61×, despite Iranian funding being
uncontested except in degree. Encoding this would have asserted that AQAP is
Iran-aligned and Hamas is not.

Co-mention measures regional co-coverage. It is not a weak proxy for alignment;
it is a different quantity that happens to be correlated where the geography
overlaps. The test is committed so the result stays checkable and nobody tries it
again from first principles.

## Why not just write the edges by hand

Because sponsorship is a **contested geopolitical claim**, and this project has
been careful not to assert those. The Abqaiq attribution is kept deliberately
plural — the Houthis claimed it, the US and Saudi Arabia named Iran, and both
appear with their share of coverage — precisely because picking a winner would
assert what the sources dispute. Hard-coding a proxy list in a dropdown makes the
same assertion with less visibility.

If the edges are ever added they should carry, per edge: the **source**, the
**support type**, the **years it covers**, and a **contested** flag — so a reader
who disagrees about Hamas can see the basis and argue with it rather than
discovering it inside a label.

## Candidate sources

| source | what it codes | fit |
|---|---|---|
| **UCDP External Support** (Uppsala) | state support to non-state conflict actors: supporter, recipient, year, and type — funding, weapons, training, territory, troops | **best.** Academic, sourced, dated, free for research; joins on UCDP actor IDs |
| **Non-State Actor data** (Cunningham / Gleditsch / Salehyan) | external support for rebel groups, same actor keys | good corroboration; overlaps UCDP |
| **BAAD** (Big Allied and Dangerous) | organisation-level state sponsorship and inter-group alliances | useful for group-to-group ties UCDP does not cover |
| US State Dept designations | FTO listings, state sponsors of terrorism | **avoid as primary** — official, but a party to the dispute; encoding one government's position as ground truth is the same error in the other direction |
| Wikidata | `sponsor` / `allegiance` claims | machine-readable, crowd-sourced provenance; acceptable only as a cross-check |

## What it would actually cost

The download is not the work. Three things are:

1. **Name resolution.** UCDP actor names will not match the strings our extractor
   produces (`IRGC / Quds`, `AQAP / al-Qaeda`). This is an entity-resolution
   problem in its own right.
2. **Window overlap.** Our corpus is 2018-02-27..2022-03-31. Support coding is
   annual and conflict-scoped, so coverage has to be checked per group rather than
   assumed.
3. **Modelling the disagreement.** Sources differ on Hamas in particular. An edge
   set that hides that is worse than no edge set.

## Recommendation (2026-08-27)

**Do not add it now.** The gain is *testability*, not findings: it converts
"cannot be tested" into a runnable ladder, but the corpus-wide result stands —
405,987 tests, nothing survives correction for a search that wide
(`scripts/multiple_testing.py`). A proxy-union class faces the same correction and,
on current evidence, would not survive it. Taking on an external dataset, a
resolution problem and a contested-claim modelling problem, to enable one more
question that probably returns "not detectable", is a poor trade for now.

It is a good **next** step if the goal becomes for the graph to reason about actor
relationships rather than only actor events — which is where the sponsor edge
earns its place, independently of whether it moves a t-statistic.
