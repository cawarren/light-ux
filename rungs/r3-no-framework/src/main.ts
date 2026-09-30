// R3 "No framework": hand-written TypeScript on the DOM.
//   - fixed pool of POOL recycled row nodes; rows are never created after startup, only their text
//     and attributes are rewritten in place (only changed values are written);
//   - fixed 32 px rows, geometry computed arithmetically: the write path never reads layout
//     (scrollTop is tracked from scroll events, the one place it is read, before any write);
//   - CSS containment on the list, sizer, window and rows (styles.css);
//   - filtering in a Web Worker (worker.ts); the main thread only renders.
//
// Supersession policy (the main thread is the only scheduler; see README):
//   - at most ONE query is in flight to the worker;
//   - while it runs, further keystrokes only update `wanted` (the input's current value);
//   - when a result arrives it is displayed (with its marker flip) if it is newer than what is on
//     screen, then, if the input has moved on, the LATEST value is sent; intermediate values are
//     never computed, displayed or flipped for;
//   - the empty query is answered on the main thread in the input task itself (all ids, dataset
//     order), and any older in-flight result is then dropped by sequence number;
//   - at most one list update (= one marker flip) per frame: an update that would land in a frame
//     that already has one waits for the next rAF callback, where only the newest waiting one is
//     applied (two flips in one frame would cancel out on screen).
// So a displayed list is never older than the previous displayed list, and a key typed while the
// worker is busy waits for at most the in-flight query plus its own.
import type { Ladder } from './ladder-types.ts';

declare global {
  interface Window { __r3?: R3Debug }
}
interface R3Debug { worker: { seq: number; q: string; ms: number; how: string }[]; prepMs: number }

const ROW_H = 32; // px, fixed (styles.css .item)
const LIST_MAX_H = 288; // R1 max-h-72
const VISIBLE = LIST_MAX_H / ROW_H; // 9
const SCROLL_PAD = 4; // R1 scroll-py-1 (scrollIntoView block: nearest)
const POOL = 50; // >= VISIBLE + overscan; 50 so the probe's top-50 read sees the first 50 results
const OVERSCAN_BEFORE = 8;
const EMPTY_H = 68; // "No results found." (py-6 + 20 px line)

const L = window.__ladder;
const itemsUrl = new URLSearchParams(location.search).get('items') ?? '/dataset/items.json';

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const input = $<HTMLInputElement>('[data-ladder-input]');
const list = $<HTMLDivElement>('[data-ladder-list]');
const sizer = $<HTMLDivElement>('.sizer');
const win = $<HTMLDivElement>('.window');
const empty = $<HTMLDivElement>('.empty');
const live = $<HTMLDivElement>('.live');

// ---------------------------------------------------------------- state
let items: string[] = [];
let allIds: Int32Array = new Int32Array(0);
let shownIds: Int32Array = new Int32Array(0);
let shownQuery = '';
let shownSeq = 0; // seq of the displayed result
let selIndex = -1; // index into shownIds
let start = 0; // result index shown by pool row 0
let scrollTop = 0; // tracked, never read in the write path
let nextSeq = 1;
let inFlight: { seq: number; q: string } | null = null;
let workerReady = false;
let mainReady = false;

// Last written values per pool row / element, so unchanged values are never rewritten.
interface Row { el: HTMLDivElement; text: Text; attached: boolean; id: number; sel: boolean; pos: number }
const rows: Row[] = [];
let wListH = -1, wSizerH = -1, wStart = -1, wSetSize = -1, wEmpty = true, wActive = '', wLive = '';

for (let k = 0; k < POOL; k++) {
  const el = document.createElement('div');
  el.className = 'item';
  el.id = `r3-opt-${k}`;
  el.setAttribute('role', 'option');
  el.setAttribute('data-ladder-item', '');
  el.setAttribute('aria-selected', 'false');
  const text = document.createTextNode('');
  el.appendChild(text);
  rows.push({ el, text, attached: false, id: -1, sel: false, pos: -1 });
}

// ---------------------------------------------------------------- rendering (writes only)
function renderRows() {
  const n = shownIds.length;
  if (wSetSize !== n) {
    wSetSize = n;
    for (const r of rows) r.el.setAttribute('aria-setsize', String(n));
  }
  for (let k = 0; k < POOL; k++) {
    const r = rows[k];
    const idx = start + k;
    if (idx < n) {
      const id = shownIds[idx];
      if (r.id !== id) {
        r.id = id;
        r.text.data = items[id];
        r.el.setAttribute('data-id', String(id));
      }
      if (r.pos !== idx) { r.pos = idx; r.el.setAttribute('aria-posinset', String(idx + 1)); }
      const sel = idx === selIndex;
      if (r.sel !== sel) { r.sel = sel; r.el.setAttribute('aria-selected', sel ? 'true' : 'false'); }
      if (!r.attached) { r.attached = true; win.appendChild(r.el); } // attached rows stay a prefix
    } else if (r.attached) {
      r.attached = false;
      r.el.remove();
    }
  }
  if (wStart !== start) { wStart = start; win.style.transform = `translateY(${start * ROW_H}px)`; }
}

function renderFrame() {
  const n = shownIds.length;
  const listH = n === 0 ? EMPTY_H : Math.min(n * ROW_H, LIST_MAX_H);
  if (wListH !== listH) { wListH = listH; list.style.height = `${listH}px`; }
  const sizerH = n * ROW_H;
  if (wSizerH !== sizerH) { wSizerH = sizerH; sizer.style.height = `${sizerH}px`; }
  if (wEmpty !== (n === 0)) { wEmpty = n === 0; empty.hidden = !wEmpty; }
}

function renderActive() {
  const k = selIndex - start;
  const active = selIndex >= 0 && k >= 0 && k < POOL ? rows[k].el.id : '';
  if (wActive !== active) {
    wActive = active;
    if (active) input.setAttribute('aria-activedescendant', active);
    else input.removeAttribute('aria-activedescendant');
  }
}

function startFor(top: number) {
  const first = Math.floor(top / ROW_H);
  return Math.max(0, Math.min(first - OVERSCAN_BEFORE, shownIds.length - POOL));
}

/** Show a new result list: the only path that changes the list for a query. Flips the marker last. */
function show(q: string, ids: Int32Array, seq: number) {
  shownQuery = q;
  shownIds = ids;
  shownSeq = seq;
  selIndex = ids.length ? 0 : -1; // R1/cmdk: first item selected after every query change
  if (scrollTop !== 0) { scrollTop = 0; list.scrollTop = 0; } // before any other write
  start = 0;
  renderFrame();
  renderRows();
  renderActive();
  const msg = ids.length ? `${ids.length} result${ids.length === 1 ? '' : 's'}` : 'No results found.';
  if (wLive !== msg) { wLive = msg; live.textContent = msg; }
  L?.markerFlip(q, { count: ids.length }); // last write for this query, same task
}

// ---------------------------------------------------------------- search scheduling
const worker = new Worker('/worker.js');
const debug: R3Debug = { worker: [], prepMs: 0 };
window.__r3 = debug;

function wantedQuery() { return input.value; }

// At most one list update (and so one marker flip) per frame. Two flips in one frame would put
// the marker back to its old colour in the presented frame while the list changed. A result that
// arrives when this frame already has an update waits in `pending` for the next rAF callback (the
// start of the next frame), and a newer one replaces it there.
let updatedThisFrame = false;
let pending: { q: string; ids: Int32Array; seq: number } | null = null;
function onFrame() {
  updatedThisFrame = false;
  if (pending) {
    const p = pending;
    pending = null;
    commit(p.q, p.ids, p.seq);
  }
}
function commit(q: string, ids: Int32Array, seq: number) {
  show(q, ids, seq);
  updatedThisFrame = true;
  requestAnimationFrame(onFrame);
}
/** What the list will show once any pending update is applied. */
function targetQuery() { return pending ? pending.q : shownQuery; }
function targetSeq() { return pending ? pending.seq : shownSeq; }

/** Offer a finished result for display (see "Supersession" at the top). */
function offer(q: string, ids: Int32Array, seq: number) {
  if (seq <= targetSeq()) return; // older than what is (or is about to be) on screen
  if (q === shownQuery) { pending = null; return; } // the screen already shows it
  // The screen already matches the input (e.g. typed "b", then Backspace, while "ab" was in flight).
  if (!pending && wantedQuery() === shownQuery) return;
  if (updatedThisFrame) pending = { q, ids, seq };
  else commit(q, ids, seq);
}

function pump() {
  if (!workerReady || !mainReady || inFlight) return;
  const q = wantedQuery();
  if (q === '' || q === targetQuery()) return;
  inFlight = { seq: nextSeq++, q };
  worker.postMessage({ type: 'query', seq: inFlight.seq, q });
}

worker.onmessage = (e: MessageEvent) => {
  const m = e.data;
  if (m.type === 'ready') {
    workerReady = true;
    debug.prepMs = m.prepMs;
    maybeLoaded();
    pump();
    return;
  }
  if (m.type === 'result') {
    inFlight = null;
    if (debug.worker.length > 200) debug.worker.shift();
    debug.worker.push({ seq: m.seq, q: m.q, ms: m.ms, how: m.how });
    offer(m.q, m.ids as Int32Array, m.seq);
    pump();
  }
};
worker.postMessage({ type: 'init', url: new URL(itemsUrl, location.href).href });

input.addEventListener('input', () => {
  const q = wantedQuery();
  if (q === '') {
    // Answered here, in the input task (or the next frame if this one already has an update);
    // an in-flight answer is now older (seq) and gets dropped.
    if (mainReady && targetQuery() !== '') offer('', allIds, nextSeq++);
    return;
  }
  pump();
});

// ---------------------------------------------------------------- selection and scrolling
function select(idx: number, keepInView: boolean) {
  const n = shownIds.length;
  if (!n) return;
  idx = Math.max(0, Math.min(n - 1, idx));
  if (idx === selIndex) return;
  selIndex = idx;
  if (keepInView) {
    const top = idx * ROW_H, bottom = top + ROW_H;
    const viewH = Math.min(n * ROW_H, LIST_MAX_H);
    const maxTop = Math.max(0, n * ROW_H - viewH);
    let t = scrollTop;
    if (top < t + SCROLL_PAD) t = top - SCROLL_PAD;
    else if (bottom > t + viewH - SCROLL_PAD) t = bottom - viewH + SCROLL_PAD;
    t = Math.max(0, Math.min(maxTop, t));
    if (t !== scrollTop) { scrollTop = t; list.scrollTop = t; start = startFor(t); }
  }
  renderRows();
  renderActive();
}

list.addEventListener('scroll', () => {
  const t = list.scrollTop; // the only layout read; nothing has been written in this task
  if (t === scrollTop) return;
  scrollTop = t;
  const s = startFor(t);
  if (s !== start) { start = s; renderRows(); renderActive(); }
}, { passive: true });

function activate() {
  if (selIndex < 0) return;
  // Same visible no-op action as R1's onSelect (the keyboard parity script can assert it).
  document.documentElement.dataset.lastSelected = items[shownIds[selIndex]];
}

input.addEventListener('keydown', (e) => {
  if (e.isComposing || e.keyCode === 229) return; // IME composition owns the keys
  const ctrlOnly = e.ctrlKey && !e.metaKey && !e.altKey;
  let to: number | null = null;
  switch (e.key) {
    case 'ArrowDown': to = e.metaKey ? shownIds.length - 1 : selIndex + 1; break;
    case 'ArrowUp': to = e.metaKey ? 0 : selIndex - 1; break;
    case 'n': case 'j': if (ctrlOnly) to = selIndex + 1; break; // cmdk vim bindings
    case 'p': case 'k': if (ctrlOnly) to = selIndex - 1; break;
    case 'PageDown': to = selIndex + VISIBLE; break; // parity extra (cmdk has none)
    case 'PageUp': to = selIndex - VISIBLE; break;
    case 'Enter': e.preventDefault(); activate(); return;
    // Home/End are NOT intercepted: they edit the text input (cmdk moves the selection instead).
  }
  if (to === null) return;
  e.preventDefault();
  select(to, true);
});

win.addEventListener('pointermove', (e) => {
  const el = (e.target as HTMLElement).closest<HTMLElement>('.item');
  if (!el) return;
  const k = rows.findIndex((r) => r.el === el);
  if (k >= 0) select(start + k, false);
});
win.addEventListener('click', (e) => {
  const el = (e.target as HTMLElement).closest<HTMLElement>('.item');
  if (!el) return;
  const k = rows.findIndex((r) => r.el === el);
  if (k >= 0) { select(start + k, false); activate(); input.focus(); }
});

// ---------------------------------------------------------------- contract: results()
// Virtualized rung: the full ranking currently displayed (CONTRACT.md "Correctness").
if (L) {
  (L as Ladder & { results: () => unknown }).results = () => ({
    ids: Array.from(shownIds),
    selected: selIndex >= 0 ? shownIds[selIndex] : null,
    query: shownQuery,
  });
}

// ---------------------------------------------------------------- startup
function maybeLoaded() {
  if (workerReady && mainReady) L?.datasetLoaded({ url: itemsUrl, count: items.length });
}

fetch(itemsUrl)
  .then((r) => r.json())
  .then((json: { items: string[] }) => {
    items = json.items;
    allIds = new Int32Array(items.length);
    for (let i = 0; i < items.length; i++) allIds[i] = i;
    shownIds = allIds;
    shownQuery = '';
    selIndex = items.length ? 0 : -1;
    renderFrame();
    renderRows();
    renderActive();
    mainReady = true;
    maybeLoaded();
    pump(); // anything typed before the data arrived
  });
