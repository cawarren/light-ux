// Test 1 (§2.3): vendored score (Node + frozen Chromium POW table) must be bit-equal to the
// UNMODIFIED cmdk@1.1.1 dist commandScore running in the pinned Chromium.
//   node scripts/browser-bitexact.ts [--items F --queries F]
// Also reports (informationally) how often unmodified cmdk in *Node* differs, i.e. why the table exists.
import path from 'node:path';
import fs from 'node:fs';
import { launchChromium } from './chromium.ts';
import { installBatchScorer, CMDK_DIST } from './cmdk-browser.ts';
import { loadItems, loadQueries, arg } from './io.ts';
import { score, bitsHex } from '../src/index.ts';

const fixture = path.resolve(import.meta.dirname, '../../fixtures/f20k');
const items = loadItems(arg('--items', path.join(fixture, 'items.json'))!);
const queries = loadQueries(arg('--queries', path.join(fixture, 'queries.json'))!);
const { commandScore: nodeCmdk } = await import(path.join(CMDK_DIST, 'command-score.mjs'));

const browser = await launchChromium();
const page = await browser.newPage();
await installBatchScorer(page);

const mine = (it: string, q: string) => { try { return bitsHex(score(it, q)); } catch (e) { if (e instanceof RangeError) return 'RangeError'; throw e; } };
const nodeRaw = (it: string, q: string) => { try { return bitsHex(nodeCmdk(it, q, [])); } catch (e) { if (e instanceof RangeError) return 'RangeError'; throw e; } };

let pairs = 0, mismatches = 0, nodeRawMismatches = 0;
const badQids: (string | number)[] = [];
for (const { qid, q } of queries) {
  const chrome = await page.evaluate(([its, qq]) => (window as any).scoreAll(its, qq), [items, q] as const);
  if (chrome === 'RangeError') {
    pairs++;
    let threw = false; try { for (const it of items) score(it, q); } catch (e) { threw = e instanceof RangeError; }
    if (!threw) { mismatches++; badQids.push(qid); }
    continue;
  }
  let bad = false;
  for (let i = 0; i < items.length; i++) {
    pairs++;
    if (mine(items[i], q) !== chrome[i]) { mismatches++; bad = true; }
    if (nodeRaw(items[i], q) !== chrome[i]) nodeRawMismatches++;
  }
  if (bad) badQids.push(qid);
}
// Hand-written edge goldens must also hold for unmodified cmdk in Chromium.
const edge = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../goldens/edge-cases.json'), 'utf8'));
const str = (v: any) => (typeof v === 'string' ? v : String.fromCharCode(...v.units));
const edgeChrome: string[] = await page.evaluate((c) => (window as any).scoreBatch(c), edge.cases.map((c: any) => [str(c.item), str(c.q)]));
const edgeBad = edge.cases.filter((c: any, i: number) => edgeChrome[i] !== (c.error ?? c.bits)).map((c: any) => c.id);
mismatches += edgeBad.length;
await browser.close();
console.log(JSON.stringify({ edgeCases: edge.cases.length, edgeMismatches: edgeBad.length, edgeBad }));
console.log(JSON.stringify({ chromium: browser.version(), items: items.length, queries: queries.length, pairs, mismatches, badQids,
  info_unmodifiedCmdkInNode_vs_Chromium_mismatches: nodeRawMismatches }));
console.log(mismatches === 0 ? 'BITEXACT PASS' : 'BITEXACT FAIL');
process.exitCode = mismatches === 0 ? 0 : 1;
