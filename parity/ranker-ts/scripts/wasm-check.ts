// Runs the wasm32-unknown-unknown build of ladder-rank (examples/wasm_probe) in Node and compares
// every (item, query) score bit pattern with ranker-ts on the fixture.
//   node scripts/wasm-check.ts [--items F --queries F]
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { loadItems, loadQueries, arg } from './io.ts';
import { score, bitsHex } from '../src/index.ts';

const rsDir = path.resolve(import.meta.dirname, '../../ranker-rs');
execFileSync('cargo', ['build', '--release', '-q', '--no-default-features', '--example', 'wasm_probe', '--target', 'wasm32-unknown-unknown'], { cwd: rsDir, stdio: 'inherit' });
const wasm = fs.readFileSync(path.join(rsDir, 'target/wasm32-unknown-unknown/release/examples/wasm_probe.wasm'));
const { instance } = await WebAssembly.instantiate(wasm, {});
const X = instance.exports as any;

const fixture = path.resolve(import.meta.dirname, '../../fixtures/f20k');
const items = loadItems(arg('--items', path.join(fixture, 'items.json'))!);
const queries = loadQueries(arg('--queries', path.join(fixture, 'queries.json'))!);
const push = (s: string) => { for (let i = 0; i < s.length; i++) X.push_unit(s.charCodeAt(i)); };
for (const it of items) { push(it); X.commit_item(); }

let pairs = 0, mism = 0;
for (const { q } of queries) {
  push(q); X.commit_query();
  for (let i = 0; i < items.length; i++) {
    pairs++;
    let want: string;
    try { want = bitsHex(score(items[i], q)); } catch (e) { if (e instanceof RangeError) want = 'RangeError'; else throw e; }
    const g = X.score_item(i);
    const got = g === -1 ? 'RangeError' : bitsHex(g);
    if (got !== want) mism++;
  }
}
console.log(JSON.stringify({ target: 'wasm32-unknown-unknown', pairs, mismatches: mism, info_wasm_powf_vs_table_diffs: X.powf_diffs() }));
console.log(mism === 0 ? 'WASM PASS' : 'WASM FAIL');
process.exitCode = mism ? 1 : 0;
