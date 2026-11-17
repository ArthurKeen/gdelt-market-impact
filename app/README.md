# What-if UI

A demo instrument for the GDELT causal what-if graph. **Not a product** — read-only,
no auth, no persistence, no write paths.

```bash
python app/main.py                 # http://127.0.0.1:8000
python app/main.py --port 9000
```

## Why it exists

The stock ArangoDB Graph Visualizer gives no control over canvas layout, and this
demo's argument depends on it: the causal chain has to read as columns, and the
answer has to sit beside the evidence rather than in another tab. Two attempts at
scripting around that were enough.

## Driving it

**Use the preset buttons.** Live, the demo needs one combination out of 11 × 10 × 2
dropdown states, found while talking. The four presets set every control and fire:

| | |
|---|---|
| 1 · Tariffs on China | a class question — expect nothing |
| 2 · Houthi attacks on Saudi | the class Abqaiq belongs to — and it still says no |
| 3 · …and Abqaiq alone | the same attack as one day — +14.68% |
| 4 · Saudi-Russia price war | biggest move in the corpus, with a COVID caveat |

2 → 3 is the pair that carries the argument. Each preset prints a hint beside the
buttons so the caveat is on screen rather than in your head.

**Hover an event** for the CAMEO code in words, both actors, location, Goldstein,
tone and themes. **Click** to pin the full record on the right with a link to the
source article; click the background to clear.

Hover exists because a bare date is ambiguous and that ambiguity bit us: ask *Yemen
threatens Saudi Arabia* and 2019-09-16 comes back, ask *Yemen attacks Saudi Arabia*
and it comes back too — but the first is a Houthi warning (42 mentions) and the
second is the Abqaiq strike (940). Same day, different events, identical on screen
until the label carried the coverage.

## What it shows

Four regions, and the ordering is the argument:

1. **The evidence** — event → theme → sector as three dagre columns we control.
   A theme→sector link is drawn **only** where the estimate clears |t| ≥ 2. Since
   Theme→Sector is complete bipartite, drawing all of it would be 125 meaningless
   links; when nothing clears, the sector column sits visibly unconnected, which is
   the finding rather than a rendering bug.
2. **Is this answerable?** — the verdict. The panel the stock viewer cannot offer.
3. **Does it survive more evidence?** — the same question at growing sample sizes.
4. **The estimate**, and **directly-measured shocks** for contrast.

## The verdict rule

For a **class**, not "is the largest t big" but **does significance survive more
evidence**. A real effect sharpens as n grows because the standard error falls as
1/√n. Noise wanders: the leading sector changes and |t| decays. See `verdict()`.

For a **single event** there is no significance test to run, so `single_verdict()`
asks how far the move sits from that series' own normal range, and reports one of
three states:

| state | rule | example |
|---|---|---|
| `detectable` | any series past 3σ | price war, z = −8.5 |
| `below-bar` | ≥2 series past 2σ, none past 3σ | Section 301 — a defensive rotation |
| `not-detectable` | neither | 2019-05-10 tariff hike |

The middle state exists because calling a visible pattern "nothing" is the fragile
option: on 2018-03-22 Utilities, Real Estate and Consumer Staples all rose past 2σ
while the S&P fell 2.52%, and anyone reading the table can see it. Reported, not
claimed — and a bar that catches something and still declines to claim it is
visibly calibrated, which is the argument for trusting z = −8.5 elsewhere.

Every **class** question in this corpus returns *not detectable*, including
physical-attack classes. That is the finding, not a fault — so the verdict panel
cross-references the directly-measured shocks, or a panel that only ever says "no"
would read as broken.

## Screenshots

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --window-size=1680,1050 --hide-scrollbars \
  --virtual-time-budget=25000 --screenshot=shot.png http://127.0.0.1:8077/
```

`document.body.dataset.layout` carries the measured geometry (`cols`, `w`, `h`,
`ratio`), readable with `--dump-dom`. dagre degrades to a tall thin strip when
over-constrained and that failure is easy to miss in a visual review, so the
current parameters were tuned against that number rather than by eye — target
ratio ≈ 1.25 against a panel of roughly 1.4:1.

## Vendored

`static/vendor/` holds Cytoscape, dagre and cytoscape-dagre. Deliberately local
rather than CDN so the demo survives a room with no wifi.
