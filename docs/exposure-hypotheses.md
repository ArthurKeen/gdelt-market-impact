# Exposure hypotheses — stated before looking

**Written and committed 2026-11-17, before any of these tests was computed.** The
commit timestamp is the evidence. Results go in a separate section added afterwards;
nothing above that line changes once results exist. A test not listed here is
exploratory and is reported as such.

## Why stated in advance

The class sweep tested 405,987 questions and the correction for that search is
what killed every one of them. Testing four questions, chosen for a reason before
seeing the data, keeps the correction light enough that a real effect can show,
and makes a positive answer defensible: it cannot have been picked because it
looked good.

## The event set: Gulf shock days

Defined mechanically from the corpus, not chosen by hand:

1. **Region:** Persian Gulf and Gulf of Oman, as a box: latitude 22.0 to 30.5,
   longitude 47.5 to 60.0, inclusive. An event is in the region when its
   `locatedIn` Location's `lat`/`lng` fall inside the box. (This includes Abqaiq,
   Ras Tanura, Kuwait, Bahrain, Qatar, the UAE coast, Bandar Abbas and Fujairah,
   and excludes Riyadh. Inland southern Iran is included; the box is an
   approximation, stated here so it cannot be tuned later.)
2. **Events:** `quadClass == 4` (material conflict), located in the region, any
   actors already in the corpus, `dateAdded` from 2013-04-01 to 2026-10-31.
3. **Daily coverage:** for each `dateAdded` day, the sum of `numMentions` over
   those events.
4. **One day per week:** 7-day blocks, `FLOOR(DATE_DIFF("2018-01-01", day, "d") / 7)`,
   as in the class sweep. Keep the highest-coverage day in each block, so no two
   test days share a window.
5. **The set:** the 40 blocks whose kept day has the highest coverage.

## The measurement

Each series is measured from its own last close **strictly before** the day to its
next close (horizon 1 session), the anchor used everywhere else in this project.
Asian series are in local currency.

## The four hypotheses

All one-sided, exact Student-t, **Bonferroni α = 0.05 / 4 = 0.0125**.

| | Claim | Quantity per day | Pass if |
|---|---|---|---|
| **H1** | Japan is more exposed than the US | r(Nikkei 225) − r(S&P 500) | mean < 0, p < 0.0125 |
| **H2** | Positive control: these are supply shocks | r(Brent) − Brent's mean daily return 2013-04-01..2026-10-31 | mean > 0, p < 0.0125 |
| **H3** | Helium channel: TSMC is hit beyond Taiwan's market | r(TSMC 2330.TW) − r(Taiwan TAIEX) | mean < 0, p < 0.0125 |
| **H4** | Tankers gain when the Gulf is dangerous | r(Frontline) − Frontline's mean daily return 2013-04-01..2026-10-31 | mean > 0, p < 0.0125 |

**How to read them together.** H2 is the control. If Brent does not rise on these
days, the event set is not capturing supply shocks, and H1, H3 and H4 should be
read in that light rather than as findings about exposure.

**What would count as a finding.** A pass on H1 is the claim the exposure work
exists to test: that a Gulf shock reaches Tokyo harder than New York. A fail is
reported with the same prominence.

## Results

*(Added after the tests ran. Not edited above this line.)*

**Run 2026-11-17** (`scripts/exposure_hypotheses.py`; full output in
`docs/exposure-hypotheses.json`). All four fail, including the control.

| | Claim | n | mean | t | p (one-sided) | α = 0.0125 |
|---|---|---|---|---|---|---|
| H1 | Japan more exposed than the US | 37 | **+0.18%** (Japan did *better*) | +0.68 | 0.75 | fail |
| H2 | Control: Brent rises | 37 | +0.66% | +1.08 | 0.14 | fail |
| H3 | TSMC hit beyond Taiwan's market | 37 | +0.19% (TSMC did better) | +1.33 | 0.90 | fail |
| H4 | Tankers gain | 37 | −0.07% | −0.12 | 0.55 | fail |

**A flaw in the procedure, found after running and disclosed here.** The text
above says no two test days may share a window, and fixed 7-day blocks did not
deliver that: a Sunday and the following Monday fall in different blocks and
resolve to the same trading session. Three such sessions were counted twice in the
first run, for every series: 2016-01-03/04 (the Nimr execution and the
embassy storming), 2019-09-15/16 (Abqaiq) and 2026-02-28/03-02.
The table above measures each session once, as the stated intent requires and as
the class sweep already does. The first run, with the duplicates, also failed all
four: H1 +0.10% (p 0.66), H2 +1.15% (p 0.05), H3 +0.19% (p 0.92), H4 −0.07%
(p 0.55). Neither version passes anything.

**What it means.** H2 failing is the result to read first: on the Gulf's biggest
conflict-news days, oil itself does not reliably rise. The 40 days are mostly
political and military events that cut no supply — the Nimr execution and the
embassy storming (2016-01), the Qatar blockade (2017-06), Soleimani and Iran's
reply (2020-01), much of the 2026 war. So the failure of H1, H3 and H4 says the
*event set* does not isolate supply shocks; it does **not** show that Japan is no
more exposed than the US. That is the same lesson as the class sweep: news
volume is not a shock to physical supply.

**Exploratory, not a finding** (no correction, chosen after seeing the data). On
the 12 of the 40 days when Brent rose 2% or more, the Nikkei still did *better*
than the S&P on average, +0.68% (t +1.30, n 12). The one striking exception is
2026-02-28/03-02: Brent +7.26%, the Nikkei −1.39% against the S&P, and **KOSPI
−7.28%**.

**Proposed next test (H5), to be committed before it runs.** Define the event set
by physical supply rather than coverage: material-conflict events in the Gulf box
whose coverage names an oil facility or operator (`targetFacilities` /
`targetOperators` from `scripts/extract_entities.py`, e.g. Abqaiq, Ras Tanura,
Saudi Aramco, tankers). Then test H1 and the Korea version on that set.

---

*Publication note.* This repository was published without its working history. The
pre-registration above was committed in the original working repository as
`dd547ef` (2026-11-16 17:14 −07:00, which is 2026-11-17 00:14 UTC), before the script that runs it and before
the results, which followed in `2c3afc5` (17:26). That history is not part of this
public copy, so the order cannot be checked from here.
