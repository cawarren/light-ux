/* Latency Ladder playground UI (docs/phase-a/README.md §5). Keyboard-driven; no framework.
 * In blind and calibration sessions this page never learns which rung or delay an interval uses:
 * the server hands out opaque per-interval URLs and keeps the schedule to itself. */
'use strict';

// Contract rungs (R2+) use data-ladder-*; R1 predates the contract and uses cmdk's attributes.
const INPUT_SEL = '[data-ladder-input], [cmdk-input]';
const LIST_SEL = '[data-ladder-list], [cmdk-list]';
const $ = (s) => document.querySelector(s);
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));
const now = () => performance.now();
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const S = {
  state: null,
  size: null, // size for new sessions
  keyHandler: null,
  msgHandlers: [],
  open: { rung: 'r3', size: null, added: 0, hud: true },
  session: null,
  readyMs: 3000,
  abort: null,
};

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ------------------------------------------------------------------------ keys and messages
document.addEventListener('keydown', (e) => {
  if (S.keyHandler) S.keyHandler(e);
});
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || !e.data || typeof e.data !== 'object') return;
  S.msgHandlers.slice().forEach((h) => h(e.data));
  if (e.data.type === 'play:noedit') {
    const h = $('#noedit');
    if (h) { h.classList.remove('flash'); void h.offsetWidth; h.classList.add('flash'); }
  }
  if (e.data.type === 'play:key' && S.keyHandler) {
    S.keyHandler({ key: e.data.key || '', code: e.data.code || '', altKey: !!e.data.code, fromFrame: true, preventDefault() {} });
  }
});
function onMessage(pred) {
  return new Promise((resolve) => {
    const h = (d) => { if (pred(d)) { S.msgHandlers = S.msgHandlers.filter((x) => x !== h); resolve(d); } };
    S.msgHandlers.push(h);
  });
}
/** Resolve with the key pressed among `keys` (case-insensitive). Escape rejects when aborting is allowed. */
function waitKey(keys) {
  return abortable(new Promise((resolve) => {
    S.keyHandler = (e) => {
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (keys.includes(k)) { e.preventDefault(); S.keyHandler = sessionKeys; resolve(k); } else sessionKeys(e);
    };
  }));
}
function sessionKeys(e) {
  // While typing, Escape restarts the interval (handled in runTrial); elsewhere it pauses.
  if (e.key === 'Escape' && S.abort && !S.typing) S.abort.reject(new Error('abort'));
}
function newAbort() {
  let reject;
  const p = new Promise((_, rj) => { reject = rj; });
  p.catch(() => {});
  S.abort = { promise: p, reject };
}
function abortable(p) {
  return S.abort ? Promise.race([p, S.abort.promise]) : p;
}

// ------------------------------------------------------------------------ chrome
function bar({ mode = '', count = '', hint = '', progress = null }) {
  $('#bar-mode').textContent = mode;
  $('#bar-count').textContent = count;
  $('#bar-hint').innerHTML = hint;
  $('#bar-progress').hidden = progress == null;
  if (progress != null) $('#bar-progress-fill').style.width = `${Math.round(progress * 100)}%`;
}
function showOverlay(html) {
  $('#overlay-box').innerHTML = html;
  $('#overlay').hidden = false;
  $('#overlay').tabIndex = -1;
  $('#overlay').focus();
}
function hideOverlay() { $('#overlay').hidden = true; }
function prompt(html) { $('#prompt').innerHTML = html || ''; $('#prompt').hidden = !html; }
function screen(name) {
  $('#home').hidden = name !== 'home';
  $('#stage-wrap').hidden = name !== 'stage';
  $('#hud').hidden = !(name === 'stage' && S.mode === 'open' && S.open.hud);
}

// ------------------------------------------------------------------------ stage
const stage = () => $('#stage');
const frameWin = () => stage().contentWindow;

async function blank() {
  const f = stage();
  if (f.getAttribute('src') === 'about:blank') return;
  const loaded = new Promise((r) => f.addEventListener('load', r, { once: true }));
  f.src = 'about:blank';
  await Promise.race([loaded, sleep(2000)]);
}

/** Load a rung page and wait until it has its dataset rendered. Returns load time in ms. */
async function loadStage(url) {
  const f = stage();
  const t0 = now();
  const loaded = new Promise((r) => f.addEventListener('load', r, { once: true }));
  f.src = url;
  await abortable(loaded);
  for (;;) {
    const w = frameWin();
    if (w && w.__ladder && w.__ladder.dataset && w.__play && w.document.querySelector(INPUT_SEL)) break;
    if (now() - t0 > 180000) throw new Error('the palette did not finish loading within 3 minutes');
    await abortable(sleep(25));
  }
  return now() - t0;
}

/** Scroll the list to the top, clear buffers and focus the input (§5.2 leakage checks). */
function armStage() {
  const w = frameWin();
  const d = w.document;
  const list = d.querySelector(LIST_SEL);
  if (list) list.scrollTop = 0;
  w.scrollTo(0, 0);
  w.__play.refreshCover(); // colour the marker cover (hidden-marker sessions); outside the timed interval
  w.__ladder.reset();
  w.__play.reset();
  stage().focus();
  const inp = d.querySelector(INPUT_SEL);
  inp.focus();
  return inp;
}

function browserInfo() {
  const b = (navigator.userAgentData && navigator.userAgentData.brands || [])
    .find((x) => /Chrom|Edge|Opera|Brave/i.test(x.brand) && !/Not/i.test(x.brand));
  if (b) return `${b.brand} ${b.version}`;
  const m = navigator.userAgent.match(/(Firefox|Chrome|Version)\/(\d+)/);
  return m ? `${m[1] === 'Version' ? 'Safari' : m[1]} ${m[2]}` : 'unknown';
}

// ------------------------------------------------------------------------ home
async function home(msg) {
  S.mode = 'home';
  S.abort = null;
  S.keyHandler = null;
  document.body.dataset.screen = '';
  await blank();
  prompt('');
  S.state = await api('/api/state');
  const st = S.state;
  S.size = S.size || st.defaults.size;
  const d = st.defaults;
  const missing = st.rungs.filter((r) => !r.available).map((r) => r.id.toUpperCase());
  const resume = st.sessions.map((s, i) =>
    `<li data-k="${i + 1}"><kbd>${i + 1}</kbd><span>Resume ${s.kind === 'blind' ? 'blind test' : 'calibration'} · ${esc(s.size)}` +
    ` <span class="d">${s.done} / ${s.total} done · started ${esc(s.created_at.slice(0, 16).replace('T', ' '))}</span></span></li>`).join('');
  $('#home').innerHTML = `<div class="card">
    <h1>Latency Ladder playground</h1>
    <p>Feel the rungs, then test blind whether you can tell them apart (docs/phase-a §5).</p>
    ${msg ? `<p class="warn">${esc(msg)}</p>` : ''}
    ${!st.dataset ? '<p class="warn">No dataset in the rung builds: run <code>node rungs/scripts/prepare.mjs</code>.</p>' : ''}
    ${missing.length ? `<p class="warn">Not built: ${missing.join(', ')}. See playground/README.md.</p>` : ''}
    ${self.crossOriginIsolated ? '' : '<p class="warn">This page is not cross-origin isolated; timings are coarse.</p>'}
    <ul class="menu">
      <li data-k="o"><kbd>O</kbd><span>Open mode <span class="d">pick a rung and dataset size, type freely, see your latencies</span></span></li>
      <li data-k="b"><kbd>B</kbd><span>New blind test <span class="d">2AFC, ${d.trials_per_pair} trials × 4 pairs (R1–R3, R1–R2, R2–R3, R3–R3 placebo)</span></span></li>
      <li data-k="j"><kbd>J</kbd><span>New latency calibration <span class="d">R3 vs R3 + N ms (${d.delay_mode}), ${d.jnd_method === 'staircase' ? 'staircase' : `${d.jnd_reps} trials per level`}</span></span></li>
      ${resume}
    </ul>
    <p style="margin-top:14px">Dataset for new sessions: <b>${esc(S.size)}</b> <kbd>S</kbd> to change.
      ${d.allow_no_difference ? '“No difference” answers are enabled.' : 'Forced choice (no “no difference” answer).'}</p>
    <h2>How a blind trial works</h2>
    <p>Each trial has two intervals. A “get ready” screen stays up for a fixed time while the palette loads.
      Then type the word shown at the top in one go and press <kbd>Enter</kbd>. After the second interval,
      press <kbd>1</kbd> or <kbd>2</kbd> for the one that felt faster, then <kbd>1</kbd>–<kbd>3</kbd> for how sure you are.
      ${d.allow_backspace ? 'Backspace is allowed.' : 'Backspace is off in blind trials (calibration allows it).'}
      If you mistype, <kbd>Esc</kbd> restarts that interval.
      <kbd>Esc</kbd> on the other screens pauses; sessions resume where you left off.</p>
  </div>`;
  screen('home');
  bar({ mode: '', hint: '<kbd>O</kbd> open · <kbd>B</kbd> blind · <kbd>J</kbd> calibration' });
  document.querySelectorAll('#home li[data-k]').forEach((li) => li.addEventListener('click', () => homeKey(li.dataset.k)));
  S.keyHandler = (e) => {
    const k = e.key.toLowerCase();
    if (['o', 'b', 'j', 's'].includes(k) || /^[1-9]$/.test(k)) { e.preventDefault(); homeKey(k); }
  };
  document.body.dataset.screen = 'home'; // ready for keys (used by the e2e test)
}

function homeKey(k) {
  const st = S.state;
  if (k === 'o') return openMode();
  if (k === 's') {
    const i = st.sizes.indexOf(S.size);
    S.size = st.sizes[(i + 1) % st.sizes.length];
    return home();
  }
  if (k === 'b' || k === 'j') {
    return api('/api/sessions', { kind: k === 'b' ? 'blind' : 'jnd', size: S.size })
      .then((info) => runSession(info)).catch((e) => home(e.message));
  }
  const s = st.sessions[Number(k) - 1];
  if (s) {
    return api(`/api/sessions/${s.id}/open`, {}).then((info) => runSession(info)).catch((e) => home(e.message));
  }
}

// ------------------------------------------------------------------------ open mode
const DELAYS = [0, 8, 17, 25, 33, 50, 67, 100];
let hudTimer = null;

async function openMode() {
  S.mode = 'open';
  S.open.size = S.open.size || S.size || S.state.defaults.size;
  screen('stage');
  await openLoad();
  S.keyHandler = (e) => {
    const code = e.code || '';
    const alt = e.altKey || e.fromFrame;
    const k = (e.key || '').toLowerCase();
    let m = code.match(/^Digit([1-3])$/);
    if ((e.key === 'Escape' && !e.fromFrame) || (alt && code === 'KeyQ')) { clearInterval(hudTimer); return home(); }
    if (!alt && !(document.activeElement === document.body || document.activeElement === $('#overlay'))) return;
    if (m || (!e.fromFrame && /^[1-3]$/.test(k))) {
      const id = `r${m ? m[1] : k}`;
      if (S.state.rungs.find((r) => r.id === id && r.available)) { S.open.rung = id; openLoad(); }
    } else if (code === 'KeyS' || k === 's') {
      S.open.size = S.state.sizes[(S.state.sizes.indexOf(S.open.size) + 1) % S.state.sizes.length]; openLoad();
    } else if (code === 'KeyD' || k === 'd') {
      S.open.added = DELAYS[(DELAYS.indexOf(S.open.added) + 1) % DELAYS.length]; openLoad();
    } else if (code === 'KeyH' || k === 'h') {
      S.open.hud = !S.open.hud; screen('stage');
    }
  };
}

async function openLoad() {
  clearInterval(hudTimer);
  const o = S.open;
  const r = await api('/api/open', { rung: o.rung, size: o.size, added_ms: o.added });
  bar({ mode: `Open · ${r.label} · ${r.size}${r.added_ms ? ` · +${r.added_ms} ms (${r.mode})` : ''}`,
    hint: '<kbd>Alt</kbd>+<kbd>1</kbd>–<kbd>3</kbd> rung · <kbd>Alt</kbd>+<kbd>S</kbd> size · <kbd>Alt</kbd>+<kbd>D</kbd> added delay · <kbd>Alt</kbd>+<kbd>H</kbd> HUD · <kbd>Alt</kbd>+<kbd>Q</kbd> home' });
  showOverlay(`<div class="sub">Loading ${esc(r.label)} · ${esc(r.size)}…</div>`);
  renderHud(null, r);
  const ms = await loadStage(r.url);
  armStage();
  hideOverlay();
  o.loadMs = ms;
  hudTimer = setInterval(() => {
    const w = frameWin();
    if (w && w.__play) renderHud(w.__play.summary(), r);
  }, 300);
}

function renderHud(s, r) {
  const o = S.open;
  const opt = (arr, cur, lab) => arr.map((v) => `<option value="${v}" ${v === cur ? 'selected' : ''}>${lab(v)}</option>`).join('');
  const lat = s ? s.lat_ms.slice(-20).reverse() : [];
  $('#hud').innerHTML = `
    <h3>Condition</h3>
    <select id="h-rung">${opt(S.state.rungs.filter((x) => x.available).map((x) => x.id), o.rung, (v) => S.state.rungs.find((x) => x.id === v).label)}</select>
    <select id="h-size">${opt(S.state.sizes, o.size, (v) => `${v} items`)}</select>
    <select id="h-delay">${opt(DELAYS, o.added, (v) => v ? `+${v} ms added (${S.state.defaults.delay_mode})` : 'no added delay')}</select>
    <h3>Your last keystrokes</h3>
    <div class="row"><span>p50 / p95</span><b>${s && s.p50 != null ? `${s.p50.toFixed(1)} / ${s.p95.toFixed(1)} ms` : '—'}</b></div>
    <div class="row"><span>measured keys</span><span>${s ? s.n_measured : 0}</span></div>
    <div class="row"><span>ET input delay p50</span><span>${s && s.et_input_delay_p50 != null ? s.et_input_delay_p50.toFixed(1) + ' ms' : '—'}</span></div>
    <div class="row"><span>frame</span><span>${s && s.frame_ms ? `${s.frame_ms.toFixed(1)} ms (${Math.round(1000 / s.frame_ms)} Hz)` : '—'}</span></div>
    <div class="row"><span>load</span><span>${o.loadMs ? (o.loadMs / 1000).toFixed(2) + ' s' : '…'}</span></div>
    <div class="lat">${lat.map((x) => `${x.toFixed(1)} ms`).join('<br>') || '<span class="note">type in the palette</span>'}</div>
    <p class="note">Key → frame with the marker flip for that query (${s && s.endpoint ? esc(s.endpoint) + ' time' : 'Element Timing / next frame'}).
      Measured from the key's <code>event.timeStamp</code>, i.e. when the browser read the key: OS delivery is not included (§5.1).
      ${s && s.cross_origin_isolated === false ? '<b class="warn">Not cross-origin isolated.</b>' : ''}</p>`;
  $('#h-rung').onchange = (e) => { o.rung = e.target.value; openLoad(); };
  $('#h-size').onchange = (e) => { o.size = e.target.value; openLoad(); };
  $('#h-delay').onchange = (e) => { o.added = Number(e.target.value); openLoad(); };
}

// ------------------------------------------------------------------------ sessions
const roundUp = (ms, step = 500) => Math.ceil(ms / step) * step;

async function runSession(info) {
  S.mode = info.kind;
  S.session = info;
  const t = info.timing;
  const label = info.kind === 'blind' ? 'Blind test' : 'Latency calibration';
  screen('stage');
  newAbort();
  S.keyHandler = sessionKeys;
  try {
    // Warm-up: load each palette the session uses once, unseen, to fix a get-ready time that
    // hides load-time differences (§5.2). Order is random and nothing is shown.
    bar({ mode: label, hint: '' });
    showOverlay('<div class="big">Preparing</div><div class="sub">Loading the palettes once to calibrate the get-ready screen…</div>');
    const w = await abortable(api(`/api/sessions/${info.id}/warmup`, {}));
    let maxLoad = 0;
    for (const url of w.urls) { maxLoad = Math.max(maxLoad, await loadStage(url)); await blank(); }
    S.readyMs = Math.max(t.ready_ms, roundUp(maxLoad * 1.25 + t.settle_ms));
    let lastBreak = -1;
    for (;;) {
      const nx = await abortable(api(`/api/sessions/${info.id}/next`, {}));
      const pr = nx.progress;
      bar({ mode: label, count: `${pr.done} / ${pr.total}`, progress: pr.total ? pr.done / pr.total : 0,
        hint: '<kbd>Esc</kbd> pause (between intervals)' });
      if (nx.done) return finished(info);
      if (pr.done > 0 && pr.done % info.break_every === 0 && lastBreak !== pr.done) {
        lastBreak = pr.done;
        showOverlay(`<div class="big">Break</div><div class="sub">${pr.done} of ${pr.total} trials done. Rest your eyes and hands.<br><br>
          <kbd>Space</kbd> continue · <kbd>Esc</kbd> stop here (resume later from the home screen)</div>`);
        await waitKey([' ']);
      }
      const payload = await runTrial(nx, info);
      const res = await api(`/api/sessions/${info.id}/answer`, payload);
      bar({ mode: label, count: `${res.progress.done} / ${res.progress.total}`,
        progress: res.progress.total ? res.progress.done / res.progress.total : 0, hint: '<kbd>Esc</kbd> pause (between intervals)' });
    }
  } catch (e) {
    if (e.message !== 'abort') { console.error(e); return home(`Error: ${e.message}`); }
    await blank();
    prompt('');
    showOverlay(`<div class="big">Paused</div><div class="sub">The current trial will start again from its first interval.<br><br>
      <kbd>Space</kbd> continue · <kbd>Q</kbd> home</div>`);
    S.abort = null;
    const k = await waitKey([' ', 'q']);
    if (k === 'q') return home();
    return api(`/api/sessions/${info.id}/open`, {}).then(runSession);
  }
}

async function runTrial(nx, info) {
  const t = info.timing;
  const intervals = [];
  const tTrial = new Date().toISOString();
  const word = `<b>${esc(nx.prompt)}</b>`;
  let frameMs = null;
  let coi = null;
  const noEdit = !info.allow_editing;
  const hint = noEdit ? ' <span id="noedit" class="hint">· no editing — press <kbd>Esc</kbd> to restart the interval</span>'
    : ' <span class="hint">· <kbd>Esc</kbd> restarts the interval</span>';
  for (let k = 0; k < 2; k++) {
    let restarts = 0;
    for (;;) {
      prompt(`Interval ${k + 1} of 2 · type ${word} then <kbd>Enter</kbd>${hint}`);
      showOverlay(`<div class="big">Interval ${k + 1} of 2</div><div class="sub">Get ready to type ${word}</div>
        ${noEdit ? '<div class="sub" style="font-size:13px;margin-top:6px">Type it straight through: backspace is off. Made a typo? <kbd>Esc</kbd> restarts the interval.</div>' : ''}
        <div class="ready-bar"><i></i></div>`);
      const readyMs = S.readyMs;
      requestAnimationFrame(() => {
        const i = $('#overlay-box .ready-bar i');
        if (i) { i.style.transition = `width ${readyMs}ms linear`; i.style.width = '100%'; }
      });
      const t0 = now();
      const loadMs = await loadStage(nx.urls[k]);
      const overrun = loadMs + t.settle_ms > readyMs;
      await abortable(sleep(Math.max(t0 + readyMs, t0 + loadMs + t.settle_ms) - now()));
      const holdMs = now() - t0;
      if (overrun) S.readyMs = roundUp((loadMs + t.settle_ms) * 1.1);
      const enter = onMessage((d) => d.type === 'play:enter' && d.value.trim() !== '');
      const restart = onMessage((d) => d.type === 'play:key' && d.key === 'Escape');
      armStage();
      hideOverlay();
      S.typing = true;
      const tStart = now();
      let ended;
      try {
        ended = await abortable(Promise.race([enter, restart]));
      } finally {
        S.typing = false;
      }
      if (ended.type === 'play:key') {
        // Restart cleanly: discard this load and its timings, reload the same condition from the
        // get-ready screen (which hides load time as before).
        restarts++;
        showOverlay('<div class="sub">Restarting the interval…</div>');
        await blank();
        continue;
      }
      const typingMs = now() - tStart;
      showOverlay('<div class="sub">·</div>');
      await abortable(sleep(t.post_ms));
      const w = frameWin();
      const sum = w.__play.summary();
      frameMs = frameMs || sum.frame_ms;
      coi = sum.cross_origin_isolated;
      const typed = sum.typed;
      delete sum.typed;
      intervals.push({ load_ms: Math.round(loadMs), hold_ms: Math.round(holdMs), ready_ms: readyMs,
        ready_overrun: overrun, typing_ms: Math.round(typingMs), restarts, typed, latency: sum });
      await blank();
      break;
    }
  }
  prompt('');
  const nd = info.allow_no_difference;
  const choiceHtml = (sel) => `<div class="big">Which felt faster, more responsive?</div>
    <div class="choices"><div class="choice ${sel === '1' ? 'sel' : ''}"><kbd>1</kbd><br>first</div>
    <div class="choice ${sel === '2' ? 'sel' : ''}"><kbd>2</kbd><br>second</div></div>
    ${nd ? '<div class="sub"><kbd>0</kbd> no difference (logged separately)</div>' : ''}`;
  let answer, conf, rt;
  for (;;) {
    showOverlay(choiceHtml(null));
    const tA = now();
    answer = await waitKey(nd ? ['1', '2', '0'] : ['1', '2']);
    rt = now() - tA;
    showOverlay(`${choiceHtml(answer)}<div class="sub">How sure? <kbd>1</kbd> guessing · <kbd>2</kbd> fairly sure · <kbd>3</kbd> sure</div>
      <div class="sub" style="margin-top:6px;font-size:13px"><kbd>Backspace</kbd> changes the answer</div>`);
    const c = await waitKey(['1', '2', '3', 'Backspace']);
    if (c !== 'Backspace') { conf = Number(c); break; }
  }
  showOverlay('<div class="sub">·</div>');
  return {
    index: nx.index, answer: answer === '0' ? null : Number(answer), no_difference: answer === '0',
    confidence: conf, rt_ms: Math.round(rt), t_trial_start: tTrial, intervals,
    client: { browser: browserInfo(), cross_origin_isolated: coi, frame_ms: frameMs, dpr: devicePixelRatio,
      viewport: `${innerWidth}x${innerHeight}` },
  };
}

function finished(info) {
  prompt('');
  showOverlay(`<div class="big">Session complete</div><div class="sub">Thank you. Results are in
    <code>playground/results/${esc(info.id)}.jsonl</code>.<br>Summarize with
    <code>python3 playground/play.py report playground/results</code>.<br><br><kbd>Space</kbd> home</div>`);
  S.abort = null;
  waitKey([' ']).then(() => home());
}

home().catch((e) => { document.body.textContent = `Playground failed to start: ${e.message}`; });
