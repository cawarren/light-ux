// Supersession check (R3-specific, not part of the shared suite): type faster than the worker
// can rank, in the real page, and check the policy in README "Supersession":
//   - flips only ever move forward in typing order (never an older query after a newer one);
//   - every flip is honest (same frame as the list mutations, marker last);
//   - the final flip is for the final input value, and the final list is strict-equal to rank();
//   - intermediate values may be skipped (reported, not an error).
// Usage (server running: npm run serve -- --port 3104):
//   node test/browser-supersession.ts [http://localhost:3104] [50k]
// Uses the repo root's playwright and parity/ranker-ts (dev-only; never imported by the app).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from '../../../node_modules/playwright/index.mjs';
import { rank } from '../../../parity/ranker-ts/src/index.ts';

process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers';
const base = process.argv[2] ?? 'http://localhost:3104';
const size = process.argv[3] ?? '50k';
const N = { '1k': 1000, '10k': 10000, '50k': 50000 }[size]!;
const items: string[] = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, `../../../dataset/out/dev-1/${N}/items.json`), 'utf8')).items;

// Timelines of input values, applied as one input event each, `gapMs` apart (0 = back to back).
const SCRIPTS: { name: string; values: string[]; gapMs: number }[] = [];
const typed = (q: string) => Array.from({ length: q.length }, (_, k) => q.slice(0, k + 1));
const back = (q: string) => Array.from({ length: q.length }, (_, k) => q.slice(0, q.length - 1 - k));
for (const gapMs of [0, 2, 8]) {
  SCRIPTS.push({ name: `type+clear gap ${gapMs}`, values: [...typed('open recent settings'), ...back('open recent settings')], gapMs });
  SCRIPTS.push({ name: `type, back 3, retype gap ${gapMs}`, values: [...typed('sett'), 'set', 'se', 's', ...typed('git br').slice(0)], gapMs });
  SCRIPTS.push({ name: `a, b, backspace to a gap ${gapMs}`, values: ['a', 'ab', 'a', 'ab', 'a'], gapMs });
  SCRIPTS.push({ name: `sigma-ish gap ${gapMs}`, values: ['Σ', 'ΣΣ', 'ΣΣa', 'ΣΣ', ''], gapMs });
}

const browser = await chromium.launch({ channel: 'chromium' });
let failures = 0;
for (const s of SCRIPTS) {
  const page = await browser.newPage();
  await page.goto(`${base}/?items=/dataset/${size}/items.json`);
  await page.waitForFunction((n) => window.__ladder?.dataset?.count === n, N, { timeout: 120_000 });
  await page.evaluate(() => window.__ladder!.watchList('[data-ladder-list]'));
  await page.evaluate(async ({ values, gapMs }) => {
    const input = document.querySelector<HTMLInputElement>('[data-ladder-input]')!;
    input.focus();
    for (const v of values) {
      input.value = v;
      input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
      if (gapMs) await new Promise((r) => setTimeout(r, gapMs));
    }
  }, s);
  const final = s.values[s.values.length - 1];
  // Settle: the last flip is for the final value (or nothing changed), and nothing is in flight.
  await page.waitForFunction((final) => (window.__ladder as any).results().query === final, final, { timeout: 60_000 });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50)))));
  const r = await page.evaluate(() => {
    const L = window.__ladder as any;
    return { flips: L.flips.map((f: any) => f.query), honesty: L.honestyReport(), results: L.results(), worker: (window as any).__r3.worker };
  });
  // Forward-only: map each flip to the LAST timeline position <= the previous match with that value.
  let pos = -1, ok = true;
  for (const q of r.flips) {
    let found = -1;
    for (let k = pos + 1; k < s.values.length; k++) if (s.values[k] === q) { found = k; break; }
    if (found < 0) { ok = false; break; }
    pos = found;
  }
  const want = rank(items, final).ids;
  const exact = JSON.stringify(r.results.ids) === JSON.stringify(want);
  const honest = r.honesty.every((h: any) => h.sameFrame && h.markerLast);
  const pass = ok && exact && honest && r.results.selected === (want.length ? want[0] : null);
  if (!pass) failures++;
  console.log(`${pass ? 'PASS' : 'FAIL'} ${s.name}: ${s.values.length} values -> ${r.flips.length} flips (${s.values.length - r.flips.length} coalesced), forward=${ok} exact=${exact} honest=${honest}; worker: ${r.worker.map((w: any) => `${w.how}:${w.ms.toFixed(1)}`).join(' ')}`);
  await page.close();
}
await browser.close();
assert.equal(failures, 0);
