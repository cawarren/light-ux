// Test 2 (§2.3): browser oracle. Real cmdk 1.1.1 React component in the pinned Chromium:
// for each query mount fresh, fill() once, read the [cmdk-item] DOM order and the selected item,
// and require strict equality with rank().
//   node scripts/browser-oracle.ts [--items F --queries F] [--limit 100]
// Default inputs: fixtures/f5k-clean (node scripts/gen-fixture.ts --count 5000 --clean --out ../fixtures/f5k-clean).
import path from 'node:path';
import { build } from 'esbuild';
import { launchChromium } from './chromium.ts';
import { loadItems, loadQueries, arg } from './io.ts';
import { rank } from '../src/index.ts';

const fixture = path.resolve(import.meta.dirname, '../../fixtures/f5k-clean');
const items = loadItems(arg('--items', path.join(fixture, 'items.json'))!);
let queries = loadQueries(arg('--queries', path.join(fixture, 'queries.json'))!);
const limit = Number(arg('--limit', '100'));

// Top up to `limit` with queries derived from items (deterministic: prefixes / word starts of every k-th item).
for (let k = 0; queries.length < limit && k < items.length; k += 97) {
  const it = items[k];
  const q = k % 3 === 0 ? it.slice(0, 3) : k % 3 === 1 ? it.split(/[ /._-]/).map((w) => w[0] ?? '').join('').slice(0, 4) : it.slice(2, 6);
  if (q) queries.push({ qid: `derived-${k}`, q });
}
queries = queries.slice(0, limit);

const bundle = await build({
  entryPoints: [path.join(import.meta.dirname, 'browser-app.tsx')],
  bundle: true, write: false, format: 'iife', jsx: 'automatic', minify: true,
  define: { 'process.env.NODE_ENV': '"production"' }, target: 'es2020',
});

const browser = await launchChromium();
const page = await browser.newPage();
await page.setContent('<!doctype html><html><body></body></html>');
await page.addScriptTag({ content: bundle.outputFiles[0].text });
await page.evaluate((its) => { (window as any).__items = its; }, items);

const settle = () => page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
const read = () => page.evaluate(() => ({
  ids: [...document.querySelectorAll('[cmdk-item]')].map((e) => Number((e as HTMLElement).dataset.id)),
  selected: (() => { const s = document.querySelector('[cmdk-item][aria-selected="true"]') as HTMLElement | null; return s ? Number(s.dataset.id) : null; })(),
}));

let pass = 0, fail = 0, idsCompared = 0, nonEmpty = 0;
for (const { qid, q } of queries) {
  await page.evaluate(() => (window as any).__mount((window as any).__items));
  await page.waitForSelector('[cmdk-input]');
  await settle();
  if (q !== '') await page.fill('[cmdk-input]', q);
  // wait until the list is stable across two reads
  let prev = await read(); let cur = prev;
  for (let k = 0; k < 20; k++) { await settle(); cur = await read(); if (JSON.stringify(cur) === JSON.stringify(prev)) break; prev = cur; }
  const want = rank(items, q);
  idsCompared += cur.ids.length; if (want.ids.length && q !== '') nonEmpty++;
  const ok = JSON.stringify(cur.ids) === JSON.stringify(want.ids) && cur.selected === want.selected;
  ok ? pass++ : fail++;
  console.log(`${qid}\t${ok ? 'PASS' : 'FAIL'}${ok ? '' : `\tcount dom=${cur.ids.length} ref=${want.ids.length}`}`);
}
await browser.close();
console.log(`browser oracle (cmdk 1.1.1 React, Chromium ${browser.version()}, ${items.length} items): ${pass} pass, ${fail} fail of ${queries.length} (${nonEmpty} non-empty result lists, ${idsCompared} ids compared)`);
process.exitCode = fail ? 1 : 0;
