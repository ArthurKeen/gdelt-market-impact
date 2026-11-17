/* What-if UI.
 *
 * The layout is the reason this exists: the stock Graph Visualizer offers no
 * control over it, and this demo's argument depends on the causal chain reading
 * as columns. dagre with rankDir LR gives deterministic left-to-right ranks that
 * hold still between questions.
 */
'use strict';
cytoscape.use(cytoscapeDagre);

const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pct = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%');
const num = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2));

let cy = null;
let mode = 'class';

// Raw CAMEO codes in a customer-facing control are a needless puzzle — "SAU" is
// Saudi Arabia and nobody should have to ask.
// GDELT has no actor code for a non-state group: it resolves them to the country
// they operate from. The Houthis are YEM, Hezbollah is LBN, Hamas is PSE. Searching
// every field of a full GDELT day for "HOUTHI" finds it only in source URLs.
//
// That is why these six are here. Without YEM the Abqaiq strike is unreachable —
// the corpus held the diplomatic aftermath (Iran<->Saudi accusations) but not the
// attack, which GDELT codes YEM -> SAU, CAMEO 190.
const COUNTRY = { USA: 'the US', CHN: 'China', IRN: 'Iran', SAU: 'Saudi Arabia',
                  RUS: 'Russia', ISR: 'Israel',
                  YEM: 'Yemen (incl. Houthis)', LBN: 'Lebanon (incl. Hezbollah)',
                  PSE: 'Palestine (incl. Hamas)', SYR: 'Syria', IRQ: 'Iraq' };

// `fire` is false when the caller is about to set the fields and ask itself:
// asking here as well sent every preset and parsed question twice.
function setMode(next, fire = true) {
  mode = next;
  document.querySelectorAll('.mode').forEach((b) =>
    b.classList.toggle('on', b.dataset.mode === next));
  document.querySelectorAll('[data-for]').forEach((f) =>
    f.classList.toggle('hidden', f.dataset.for !== next));
  document.querySelectorAll('.mode-class').forEach((e) =>
    e.classList.toggle('hidden', next !== 'class'));
  document.querySelectorAll('.mode-event').forEach((e) =>
    e.classList.toggle('hidden', next !== 'event'));
  if (fire) (next === 'class' ? ask : askEvent)();
}

// The whole cytoscape stylesheet, rebuilt from the CSS custom properties each time
// it is asked for. It is a function rather than a literal because the day/night
// toggle changes what css() returns, and the canvas has to follow.
function cyStyle() {
  return [
    { selector: 'node', style: {
        label: 'data(label)', 'font-size': 12, 'font-family': 'ui-sans-serif, sans-serif',
        color: css('--ink'), 'text-valign': 'center', 'text-halign': 'right',
        'text-margin-x': 8, width: 16, height: 16,
        'background-color': 'data(colour)', 'border-width': 0 } },
    { selector: 'node[kind="sector"]', style: {
        shape: 'round-rectangle', width: 13, height: 13 } },
    { selector: 'node[weak]', style: {
        'background-opacity': .25, 'border-width': 1.5,
        'border-color': css('--unknown'), color: css('--faint') } },
    { selector: 'node[conflict]', style: {
        'border-width': 2, 'border-color': css('--neg') } },
    { selector: 'edge', style: {
        width: 'data(w)', 'line-color': 'data(colour)', 'curve-style': 'bezier',
        'target-arrow-shape': 'triangle', 'target-arrow-color': 'data(colour)',
        'arrow-scale': .7, opacity: 1 } },
    { selector: 'node.picked', style: {
        'border-width': 4, 'border-color': css('--book') } },
    { selector: '.muted', style: { opacity: .12 } },
    { selector: 'node.trace', style: {
        'border-width': 3, 'border-color': css('--book') } },
    { selector: 'edge.trace', style: { opacity: 1, width: 3 } },
    // Tested and did not clear the bar. Dashed and thin so it never reads as a
    // finding — it is the absence of one, drawn so the absence is visible.
    // Tested and did not clear the bar. The DASH carries that — fading the colour
    // as well put it under the 3:1 contrast floor and made the absence invisible,
    // which defeats the point of drawing it.
    { selector: 'edge.candidate', style: {
        'line-style': 'dashed', width: 1.4, 'target-arrow-shape': 'none' } },
  ];
}

function initCy() {
  cy = cytoscape({
    container: $('cy'),
    // Locked down: this is a presentation surface, and a graph that can be
    // dragged into a tangle mid-demo is worse than one that cannot move.
    boxSelectionEnabled: false,
    autounselectify: true,
    userZoomingEnabled: true,
    style: cyStyle(),
  });
}

// ---- day / night -----------------------------------------------------------
// Three states: 'light', 'dark', and absent = follow the operating system. The
// demo is given in rooms we do not choose — a bright conference room needs the
// light palette whatever the laptop is set to — so following the system alone
// is not enough.
//
// Element colours are baked into element data at draw time, so the canvas cannot
// inherit a CSS change the way the page does. Each element carries the palette
// ROLE it was painted from, and repainting re-reads that role. Cheaper and less
// disruptive than re-running the question: no database round trip, and the
// layout holds still.
function repaintGraph() {
  if (!cy) return;
  cy.batch(() => {
    cy.elements().forEach((el) => {
      const role = el.data('role');
      if (role) el.data('colour', css('--' + role));
    });
  });
  cy.style().fromJson(cyStyle()).update();
}

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  try { localStorage.setItem('gdelt-theme', mode); } catch (e) { /* private window */ }
  const dark = getComputedStyle(root).getPropertyValue('--paper').trim().toLowerCase()
    .startsWith('#1');
  const btn = $('themeToggle');
  if (btn) {
    btn.querySelector('.glyph').textContent = dark ? '☾' : '☀';
    btn.querySelector('.word').textContent = dark ? 'night' : 'day';
    btn.title = `Theme: ${mode}. Click for ${dark ? 'day' : 'night'}.`;
  }
  repaintGraph();
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('gdelt-theme'); } catch (e) { /* ignore */ }
  applyTheme(saved || 'system');
  // Following the system means following it while the page is OPEN too. Without
  // this the page keeps the palette it booted with, and the canvas — whose colours
  // are baked into element data — keeps the old one even after the CSS flips.
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const onSystemChange = () => {
    let cur = null;
    try { cur = localStorage.getItem('gdelt-theme'); } catch (e) { /* ignore */ }
    if (!cur || cur === 'system') applyTheme('system');
  };
  if (mq.addEventListener) mq.addEventListener('change', onSystemChange);
  else if (mq.addListener) mq.addListener(onSystemChange);
  $('themeToggle').addEventListener('click', () => {
    const dark = getComputedStyle(document.documentElement)
      .getPropertyValue('--paper').trim().toLowerCase().startsWith('#1');
    applyTheme(dark ? 'light' : 'dark');
  });
}

function layout() {
  cy.resize();          // fit() measures the viewport; do it after the panel has one
  cy.layout({
    // Tuned against measured geometry, not eyeballed: the panel is roughly 1.4:1, and
    // these values put the graph at ~1.3:1 so it fills the width instead of
    // degenerating into the tall thin strip dagre produces when it is over-constrained.
    name: 'dagre', rankDir: 'LR', nodeSep: 10, rankSep: 330, align: 'UL',
    fit: true, padding: 24, animate: false,
  }).run();
}

// Themes per event, rebuilt per question — the graph payload is one row per
// (event, theme) pair, so the detail card has to reassemble them.
let EVENT_THEMES = {};


// ---- CAMEO codes, in words -------------------------------------------------
// The tooltip has to say what an event WAS. "190" is not an answer to that, and
// the analog set is not always a single code once you follow a node around.
const CAMEO_NAME = {
  '010': 'makes a public statement', '012': 'makes a pessimistic comment',
  '016': 'complains officially', '020': 'appeals', '036': 'expresses intent to meet',
  '040': 'consults', '042': 'visits', '043': 'hosts a visit', '046': 'meets at a third location',
  '050': 'engages in diplomatic cooperation', '051': 'praises or endorses',
  '060': 'engages in material cooperation', '061': 'cooperates economically',
  '071': 'provides economic aid', '090': 'investigates',
  '100': 'demands', '111': 'criticises or denounces', '112': 'accuses',
  '1124': 'accuses of human-rights abuses', '122': 'rejects',
  '130': 'threatens', '131': 'threatens non-force', '134': 'threatens to halt talks',
  '138': 'threatens', '1312': 'threatens an embargo', '1383': 'threatens an attack',
  '152': 'demonstrates military power', '153': 'mobilises armed forces',
  '160': 'reduces relations', '163': 'imposes sanctions / embargo',
  '172': 'blockades / restricts movement', '173': 'arrests or detains',
  '174': 'expels', '180': 'attacks unconventionally', '181': 'abducts',
  '186': 'attempts assassination', '190': 'attacks with military force',
  '192': 'occupies territory', '193': 'fights with small arms',
  '194': 'fights with artillery or tanks', '195': 'attacks from the air',
};
const cameoName = (c) => CAMEO_NAME[c] || (c ? `CAMEO ${c}` : 'unclassified');

// ---- hover tooltip ---------------------------------------------------------
function hideTip() { const t = $('tip'); if (t) t.classList.add('hidden'); }

function showTip(node, ev) {
  const m = node.data('meta');
  const t = $('tip');
  if (!m || !t) return;
  const who = `${COUNTRY[m.actorFrom] || m.actorFrom || '?'} → ${COUNTRY[m.actorTo] || m.actorTo || '?'}`;
  const themes = [...(EVENT_THEMES[m.event] || [])].map((x) => x.replace(/_/g, ' '));
  t.innerHTML = `<b>${m.date} — ${cameoName(m.cameo)}</b>`
    + `<span class="dim">${who}${m.geo ? ' · ' + m.geo : ''}</span><br>`
    + `${Number(m.mentions || 0).toLocaleString()} mentions`
    + (m.goldstein != null ? ` · Goldstein ${m.goldstein}` : '')
    + (m.tone != null ? ` · tone ${Number(m.tone).toFixed(1)}` : '')
    + (themes.length ? `<br><span class="dim">${themes.slice(0, 4).join(', ')}</span>` : '')
    + `<br><span class="dim">click to pin the detail on the right</span>`;
  t.classList.remove('hidden');
  const host = $('cy').getBoundingClientRect();
  const x = (ev?.renderedPosition?.x ?? 0), y = (ev?.renderedPosition?.y ?? 0);
  // Flip before the tooltip runs off the panel rather than after.
  t.style.left = `${Math.min(x + 14, host.width - t.offsetWidth - 8)}px`;
  t.style.top = `${Math.max(6, y - 8)}px`;
}

// ---- clicked-event detail --------------------------------------------------
function clearDetail() {
  const d = $('evDetail');
  if (d) d.classList.add('hidden');
  if (typeof cy !== 'undefined' && cy) cy.nodes().removeClass('picked');
}

function showDetail(node) {
  const m = node.data('meta');
  if (!m) return;
  cy.nodes().removeClass('picked');
  node.addClass('picked');
  const themes = [...(EVENT_THEMES[m.event] || [])].map((x) => x.replace(/_/g, ' '));
  $('evdTitle').textContent = `${COUNTRY[m.actorFrom] || m.actorFrom} ${cameoName(m.cameo)} `
    + `${COUNTRY[m.actorTo] || m.actorTo}`;
  $('evDetailSub').textContent = m.date;
  const rows = [
    ['coverage', `${Number(m.mentions || 0).toLocaleString()} mentions`],
    ['CAMEO', `${m.cameo} — ${cameoName(m.cameo)}`],
    ['Goldstein', m.goldstein != null ? `${m.goldstein}  (−10 hostile … +10 cooperative)` : '—'],
    ['avg tone', m.tone != null ? Number(m.tone).toFixed(2) : '—'],
    ['located', m.geo || m.geoCountry || '—'],
    ['GDELT id', m.event],
  ];
  // Recovered from what the coverage named, not from CAMEO. Only shown when
  // present: most events have none, and an empty row implies a failed lookup
  // rather than an event nobody described in those terms.
  // Where the shares are known, show them. Abqaiq is the case that matters: the
  // Houthis claimed it and the US and Saudi Arabia named Iran, and both are in
  // the coverage. Collapsing that to one winner would assert an attribution the
  // sources do not agree on.
  const det = m.entityDetail || {};
  const withShares = (list, fallback) => {
    if (!list || !list.length) return fallback && fallback.length ? fallback.join(', ') : null;
    return list.map((x) => x.pct != null && list.length > 1
      ? `${x.name} <span class="dim">${Math.round(x.pct * 100)}%</span>`
      : x.name).join(', ');
  };
  const named = [
    ['named group', withShares(det.perpetrators, m.perpetrators)],
    ['named operator', withShares(det.operators, m.operators)],
    ['named target', withShares(det.facilities, m.facilities)],
  ].filter(([, v]) => v);
  for (const [k, v] of named) rows.push([k, v]);
  if (named.length && m.coverage) {
    rows.push(['from coverage', `${m.coverage} article${m.coverage > 1 ? 's' : ''}`]);
  }
  $('evdKV').innerHTML = rows
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  // These are news URLs from 2018-2022 and they rot: a sample of 40 high-coverage
  // events resolved 18 times — 45%. Naming the publisher says where the link goes
  // before it is clicked, and the archive link gives the other half somewhere to
  // land. The Wayback redirect finds the nearest snapshot itself, and says so when
  // there is none, which is a better failure than a publisher's 404.
  const link = $('evdLink');
  const arch = $('evdArchive');
  const rot = $('evdRot');
  if (m.url) {
    let host = '';
    try { host = new URL(m.url).hostname.replace(/^www\./, ''); } catch (e) { host = 'source'; }
    link.href = m.url;
    link.textContent = host;
    link.style.display = '';
    arch.href = 'https://web.archive.org/web/'
              + (m.date || '').replace(/-/g, '') + '/' + m.url;
    arch.style.display = '';
    rot.textContent = 'about half of these 2018-2022 links have expired';
  } else {
    link.style.display = 'none';
    arch.style.display = 'none';
    rot.textContent = '';
  }
  $('evdThemes').textContent = themes.length
    ? `reported under: ${themes.join(', ')}` : '';
  $('evDetail').classList.remove('hidden');
}


// ---- sector row -> its paths in the graph ----------------------------------
// The table and the graph were two panels showing the same answer with no visible
// join. Clicking a sector walks backwards from it — sector, the themes that reach
// it, the events under those themes — and mutes everything else.
//
// Clicking a sector NOTHING reaches is the more useful case, and it is deliberately
// not treated as an error: the row highlights, the graph goes quiet, and the note
// says so. That is the project's finding made clickable.
let CORPUS_WINDOW = 'delivered';
let WINDOW_LIST = [];

// The date picker follows the window: its bounds used to be fixed in the HTML at
// the delivered span, so no later day could be typed in at all.
function selectWindow(id) {
  CORPUS_WINDOW = id;
  const sel = $('windowSel');
  if (sel) sel.value = id;
  const w = WINDOW_LIST.find((x) => x.id === id);
  if (w) { $('eventDate').min = w.from; $('eventDate').max = w.to; }
}
let tracedSector = null;
let LAST_OUTCOME = [];       // the rows behind the estimate table, for the trace

function clearTrace() {
  tracedSector = null;
  if (cy) {
    cy.remove('edge.candidate');     // the dashed links exist only while tracing
    cy.elements().removeClass('trace muted');
  }
  document.querySelectorAll('#outcome tr.on').forEach((r) => r.classList.remove('on'));
  const n = $('graphNote');
  if (n && n.dataset.base) n.textContent = n.dataset.base;
}

function traceSector(name) {
  if (!cy) return;
  if (tracedSector === name) { clearTrace(); return; }   // second click clears
  clearTrace();
  tracedSector = name;

  document.querySelectorAll('#outcome tr').forEach((r) =>
    r.classList.toggle('on', r.dataset.sector === name));

  const sector = cy.getElementById('s:' + name);
  if (!sector || sector.empty()) return;

  // Theme->Sector is complete bipartite in the graph: every theme was measured
  // against every sector. drawGraph only DRAWS the links that cleared the bar,
  // because 125 of them is a hairball. So a sector nothing reached has no drawn
  // path, and muting alone would just grey the screen and say nothing.
  //
  // Draw the rest on demand, dashed: those are the links that were tested and did
  // not clear. Now the click answers "why not?" instead of only "nothing".
  const themes = cy.nodes('[kind="theme"]');
  const already = new Set(sector.incomers('edge').map((e) => e.source().id()));
  const pending = themes.filter((t) => !already.has(t.id()));
  if (pending.length) {
    cy.add(pending.map((t) => ({
      data: { id: `cand:${t.id()}->${name}`, source: t.id(), target: sector.id(),
              colour: css('--link'), w: 1.4, role: 'link' },
      classes: 'candidate',
    })));
  }

  // Walk upstream from the sector — now guaranteed to reach the themes, and
  // through them the events that produced them.
  let front = sector, lit = sector;
  for (let hop = 0; hop < 4; hop++) {
    const inc = front.incomers();
    if (inc.empty()) break;
    lit = lit.union(inc);
    front = inc.nodes();
  }
  cy.elements().difference(lit).addClass('muted');
  lit.addClass('trace');

  const note = $('graphNote');
  if (note) {
    if (!note.dataset.base) note.dataset.base = note.textContent;
    const row = LAST_OUTCOME.find((r) => r.sector === name) || {};
    const nEvents = lit.nodes('[meta]').length;
    const nThemes = lit.nodes('[kind="theme"]').length;
    const cleared = already.size;
    const t = row.tStat == null ? '—' : (row.tStat > 0 ? '+' : '') + row.tStat.toFixed(2);
    note.textContent = cleared
      ? `${name}: ${nEvents} event${nEvents === 1 ? '' : 's'} through ${nThemes} theme`
        + `${nThemes === 1 ? '' : 's'}, t = ${t} — solid links cleared the bar`
      : `${name}: ${nThemes} theme${nThemes === 1 ? '' : 's'} measured against it from `
        + `${nEvents} event${nEvents === 1 ? '' : 's'}, t = ${t}. None cleared |t| ≥ 2 — `
        + `the dashed links are what was tested and failed`;
  }
}

function drawGraph(pairs, outcome) {
  const els = [];
  const seen = new Set();
  EVENT_THEMES = {};
  hideTip(); clearDetail();
  const add = (id, label, kind, colour, extra = {}) => {
    if (seen.has(id)) return;
    seen.add(id);
    els.push({ data: { id, label, kind, colour, ...extra } });
  };

  for (const p of pairs) {
    const ev = 'e:' + p.event;
    // Label the event with its coverage, not just its date. Two classes can both
    // return 2019-09-16 for Yemen->Saudi: the strike on Abqaiq (940 mentions) and
    // a Houthi warning of further attacks two days later (42 mentions). Shown as
    // bare dates those are indistinguishable, and the obvious question in a demo
    // — "isn't that the same event?" — has no answer on screen. The mention count
    // separates a headline shock from a wire brief at a glance.
    // A bare date does not say what the event was. Where the coverage named a
    // group or a target, put it on the node — that is the whole point of having
    // extracted them.
    const who = (p.perpetrators || [])[0];
    const what = (p.facilities || [])[0] || (p.operators || [])[0];
    const tag = [who, what].filter(Boolean).join(' \u2192 ');
    add(ev, `${p.date}  ·  ${Number(p.mentions || 0).toLocaleString()}`
            + (tag ? `  ·  ${tag}` : ''),
        'event', css('--news'),
        { ...(p.quad >= 3 ? { conflict: 1 } : {}), meta: p, role: 'news' });
    // Themes arrive one row per (event, theme); collect them for the detail card.
    (EVENT_THEMES[p.event] ||= new Set()).add(p.theme);
    add('t:' + p.theme, p.theme.replace(/_/g, ' ').slice(0, 26), 'theme', css('--theme'),
        { role: 'theme' });
    els.push({ data: { id: `${ev}->${p.theme}`, source: ev, target: 't:' + p.theme,
                       colour: css('--link'), w: 1.2, role: 'link' } });
  }

  // Third column. Theme->Sector is complete bipartite — every theme reaches every
  // sector — so drawing all of it is 125 meaningless links that also force dagre
  // into a tall thin strip. Draw a sector link ONLY where the estimate clears the
  // bar. When nothing does, the sector column sits visibly unconnected, which is
  // precisely the finding: the evidence does not reach the market layer.
  let connected = 0;
  for (const row of outcome) {
    const sid = 's:' + row.sector;
    const strong = row.tStat != null && Math.abs(row.tStat) >= 2;
    add(sid, row.sector, 'sector', strong ? css('--market') : css('--unknown'),
        strong ? { role: 'market' } : { weak: 1, role: 'unknown' });
    if (!strong) continue;
    connected++;
    const colour = row.movePct >= 0 ? css('--pos') : css('--neg');
    const role = row.movePct >= 0 ? 'pos' : 'neg';
    for (const t of [...seen].filter((x) => x.startsWith('t:'))) {
      els.push({ data: { id: `${t}->${sid}`, source: t, target: sid, colour, w: 2.4, role } });
    }
  }
  // Keep the base note beside the element: a trace overwrites this text, and
  // clearTrace() has to restore *this* question's note, not the previous one's.
  const baseNote = connected
    ? `${connected} sector${connected > 1 ? 's' : ''} reached by a defensible link`
    : 'no sector is reached by a defensible link — the estimate stops at the themes';
  $('graphNote').textContent = baseNote;
  $('graphNote').dataset.base = baseNote;

  clearTrace();
  cy.elements().remove();
  cy.add(els);
  layout();

  // Report the layout's own geometry. Columns are what this view exists to give —
  // dagre silently degrades to a tall thin strip when the graph is too dense, and
  // that failure is invisible in a screenshot review. Counting distinct x-ranks
  // makes it checkable, and tells the reader what they are looking at.
  const xs = cy.nodes().map((n) => Math.round(n.position('x') / 40));
  const cols = new Set(xs).size;
  const bb = cy.elements().boundingBox();
  const ratio = bb.h > 0 ? (bb.w / bb.h) : 0;
  $('graphSub').textContent =
    `${seen.size} nodes · ${els.length - seen.size} links · ${cols} columns`;
  document.body.dataset.layout =
    `cols=${cols} w=${Math.round(bb.w)} h=${Math.round(bb.h)} ratio=${ratio.toFixed(2)}`;
}

const CALL_LABEL = { 'detectable': 'detectable',
                     'below-bar': 'below our bar',
                     'not-detectable': 'not detectable',
                     'too-few': 'cannot be tested' };

function renderVerdict(v) {
  $('call').textContent = CALL_LABEL[v.call] || 'not detectable';
  $('call').className = 'call ' + v.call;
  $('headline').textContent = v.headline;
  $('detail').textContent = v.detail;
  // Every CLASS question in this corpus comes back not-detectable, which is the
  // finding rather than a fault — but a verdict panel that only ever says "no"
  // reads as broken. Point at what does resolve, so the contrast is on screen
  // instead of only in the narration.
  //
  // The pointer names Abqaiq rather than the larger Saudi-Russia price war on
  // purpose. The price war is the bigger number (WTI -24.59%) but the S&P fell
  // 7.60% the same day, three days into the COVID drawdown, so it invites the
  // objection that a pandemic did it. Abqaiq moved crude 14.68% on a day the S&P
  // moved -0.31%. Cite the clean one; keep the big one for magnitude.
  $('crossref').textContent = (mode === 'class' && v.call !== 'detectable')
    ? 'Classes of event do not resolve here; single large shocks do. Abqaiq removed '
      + '5% of world oil supply and moved crude 14.68% in one session, on a day the '
      + 'S&P moved -0.31% — see the directly-measured events below.'
    : '';
}

function renderSweep(rows) {
  // A rung that repeats the previous sample size added no evidence — the class
  // ran out. Left unmarked it reads as four independent confirmations, which is
  // the opposite of what it is, and the green flags shout louder than the
  // headline. Say "same events" and drop the flag.
  let prev = null;
  $('sweep').innerHTML = rows.map((r) => {
    const stalled = prev !== null && r.n === prev;
    prev = r.n;
    return `
    <tr class="${stalled || !r.looksSignificant ? 'faded' : ''}">
      <td class="num">${r.n}</td>
      <td>${r.sector}</td>
      <td class="num ${stalled ? 'dim' : (r.movePct >= 0 ? 'pos' : 'neg')}">${pct(r.movePct)}</td>
      <td class="num">${num(r.tStat)}</td>
      <td class="${stalled ? 'dim' : 'flag ' + (r.looksSignificant ? 'yes' : 'no')}">${
        stalled ? 'same events' : (r.looksSignificant ? 'yes' : 'no')}</td>
    </tr>`;
  }).join('');
}

function renderOutcome(rows) {
  LAST_OUTCOME = rows;
  $('outcomeSub').textContent = `${rows[0] ? rows[0].n : 0} observations`;
  $('outcome').innerHTML = rows.map((r) => {
    const strong = r.tStat != null && Math.abs(r.tStat) >= 2;
    // The spread is usually the more honest number: a +0.89% mean over observations
    // running -3.36% to +2.75% is not a forecast, and showing only the mean hides that.
    const range = (r.lowPct == null) ? '—'
      : `${pct(r.lowPct)} … ${pct(r.highPct)}`;
    return `<tr class="pick ${strong ? '' : 'faded'}" data-sector="${r.sector}">
      <td>${r.sector}</td>
      <td class="num ${r.movePct >= 0 ? 'pos' : 'neg'}">${pct(r.movePct)}</td>
      <td class="num dim">${range}</td>
      <td class="num">${num(r.tStat)}</td>
      <td class="num">${r.n}</td></tr>`;
  }).join('');
}

function renderOil(data) {
  $('oil').innerHTML = data.rows.map((r) => {
    const big = Math.abs(r.moves[0]) >= 10;
    // An event near the end of the corpus has no 10-day close yet; pad rather
    // than let the row come up a column short.
    const cells = data.horizons.map((_, k) => (r.moves[k] == null
      ? '<td class="num dim">—</td>'
      : `<td class="num ${r.moves[k] >= 0 ? 'pos' : 'neg'}">${pct(r.moves[k])}</td>`));
    return `<tr>
      <td${big ? ' style="font-weight:650"' : ''}>${r.event}</td>
      ${cells.join('')}
    </tr>`;
  }).join('');
}

const MACRO_NAMES = { UST10Y: '10-year Treasury yield', UST2Y: '2-year Treasury yield',
  Breakeven10Y: '10-year breakeven inflation', 'USD broad': 'US dollar (broad)' };

function renderMacro(rows) {
  const panel = $('macroPanel');
  if (!rows || !rows.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  $('macroSub').textContent = 'FRED daily';
  $('macroRows').innerHTML = rows.map((r) => {
    const big = r.z != null && Math.abs(r.z) >= 3;
    const mv = (r.move > 0 ? '+' : '') + (r.unit === 'bp' ? r.move.toFixed(0) + 'bp'
                                                          : r.move.toFixed(2) + '%');
    return `<tr class="${big ? '' : 'faded'}">
      <td${big ? ' style="font-weight:650"' : ''}>${MACRO_NAMES[r.name] || r.name}</td>
      <td class="num ${r.move >= 0 ? 'pos' : 'neg'}">${mv}</td>
      <td class="num">${r.z == null ? '—' : Math.abs(r.z).toFixed(1) + '×'}</td></tr>`;
  }).join('');
}

function renderExposure(rows, eventRows) {
  const panel = $('expoPanel');
  if (!rows || !rows.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const spx = (eventRows || []).find((r) => r.target === '_GSPC');
  $('expoSub').textContent = spx ? `S&P ${pct(spx.movePct)} the same day` : '';
  $('expoRows').innerHTML = rows.map((r) => {
    const big = r.z != null && Math.abs(r.z) >= 3;
    return `<tr class="${big ? '' : 'faded'}">
      <td${big ? ' style="font-weight:650"' : ''}>${r.name}</td>
      <td class="dim">${r.group || ''}</td>
      <td class="num ${r.move >= 0 ? 'pos' : 'neg'}">${pct(r.move)}</td>
      <td class="num">${r.z == null ? '—' : Math.abs(r.z).toFixed(1) + '×'}</td></tr>`;
  }).join('');
}

// Supply paths. Escaped: figures and names come from the database, and a source
// title is free text.
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const share = (x) => (x.share != null ? `${Math.round(x.share * 100)}%`
  : (x.lo != null ? `${Math.round(x.lo * 100)}–${Math.round(x.hi * 100)}%` : ''));
const cite = (x) => (x.sourceUrl
  ? `<span class="src">[<a href="${esc(x.sourceUrl)}" target="_blank" rel="noopener">${esc((x.source || 'source').split(/[,:]/)[0])}</a>]</span>` : '');
const ROLE = { consumes: 'uses it', sells: 'sells it', competes: 'competes with Gulf supply — gains when it is cut',
               transports: 'carries it', transits: 'routes through here' };

function renderPaths(paths) {
  const panel = $('pathPanel');
  if (!paths || !paths.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  $('pathSub').textContent = paths.map((p) => `${p.chokepoint}: ${p.events} events`).join(' · ');
  $('pathBody').innerHTML = paths.map((p) => {
    const goods = p.goods.map((g) => {
        const lines = [
          ...g.producers.map((x) => `<li>from ${esc(x.country)}${share(x) ? ` (${share(x)} ${esc(x.measure)})` : ''}${cite(x)}</li>`),
          ...g.importers.map((x) => `<li>${esc(x.country)} → ${esc(x.markets.join(', ') || '—')}${share(x) ? `: ${share(x)} ${esc(x.measure)}` : `: ${esc(x.measure)}`}${cite(x)}</li>`),
          ...g.companies.map((x) => {
            const cust = x.customers.length
              ? ` → supplies ${x.customers.map((c) => `<span class="${c.held ? 'held' : ''}">${esc(c.name)}${c.held ? ' (in your portfolio)' : ''}</span>`).join(', ')}` : '';
            return `<li>${esc(x.name)} ${esc(ROLE[x.role] || x.role)}${cust}</li>`;
          }),
        ];
        return `<h3>${esc(g.good)} <span class="dim">${share(g) ? `— ${share(g)} ${esc(g.measure)}` : esc(g.measure ? '— ' + g.measure : '')}</span>${cite(g)}</h3>${lines.length ? `<ul>${lines.join('')}</ul>` : ''}`;
      }).join('');
    const transit = p.transits.length
      ? `<ul>${p.transits.map((x) => `<li>${esc(x.name)} ${esc(ROLE[x.role] || x.role)}</li>`).join('')}</ul>` : '';
    return `<h3>${esc(p.chokepoint)}</h3>${goods}${transit}`;
  }).join('');
}

// The portfolio panel. One day is a measurement; past analogs are a range, and a
// range over a class the verdict calls noise is what ordinary stretches look like,
// not a forecast — so the caveat is tied to the verdict, not left to the reader.
function renderPortfolio(p, call) {
  const panel = $('pfPanel');
  if (!p || (p.rows == null && !p.n)) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const vs = (a, b) => (b == null ? '' : ` against the S&amp;P's <strong>${pct(b)}</strong>`);
  if (p.rows) {                                   // one day
    $('pfSub').textContent = `${p.name} · ${p.horizon}-day`;
    $('pfSum').innerHTML = p.totalPct == null
      ? 'No prices for these holdings on that day.'
      : `Moved <strong class="${p.totalPct >= 0 ? 'pos' : 'neg'}">${pct(p.totalPct)}</strong>`
        + `${vs(p.totalPct, p.benchmarkPct)}.`;
    $('pfHead').innerHTML = '<tr><th>holding</th><th class="num">weight</th>'
      + '<th class="num">move</th><th class="num">contribution</th></tr>';
    $('pfRows').innerHTML = p.rows.map((r) => `<tr>
      <td>${r.ticker} <span class="tag">${r.kind}</span> <span class="dim">${r.sector || ''}</span></td>
      <td class="num">${Math.round(r.weight * 100)}%</td>
      <td class="num ${r.movePct >= 0 ? 'pos' : 'neg'}">${r.movePct == null ? '—' : pct(r.movePct)}</td>
      <td class="num ${r.contributionPct >= 0 ? 'pos' : 'neg'}">${r.contributionPct == null ? '—' : pct(r.contributionPct)}</td>
      </tr>`).join('');
    $('pfFoot').className = 'foot';         // a class question may have left 'warn'
    $('pfFoot').textContent = 'Measured from the last close before the day, the same '
      + 'anchor as every other figure here. A measurement, not an estimate.';
    return;
  }
  $('pfSub').textContent = `${p.name} · ${p.horizon}-day · ${p.n} past analogs`;
  $('pfSum').innerHTML = `After the ${p.n} past events like this one, the portfolio's `
    + `${p.horizon}-day move ran from <strong>${pct(p.worstPct)}</strong> to `
    + `<strong>${pct(p.bestPct)}</strong>, averaging <strong>${pct(p.meanPct)}</strong>`
    + (p.meanExcessPct == null ? '.' : ` — ${pct(p.meanExcessPct)} against the S&amp;P.`);
  $('pfHead').innerHTML = '<tr><th>analog day</th><th class="num">portfolio</th>'
    + '<th class="num">S&amp;P</th></tr>';
  $('pfRows').innerHTML = p.analogs.map((r) => `<tr class="${call === 'detectable' ? '' : 'faded'}">
    <td>${r.date}</td>
    <td class="num ${r.portfolioPct >= 0 ? 'pos' : 'neg'}">${pct(r.portfolioPct)}</td>
    <td class="num">${r.benchmarkPct == null ? '—' : pct(r.benchmarkPct)}</td></tr>`).join('');
  const foot = $('pfFoot');
  foot.className = call === 'detectable' ? 'foot' : 'foot warn';
  foot.textContent = call === 'detectable'
    ? 'The verdict says this class moves markets detectably, so the average is worth reading.'
    : 'The verdict above says this class does not move markets detectably. Read the '
      + 'range as what an ordinary stretch looks like for this portfolio — not as a forecast.';
}

const BASELINE_WORDS = {
  global: 'the whole window — one sigma for every day in it',
  trailing: 'the 60 trading days before each event — adapts to any regime, named '
          + 'or not, and uses only what was known at the time',
  regime: 'the detected volatility regime containing the day — sharper across a '
        + 'boundary than a trailing window, but it can absorb an event large '
        + 'enough to have defined the regime',
};

function renderEventRows(rows, baseline) {
  $('evSub').textContent = `${rows.length} series`;
  const foot = $('baselineFoot');
  if (foot) {
    foot.innerHTML = `<strong>vs normal day</strong> is measured against `
      + `${BASELINE_WORDS[baseline] || BASELINE_WORDS.global}. The three columns to `
      + `its right are the same move against all three yardsticks — a result that `
      + `only survives one of them is telling you about the yardstick.`;
  }
  const cell = (v, on) => v == null
    ? '<td class="num dim">—</td>'
    : `<td class="num dim${on ? ' pick-on' : ''}">${Math.abs(v).toFixed(1)}×</td>`;
  $('evRows').innerHTML = rows.slice(0, 12).map((r) => {
    const used = r.zUsed == null ? r.z : r.zUsed;
    const big = Math.abs(used) >= 3;
    const shown = r.marketAdjusted ? r.adjustedPct : r.movePct;
    return `<tr class="${big ? '' : 'faded'}">
      <td${big ? ' style="font-weight:650"' : ''}>${r.label || r.target.replace(/_/g, ' ')}
        ${r.marketAdjusted ? '<span class="tag">net of market</span>' : ''}
        ${r.zFellBack ? '<span class="tag">no conditional baseline</span>' : ''}</td>
      <td class="num ${shown >= 0 ? 'pos' : 'neg'}">${pct(shown)}</td>
      <td class="num">${Math.abs(used).toFixed(1)}×</td>
      ${cell(r.z, baseline === 'global')}
      ${cell(r.zTrailing, baseline === 'trailing')}
      ${cell(r.zRegime, baseline === 'regime')}</tr>`;
  }).join('');
}

// Event mode has no analog chain — the graph shows what was in the news that day,
// which is the honest visual for "this specific thing happened".
function drawEventGraph(pairs) {
  const els = [];
  const seen = new Set();
  EVENT_THEMES = {};
  hideTip(); clearDetail();
  const add = (id, label, kind, colour, extra = {}) => {
    if (seen.has(id)) return;
    seen.add(id);
    els.push({ data: { id, label, kind, colour, ...extra } });
  };
  for (const p of pairs) {
    const ev = 'e:' + p.event;
    add(ev, `${Number(p.mentions || 0).toLocaleString()} mentions`, 'event', css('--news'),
        { ...(p.quad >= 3 ? { conflict: 1 } : {}), meta: p, role: 'news' });
    (EVENT_THEMES[p.event] ||= new Set()).add(p.theme);
    add('t:' + p.theme, p.theme.replace(/_/g, ' ').slice(0, 26), 'theme', css('--theme'),
        { role: 'theme' });
    els.push({ data: { id: `${ev}->${p.theme}`, source: ev, target: 't:' + p.theme,
                       colour: css('--link'), w: 1.2, role: 'link' } });
  }
  cy.elements().remove();
  cy.add(els);
  layout();
  $('graphSub').textContent = `${seen.size} nodes · ${els.length - seen.size} links`;
  $('graphNote').textContent = pairs.length
    ? 'What was being reported that day. No analog retrieval — this is one event.'
    : 'No GDELT events in the corpus for that date.';
}


// ---- entities read out of the coverage --------------------------------------
// GDELT's actor codes cannot name a non-state group — it resolves the Houthis to
// YEM — so these come from what the articles say. They narrow WHICH events are
// measured; they do not make the measurement causal. See docs/entity-extraction.md.
let ENTITY_VOCAB = { groups: [], targets: [] };

function fillEntitySelect(id, rows, blank) {
  const sel = $(id);
  if (!sel) return;
  const keep = sel.value;                       // survive a reload if still offered
  sel.innerHTML = `<option value="">${blank}</option>`;
  for (const r of rows) {
    // Days, not events. The study measures days, so this is the number that says
    // whether a choice leaves anything to measure.
    sel.add(new Option(`${r.name}  (${r.days} day${r.days === 1 ? '' : 's'})`, r.name));
  }
  sel.value = rows.some((r) => r.name === keep) ? keep : '';
}

async function loadEntities() {
  // Scoped to the actors and verb currently chosen, so the list only offers
  // narrowings that return something and the counts are counts for THIS question.
  const qs = new URLSearchParams({
    cameo: $('cameo').value,
    actorFrom: $('actorFrom').value,
    actorTo: $('actorTo').value,
  });
  let v;
  try {
    v = await (await fetch('api/entities?' + qs)).json();
  } catch (e) { return; }
  ENTITY_VOCAB = v;
  const before = [$('group').value, $('target').value];
  fillEntitySelect('group', v.groups, 'any group');
  fillEntitySelect('target', v.targets, 'any target');
  // Changing the actors can invalidate a narrowing. Dropping it silently would
  // answer a different question than the one on screen.
  const after = [$('group').value, $('target').value];
  const lost = [0, 1].filter((i) => before[i] && !after[i]);
  if (lost.length) {
    noteVocabReset(lost.map((i) => before[i]));
  } else {
    noteVocabReset(null);
  }
}

async function loadFinderEntities() {
  try {
    const v = await (await fetch('api/entities')).json();
    fillEntitySelect('findGroup', v.groups, 'any group');
    fillEntitySelect('findTarget', v.targets, 'any target');
  } catch (e) { /* leave the blanks */ }
}

function noteVocabReset(names) {
  let el = $('vocabNote');
  if (!el) {
    el = document.createElement('span');
    el.id = 'vocabNote';
    el.className = 'vocabnote';
    const t = $('target');
    if (t && t.parentNode) t.parentNode.appendChild(el);
  }
  el.textContent = names && names.length
    ? `— ${names.join(' and ')} ${names.length > 1 ? 'do' : 'does'} not appear in `
      + `this pairing, so ${names.length > 1 ? 'they were' : 'it was'} cleared`
    : '';
}

function noteCoverageSelection(on) {
  let el = $('coverageNote');
  if (!el) {
    el = document.createElement('p');
    el.id = 'coverageNote';
    el.className = 'foot warn';
    const v = $('verdictPanel') || $('verdict');
    if (v && v.parentNode) v.parentNode.insertBefore(el, v.nextSibling);
  }
  el.style.display = on ? '' : 'none';
  el.textContent = on
    ? 'This class was selected on coverage: the events are here because articles '
      + 'named the group or the target. That is a way to find events, not evidence '
      + 'that they moved the market — the same bar applies to the verdict above.'
    : '';
}

async function findDays() {
  const g = $('findGroup').value, t = $('findTarget').value;
  const panel = $('finderPanel');
  if (!g && !t) { panel.style.display = 'none'; return; }
  const qs = new URLSearchParams();
  if (g) qs.set('group', g);
  if (t) qs.set('target', t);
  const d = await (await fetch('api/entity-days?' + qs)).json();
  panel.style.display = '';
  $('finderSub').textContent = `${d.days.length} day${d.days.length === 1 ? '' : 's'}`
    + (d.days.length === 40 ? ' (top 40 by coverage)' : '');
  $('finder').innerHTML = d.days.length ? d.days.map((r) => `
    <tr class="pick" data-day="${r.day}">
      <td>${r.day}</td>
      <td class="num">${r.events}</td>
      <td class="num">${r.mentions.toLocaleString()}</td>
      <td class="dim">${[...r.perpetrators, ...r.facilities, ...r.operators]
        .slice(0, 4).join(', ') || '—'}</td>
    </tr>`).join('')
    : '<tr><td colspan="4" class="empty">no days match that pair</td></tr>';
}

async function askEvent(ev) {
  if (ev) ev.preventDefault();
  const btn = $('goEvent');
  btn.disabled = true; btn.textContent = 'Asking…';
  try {
    const date = $('eventDate').value || $('episode').value;
    // The 2026 example sits outside the delivered window. Move to a window that
    // holds the date rather than answer "no price data" for a day that has it.
    const here = WINDOW_LIST.find((x) => x.id === CORPUS_WINDOW);
    if (here && (date < here.from || date > here.to)) {
      const fits = WINDOW_LIST.find((x) => date >= x.from && date <= x.to);
      if (fits) selectWindow(fits.id);
    }
    const res = await fetch('api/event', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, horizon: +$('eventHorizon').value,
                             baseline: $('baseline').value,
                             window: CORPUS_WINDOW }),
    });
    const d = await res.json();
    if (!res.ok) {
      renderVerdict({ call: 'unknown', headline: 'Could not measure that date.',
                      detail: d.detail || '' });
      return;
    }
    renderVerdict(d.verdict);
    renderEventRows(d.rows, d.baseline);
    renderPortfolio(d.portfolio, d.verdict.call);
    renderMacro(d.macro);
    renderExposure(d.exposure, d.rows);
    renderPaths(d.paths);
    drawEventGraph(d.graph);
  } finally {
    btn.disabled = false; btn.textContent = 'Ask';
  }
}

async function ask(ev) {
  if (ev) ev.preventDefault();
  const btn = $('go');
  btn.disabled = true; btn.textContent = 'Asking…';
  try {
    const body = {
      cameo: $('cameo').value, actorFrom: $('actorFrom').value,
      actorTo: $('actorTo').value, horizon: +$('horizon').value, topK: 8,
      group: $('group').value || null, target: $('target').value || null,
      window: CORPUS_WINDOW,
    };
    const res = await fetch('api/whatif', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      renderVerdict({ call: 'unknown', headline: 'No analogs for that combination.',
                      detail: e.detail || 'Try a different pairing.' });
      cy.elements().remove();
      $('sweep').innerHTML = '<tr><td colspan="5" class="empty">—</td></tr>';
      $('outcome').innerHTML = '<tr><td colspan="4" class="empty">—</td></tr>';
      return;
    }
    const d = await res.json();
    renderVerdict(d.verdict);
    noteCoverageSelection(d.selectedOnCoverage);
    renderSweep(d.sweep);
    renderOutcome(d.outcome);
    renderPortfolio(d.portfolio, d.verdict.call);
    drawGraph(d.graph, d.outcome);
  } catch (err) {
    renderVerdict({ call: 'unknown', headline: 'Query failed.', detail: String(err) });
  } finally {
    btn.disabled = false; btn.textContent = 'Ask';
  }
}


// ---- plain English ----------------------------------------------------------
// Read, show, then ask on confirmation ("resolved slots shown and
// confirmed before execution"). A question the corpus cannot measure is refused
// with the reason, not answered from whatever the form happened to hold.
let PENDING = null;

async function askPlain(ev) {
  if (ev) ev.preventDefault();
  const text = $('nlText').value.trim();
  if (!text) return;
  const r = await fetch('api/parse?q=' + encodeURIComponent(text)).then((x) => x.json());
  if (r.refused) {
    PENDING = null;
    $('nlRead').innerHTML = `<strong>Not answerable here.</strong> ${r.refused}`;
    return;
  }
  const kept = (r.missing || []).map((k) => ({ actorFrom: 'who acts', cameo: 'the action',
    actorTo: 'who it is done to' }[k]));
  PENDING = r.mode === 'event'
    ? { mode: 'event', date: r.date, horizon: String(r.horizon) }
    : { mode: 'class', actorFrom: r.actorFrom, cameo: r.cameo, actorTo: r.actorTo,
        horizon: String(r.horizon), group: r.group, target: r.target };
  $('nlRead').innerHTML = `Read as: <strong>${r.reading}</strong>`
    + (kept.length ? ` Not stated: ${kept.join(', ')} — kept from the form below.` : '')
    + ' <button type="button" id="nlConfirm">Ask this</button>';
  $('nlConfirm').addEventListener('click', () => {
    if (!PENDING) return;
    document.querySelectorAll('.presets button').forEach((b) => b.classList.remove('on'));
    $('phint').textContent = '';
    runQuestion(PENDING);
    $('nlRead').innerHTML = `Asked: <strong>${r.reading}</strong> Change any field below to refine it.`;
  });
}

// ---- demo presets ----------------------------------------------------------
// The four questions the demo actually asks, in order. Each sets every control
// and fires, because hunting four dropdowns live while talking is how this goes
// wrong in front of people — and the pairing that matters (2 then 3) is the one
// most easily fumbled, since it is the same event asked two different ways.
const PRESETS = {
  tariff: {
    mode: 'class', actorFrom: 'USA', cameo: '163', actorTo: 'CHN', horizon: '5',
    hint: 'a class question — expect no detectable effect',
  },
  houthi: {
    mode: 'class', actorFrom: 'YEM', cameo: '190', actorTo: 'SAU', horizon: '5',
    hint: 'the class Abqaiq belongs to — 2019-09-16 is in the evidence, and it still says no',
  },
  abqaiq: {
    mode: 'event', date: '2019-09-14', horizon: '1',
    hint: 'the same attack, asked as one day — +14.68%, and the S&P moved -0.31%',
  },
  pricewar: {
    mode: 'event', date: '2020-03-09', horizon: '1',
    hint: 'the largest move in the corpus — but the S&P fell 7.60%, so quote Energy net of market',
  },
};

function applyPreset(name, btn) {
  const p = PRESETS[name];
  if (!p) return;
  document.querySelectorAll('.presets button').forEach((b) => b.classList.remove('on'));
  if (btn) btn.classList.add('on');
  $('phint').textContent = p.hint || '';
  $('nlRead').textContent = '';
  runQuestion(p);
}

// Set every control a question names and ask it. Shared by the presets and the
// plain-English box; a field the question leaves out keeps its current value.
function runQuestion(p) {
  setMode(p.mode, false);
  if (p.mode === 'class') {
    if (p.actorFrom) $('actorFrom').value = p.actorFrom;
    if (p.cameo) $('cameo').value = p.cameo;
    if (p.actorTo) $('actorTo').value = p.actorTo;
    $('horizon').value = p.horizon;
    // Setting .value does not fire 'change', so the scoped vocabulary would still
    // be the previous question's. A preset may also carry its own narrowing.
    $('group').value = '';
    $('target').value = '';
    loadEntities().then(() => {
      if (p.group) $('group').value = p.group;
      if (p.target) $('target').value = p.target;
      ask();
    });
  } else {
    // The episode <select> is filled by an async fetch during boot. Setting a
    // value it does not have yet silently leaves it empty, and askEvent() would
    // then post date:"" — so fall back to the date input, which always accepts one.
    $('episode').value = p.date;
    $('eventDate').value = $('episode').value === p.date ? '' : p.date;
    $('eventHorizon').value = p.horizon;
    askEvent();
  }
}

(async function boot() {
  initCy();
  initTheme();
  loadFinderEntities();
  $('baseline').addEventListener('change', () => {
    if (mode === 'event' && ($('eventDate').value || $('episode').value)) askEvent();
  });
  for (const id of ['findGroup', 'findTarget']) {
    const el = $(id);
    if (el) el.addEventListener('change', findDays);
  }
  $('finder').addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-day]');
    if (!row) return;
    $('eventDate').value = row.dataset.day;
    $('episode').value = '';
    askEvent();
  });
  cy.on('mouseover', 'node[meta]', (e) => showTip(e.target, e));
  cy.on('mousemove', 'node[meta]', (e) => showTip(e.target, e));
  cy.on('mouseout', 'node', hideTip);
  cy.on('tap', 'node[meta]', (e) => { hideTip(); showDetail(e.target); });
  cy.on('tap', (e) => { if (e.target === cy) { clearDetail(); clearTrace(); } });
  $('outcome').addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-sector]');
    if (row) traceSector(row.dataset.sector);
  });
  for (const [code, name] of Object.entries(COUNTRY)) {
    $('actorFrom').add(new Option(name, code, false, code === 'USA'));
    $('actorTo').add(new Option(name, code, false, code === 'CHN'));
  }
  // AFTER the actor options exist: the vocabulary is scoped to whatever they are
  // set to, and reading them before they are populated scopes it to nothing.
  loadEntities();
  for (const id of ['actorFrom', 'actorTo', 'cameo']) {
    $(id).addEventListener('change', loadEntities);
  }
  $('ask').addEventListener('submit', ask);
  $('nl').addEventListener('submit', askPlain);
  $('askEvent').addEventListener('submit', askEvent);
  // Choosing a day by hand leaves the preset behind: its highlight and its hint
  // described a different question, and a screenshot would carry both.
  const leavePreset = () => {
    document.querySelectorAll('.presets button').forEach((b) => b.classList.remove('on'));
    $('phint').textContent = '';
  };
  $('eventDate').addEventListener('change', () => { $('episode').value = ''; leavePreset(); });
  $('episode').addEventListener('change', () => { $('eventDate').value = ''; leavePreset(); });
  document.querySelectorAll('.mode').forEach((b) =>
    b.addEventListener('click', () => setMode(b.dataset.mode)));
  document.querySelectorAll('.presets button').forEach((b) =>
    b.addEventListener('click', () => applyPreset(b.dataset.preset, b)));

  // ?preset=<name> applies a preset on load. Two reasons beyond convenience: a demo
  // state becomes a shareable link, and a figure for a write-up can be re-captured by
  // fetching a URL instead of being reproduced by hand from a screenshot.
  const wanted = new URLSearchParams(location.search).get('preset');

  const eps = await fetch('api/episodes').then((r) => r.json()).catch(() => null);
  if (eps) {
    const grp = (label, rows) => {
      const g = document.createElement('optgroup');
      g.label = label;
      rows.forEach((e) => g.appendChild(new Option(`${e.label} — ${e.d}`, e.d)));
      $('episode').appendChild(g);
    };
    grp('Oil supply shocks', eps.oil);
    grp('Trade-war milestones', eps.tariff);
    $('episode').value = '2019-09-14';
  }
  if (wanted && PRESETS[wanted]) {
    const btn = document.querySelector(`.presets button[data-preset="${wanted}"]`);
    applyPreset(wanted, btn);
  }
  // Which span of the corpus questions are asked over. Hidden unless the database
  // holds more than one — an extended build carries both so the delivered numbers
  // stay reproducible beside the newer ones.
  fetch('api/windows').then((r) => r.json()).then((w) => {
    if (!w.windows || !w.windows.length) return;
    WINDOW_LIST = w.windows;
    if (w.windows.length < 2) { selectWindow(w.windows[0].id); return; }
    const el = $('windowPick');
    el.style.display = '';
    el.innerHTML = 'asked over <select id="windowSel"></select>';
    const sel = $('windowSel');
    for (const x of w.windows) sel.add(new Option(x.label, x.id));
    selectWindow(w.default || 'delivered');
    sel.addEventListener('change', () => {
      selectWindow(sel.value);
      loadEntities();
      (mode === 'class' ? ask : askEvent)();
    });
  }).catch(() => {});

  fetch('api/corpus').then((r) => r.json()).then((c) => {
    $('corpus').textContent =
      `${c.events.toLocaleString()} events · ${c.days} days · ${c.records.toLocaleString()} records`;
  }).catch(() => { $('corpus').textContent = 'corpus unavailable'; });
  fetch('api/oil').then((r) => r.json()).then(renderOil).catch(() => {});
  if (!(wanted && PRESETS[wanted])) ask();   // a preset has already asked its own question
})();
