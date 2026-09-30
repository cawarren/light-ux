// Differential TS <-> Rust: run both rank-clis on the same inputs and compare bit-for-bit.
//   node scripts/diff-ts-rs.ts [--items F --queries F] [--modes scores,ranked,golden]
// Prints per-mode mismatch counts only (qids of mismatching queries, no text).
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { arg, readJsonl } from './io.ts';

const here = import.meta.dirname;
const fixture = path.resolve(here, '../../fixtures/f20k');
const items = arg('--items', path.join(fixture, 'items.json'))!;
const queries = arg('--queries', path.join(fixture, 'queries.json'))!;
const modes = arg('--modes', 'scores,ranked,golden')!.split(',');
const rsDir = path.resolve(here, '../../ranker-rs');
const outDir = path.resolve(here, '../../out');
fs.mkdirSync(outDir, { recursive: true });

execFileSync('cargo', ['build', '--release', '-q'], { cwd: rsDir, stdio: 'inherit' });
const rsBin = path.join(rsDir, 'target/release/rank-cli');

// Canonical form: recursively sort object keys (serde_json and JSON.stringify order keys differently).
const canon = (v: any): any => Array.isArray(v) ? v.map(canon) : v && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])])) : v;

let total = 0;
for (const mode of modes) {
  const tsOut = path.join(outDir, `ts-${mode}.jsonl`), rsOut = path.join(outDir, `rs-${mode}.jsonl`);
  execFileSync('node', [path.join(here, 'rank-cli.ts'), mode, '--items', items, '--queries', queries, '--out', tsOut], { stdio: ['ignore', 'inherit', 'inherit'], maxBuffer: 1 << 30 });
  execFileSync(rsBin, [mode, '--items', items, '--queries', queries, '--out', rsOut], { stdio: 'inherit' });
  const a = readJsonl(tsOut), b = readJsonl(rsOut);
  let lines = 0, values = 0, pairMism = 0, bad: (string | number)[] = [];
  if (a.length !== b.length) throw new Error(`${mode}: line count ${a.length} vs ${b.length}`);
  for (let i = 0; i < a.length; i++) {
    lines++;
    if (mode === 'scores' && a[i].scores) {
      values += a[i].scores.length;
      if (b[i].scores) for (let k = 0; k < a[i].scores.length; k++) if (a[i].scores[k] !== b[i].scores[k]) pairMism++;
    }
    if (mode === 'ranked' && a[i].ids) values += a[i].ids.length;
    if (JSON.stringify(canon(a[i])) !== JSON.stringify(canon(b[i]))) bad.push(a[i].qid);
  }
  const errs = a.filter((x) => x.error).map((x) => x.qid);
  total += bad.length;
  console.log(`${mode}: queries=${lines} ${mode === 'scores' ? `scorePairs=${values} pairMismatches=${pairMism}` : mode === 'ranked' ? `rankedIds=${values}` : ''} mismatchingQueries=${bad.length}${bad.length ? ' qids=' + bad.join(',') : ''}${errs.length ? ` (both-sides RangeError qids=${errs.join(',')})` : ''}`);
}
console.log(total === 0 ? 'DIFF PASS: 0 mismatches' : `DIFF FAIL: ${total} mismatching query lines`);
process.exitCode = total === 0 ? 0 : 1;
