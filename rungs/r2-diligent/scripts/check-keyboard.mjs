// R2 vs R1 keyboard / layout check (not part of the harness suite; run by hand):
//   (cd rungs/r1-vite && npx vite preview --port 3102 --strictPort) &
//   (cd rungs/r2-diligent && npm run serve -- --port 3103) &
//   node rungs/r2-diligent/scripts/check-keyboard.mjs
// 1. Drives the same key sequence in R1 (Vite) and R2 on the 1k and 10k datasets, after a query,
//    and asserts the selected item, its visibility in the list viewport and Enter's action agree.
// 2. Compares row heights (wrapping) of the first rows, R1 vs R2.
// 3. R2 only: with the list scrolled far down, a query change still flips the marker exactly once,
//    in the same frame as every list mutation and after the last one, resets to scrollTop 0 and
//    selects the first result.
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../tests/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers';

const R1 = process.env.R1_URL ?? 'http://localhost:3102';
const R2 = process.env.R2_URL ?? 'http://localhost:3103';
const INPUT = '[data-ladder-input], [cmdk-input]';
const LIST = '[data-ladder-list], [cmdk-list]';
let failures = 0;
const check = (ok, msg) => { if (!ok) { failures++; console.error('FAIL', msg); } };

const browser = await chromium.launch({ channel: 'chromium' });
async function open(base, size) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${base}/?items=/dataset/${size}/items.json`);
  await page.waitForFunction(() => window.__ladder?.dataset, null, { timeout: 120_000 });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50)))));
  return page;
}
const settle = (page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 30)))));
async function state(page) {
  return page.evaluate(({ LIST }) => {
    const list = document.querySelector(LIST);
    const sel = list.querySelector('[aria-selected="true"]');
    const lr = list.getBoundingClientRect();
    const r = sel?.getBoundingClientRect();
    return {
      selected: sel?.textContent ?? null,
      visible: r ? r.top >= lr.top - 0.5 && r.bottom <= lr.bottom + 0.5 : null,
      scrollTop: list.scrollTop,
      lastSelected: document.documentElement.dataset.lastSelected ?? null,
    };
  }, { LIST });
}

const KEYS = [
  ...Array(12).fill('ArrowDown'), 'ArrowUp', 'ArrowUp', 'Control+n', 'Control+j', 'Control+p', 'Control+k',
  'Alt+ArrowDown', 'Alt+ArrowUp', 'End', 'ArrowDown', 'ArrowUp', 'Home', 'ArrowUp', 'Meta+ArrowDown', 'Meta+ArrowUp',
  ...Array(40).fill('ArrowDown'), 'Enter', 'PageDown', 'Escape',
];
for (const [size, q] of [['1k', ''], ['1k', 'se'], ['10k', 'ope'], ['10k', 'zzzzqx'], ['10k', 'dwindle'], ['10k', 'fix ch'], ['10k', 'lumping']]) {
  const p1 = await open(R1, size);
  const p2 = await open(R2, size);
  for (const p of [p1, p2]) { await p.fill(INPUT, q); await settle(p); }
  for (const key of ['(start)', ...KEYS]) {
    if (key !== '(start)') for (const p of [p1, p2]) { await p.keyboard.press(key); await settle(p); }
    const [a, b] = [await state(p1), await state(p2)];
    // R1 quirk (not reproduced): after fill(), cmdk re-sorts by moving DOM nodes, Chrome's scroll
    // anchoring can leave the list scrolled, and cmdk only scrolls the first item into view when
    // the selected value changes. R2 always starts a new query at the top.
    const ok = key === '(start)'
      ? a.selected === b.selected && b.visible === true && b.scrollTop === 0
      : a.selected === b.selected && a.visible === b.visible && a.lastSelected === b.lastSelected;
    check(ok, `${size} q=${JSON.stringify(q)} after ${key}: R1 ${JSON.stringify(a)} R2 ${JSON.stringify(b)}`);
  }
  // Row heights of the rows R2 renders (wrapping must match R1's unvirtualized rows).
  const heights = (p) => p.evaluate(() => [...document.querySelectorAll('[data-ladder-item], [cmdk-item]')].slice(0, 60).map((e) => [e.textContent, e.getBoundingClientRect().height]));
  for (const p of [p1, p2]) { await p.fill(INPUT, ''); await p.fill(INPUT, q); await settle(p); }
  const [h1, h2] = [await heights(p1), await heights(p2)];
  const n = Math.min(h1.length, h2.length);
  check(JSON.stringify(h1.slice(0, n)) === JSON.stringify(h2.slice(0, n)), `${size} q=${JSON.stringify(q)}: row heights differ`);
  console.log(`${size} q=${JSON.stringify(q)}: ${KEYS.length} keys compared; ${n} row heights compared (${h2.filter((h) => h[1] > 32).length} wrapped rows)`);
  await p1.close();
  await p2.close();
}

// 3. R2: query change while scrolled down.
{
  const page = await open(R2, '10k');
  await page.evaluate((LIST) => window.__ladder.watchList(LIST), LIST);
  await page.focus(INPUT);
  await page.keyboard.press('End');
  await settle(page);
  const before = await state(page);
  check(before.scrollTop > 1000, `End scrolled the list (scrollTop ${before.scrollTop})`);
  for (const [action, want] of [[() => page.keyboard.type('s'), 's'], [() => page.keyboard.press('End'), null], [() => page.keyboard.type('e'), 'se'], [() => page.keyboard.press('Backspace'), 's']]) {
    const [n0, frame0] = await page.evaluate(() => [window.__ladder.flips.length, window.__ladder.frame]);
    await action();
    await settle(page); await settle(page);
    if (want === null) continue;
    const r = await page.evaluate((n0) => {
      const L = window.__ladder;
      const flip = L.flips[L.flips.length - 1];
      return { added: L.flips.length - n0, flip, honesty: L.honestyReport().find((h) => h.seq === flip.seq), res: L.results() };
    }, n0);
    const s = await state(page);
    check(r.added === 1 && r.flip.query === want, `one flip for ${want} (got ${r.added})`);
    // Mutations from the End key's scroll (before the query key) are "stray" for this flip; any
    // list mutation from the query key itself must be in the flip's frame, marker last.
    const strayFromQuery = r.honesty.strayFrames.filter((f) => f >= frame0);
    check(strayFromQuery.length === 0 && r.honesty.markerLast, `honest flip while scrolled: ${JSON.stringify(r.honesty)} (key at frame ${frame0})`);
    check(s.scrollTop === 0, `scrollTop reset to 0 (got ${s.scrollTop})`);
    check(r.res.selected === r.res.ids[0], 'first result selected');
    console.log(`scrolled query change -> ${JSON.stringify(want)}: ${JSON.stringify(r.honesty)} (key at frame ${frame0})`);
  }
  await page.close();
}
await browser.close();
if (failures) { console.error(`${failures} failures`); process.exit(1); }
console.log('keyboard/layout checks passed');
