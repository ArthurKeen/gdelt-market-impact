# H5 — supply-shock days, stated before looking

**Written and committed 2026-11-17, before any of these tests was computed.** The
commit timestamp is the evidence. Results go in a separate section added
afterwards; nothing above that line changes once results exist.

## Why a fifth test

H1–H4 (docs/exposure-hypotheses.md) all failed, including the control: on the
Gulf's 40 biggest conflict-news days, Brent did not reliably rise. Those days were
mostly political and military events that cut no supply. So the failure said the
event set was wrong, not that Japan is unexposed. H5 asks the same question on
days defined by **physical supply**: conflict whose coverage names an oil facility
or an oil operator.

## What was looked at before writing this

Counts only, no prices and no returns: how many events carry each facility and
operator name, by year and by quad class; how many of them fall in the Gulf box
(few: GDELT often places them at a country centroid); and how many days and 7-day
blocks the rule below yields (739 days, 326 blocks). Separately, the author
already knows two outcomes that will almost certainly be in the set: Abqaiq
(2019-09-16, Brent +14.6%) and 2026-02-28/03-02 (KOSPI −7.28% against the S&P,
found in the H1–H4 exploratory note). H5.3 exists **because** of that second
observation. It is a test suggested by the data, and is labelled so.

## The event set

1. **Events:** `quadClass == 4` (material conflict), `dateAdded` from 2013-04-01 to
   2026-10-31, where `targetFacilities` contains at least one of
   *Abqaiq, Khurais, Ras Tanura, Shaybah, Safaniya, Khafji, Kharg Island, Haradh*,
   **or** `targetOperators` contains at least one of
   *Saudi Aramco, ADNOC, Kuwait Petroleum, SABIC, Petro Rabigh, NIOC*
   (the names `scripts/extract_entities.py` writes).
2. **Excluded from the facility list, and why:** *Jazan, Yanbu, Jubail, Qatif* are
   city or province names, so a match does not imply an energy target (Jazan alone
   is 8,178 events, mostly Houthi border shelling); *Strait of Hormuz* is a route,
   not a facility. This is decided here, not after seeing which choice helps.
3. **No lat/lng box.** The facility name is the geography. The box from H1–H4
   would drop most of these events because of where GDELT places them.
4. **Daily coverage, one day per week, top 40:** exactly as H1–H4 — sum of
   `numMentions` per `dateAdded` day; 7-day blocks
   `FLOOR(DATE_DIFF("2018-01-01", day, "d") / 7)`; keep each block's
   highest-coverage day; take the 40 blocks with the highest kept coverage.
5. **One session, one count.** A day is measured from its series' last close
   strictly before it. If two kept days resolve to the same session of the series
   being tested, only the higher-coverage one counts. (This is the flaw disclosed
   in H1–H4, now stated in advance.)

## The three hypotheses

All one-sided, exact Student-t, **Bonferroni α = 0.05 / 3 ≈ 0.0167**. Horizon one
session, local currency, as in H1–H4.

| | Claim | Quantity per day | Pass if |
|---|---|---|---|
| **H5.1** | Control: these are supply shocks | r(Brent) − Brent's mean daily return 2013-04-01..2026-10-31 | mean > 0, p < 0.0167 |
| **H5.2** | Japan is more exposed than the US | r(Nikkei 225) − r(S&P 500) | mean < 0, p < 0.0167 |
| **H5.3** | Korea is more exposed than the US *(suggested by the data)* | r(KOSPI) − r(S&P 500) | mean < 0, p < 0.0167 |

## Known biases, stated now

- **Selection on the outcome.** Coverage may name Aramco *because* the oil price
  moved; `extract_entities.py` records that this effect did not replicate on
  coverage published before the market opened. That biases H5.1 toward passing. It
  biases H5.2 and H5.3 much less: given that oil moved, the question is whether
  Tokyo and Seoul fell further than New York.
- **One day can carry a mean.** Abqaiq alone is a +14.6% Brent day. So every test
  is also reported with its single largest-contribution day removed. A pass that
  does not survive that is reported as *carried by one day*, not as a finding.
- **Overlap with H1–H4.** Some days will be in both sets. The count is reported.

## How to read them together

H5.1 first, as before. If Brent does not rise even on facility-named days, the
corpus cannot isolate supply shocks at this resolution, and H5.2 and H5.3 say
nothing about exposure. If H5.1 passes and H5.2 fails, that is the result worth
reporting: on days when oil supply really was hit, Tokyo did not fall harder than
New York.

## Results

*(Added after the tests ran. Not edited above this line.)*

**Run 2026-11-17** (`python scripts/exposure_hypotheses.py --h5`; full output in
`docs/exposure-hypotheses-h5.json`). **All three fail, including the control.**

| | Claim | n | mean | t | p (one-sided) | α = 0.0167 | without strongest day |
|---|---|---|---|---|---|---|---|
| H5.1 | Control: Brent rises | 38 | +1.24% | +1.77 | 0.042 | fail | +0.88%, p 0.081 (drops 2019-09-16, Abqaiq, +14.58%) |
| H5.2 | Japan more exposed than the US | 38 | −0.03% | −0.11 | 0.46 | fail | +0.13%, p 0.70 (drops 2026-03-07, −6.03%) |
| H5.3 | Korea more exposed than the US *(data-suggested)* | 38 | −0.26% | −0.60 | 0.28 | fail | −0.07%, p 0.43 (drops 2026-02-28, −7.28%) |

n is 38, not 40: two pairs of kept days resolved to the same session and were
counted once, as stated in advance (2019-09-15/16 and 2026-03-07/09). 8 of the
40 days are also in the H1–H4 set. 17 of the 40 are from the 2026 war.

**What it means, read as stated in advance.** H5.1 comes first, and it fails.
Brent rose on average on facility-named days, about twice as much as on the H1–H4
days (+1.24% against +0.66%), and p 0.042 would clear a single test at 0.05. It
does not clear the stated bar, and without Abqaiq it is +0.88% at p 0.08. By the
rule written before the run, the corpus cannot isolate supply shocks at this
resolution, so **H5.2 and H5.3 say nothing about exposure**, in either direction.
Naming a facility in the coverage is a better filter than raw volume; it is not
yet a supply-shock detector.

What the numbers do show, descriptively: the Japan and Korea gaps have means
close to zero, and the largest single days (2026-03-07 for Japan, 2026-02-28 for
Korea) are what make them negative at all. The KOSPI day that prompted H5.3 did
not generalise.

**Not tested, and not claimed.** Possible reasons Tokyo tracks New York on these
days include large strategic oil reserves in Japan and Korea, an oil shock being
priced as a global risk-off rather than a local one, and one-session horizons
being too short for a supply effect to reach equities. None of these was tested
here.

**What would be worth testing next.** The event set, not the markets, is the
binding problem twice over. A supply-shock set defined by the price itself (days
when Brent moved beyond its own volatility band, *then* ask which were Gulf
events) would make the control true by construction. That is a different
question: given an oil shock, is Asia hit harder? It would need its own
pre-registration.

---

*Publication note.* This repository was published from a single commit. The text
above "Results" was committed in the original working repository as `ee40d07`
(2026-11-17 08:40 −07:00), before its code existed; the run and these results
followed in `a7875c4` (08:44). That history is not part of this public copy, so
the order cannot be checked from here.
