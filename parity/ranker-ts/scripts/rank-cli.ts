// rank-cli (TS): same modes and output as parity/ranker-rs `rank-cli`.
//   node scripts/rank-cli.ts <scores|ranked|golden|bench> --items items.json --queries queries.json [--out F] [--repeat N]
import fs from 'node:fs';
import { score, rank, bitsHex, engineLowercaseMismatches } from '../src/index.ts';
import { goldenLine } from '../src/golden.ts';
import { loadItems, loadQueries, arg } from './io.ts';

const mode = process.argv[2];
const items = loadItems(arg('--items')!);
const queries = loadQueries(arg('--queries')!);
const outPath = arg('--out');
const lines: string[] = [];
const emit = (o: unknown) => lines.push(JSON.stringify(o));

if (!process.env.LADDER_SKIP_ENGINE_CHECK) {
  const bad = engineLowercaseMismatches();
  if (bad.length) {
    // Only matters if the data contains one of these code points.
    const badSet = new Set(bad);
    const hit = [...items, ...queries.map((r) => r.q)].some((s) => [...s].some((ch) => badSet.has(ch.codePointAt(0)!)));
    process.stderr.write(`note: this engine's toLowerCase differs from the Chromium table on ${bad.length} code points; data ${hit ? 'CONTAINS some -> results are NOT reference' : 'contains none -> OK'}\n`);
    if (hit) process.exitCode = 3;
  }
}

if (mode === 'scores') {
  for (const { qid, q } of queries) {
    try { emit({ qid, scores: items.map((it) => bitsHex(score(it, q))) }); }
    catch (e) { if (e instanceof RangeError) emit({ qid, error: 'RangeError' }); else throw e; }
  }
} else if (mode === 'ranked') {
  for (const { qid, q } of queries) {
    try {
      const r = rank(items, q);
      emit({ qid, count: r.ids.length, selected: r.selected, ids: r.ids, scores: r.scores ? r.scores.map(bitsHex) : null });
    } catch (e) { if (e instanceof RangeError) emit({ qid, error: 'RangeError' }); else throw e; }
  }
} else if (mode === 'golden') {
  for (const { qid, q } of queries) emit(goldenLine(items, qid, q));
} else if (mode === 'bench') {
  const repeat = Number(arg('--repeat', '1'));
  const times: number[] = [];
  let matched = 0;
  for (let k = 0; k < repeat; k++) for (const { q } of queries) {
    const t = performance.now();
    try { matched += rank(items, q).ids.length; } catch { continue; /* upstream-RangeError queries are not timed */ }
    times.push(performance.now() - t);
  }
  times.sort((a, b) => a - b);
  const pct = (p: number) => times[Math.round((times.length - 1) * p)];
  emit({ impl: `ranker-ts (Node ${process.version})`, items: items.length, queries: queries.length, repeat,
    meanMs: times.reduce((a, b) => a + b, 0) / times.length, p50Ms: pct(0.5), p95Ms: pct(0.95), maxMs: pct(1), matched });
} else {
  console.error('usage: rank-cli <scores|ranked|golden|bench> --items F --queries F [--out F]');
  process.exit(2);
}
const text = lines.join('\n') + (lines.length ? '\n' : '');
if (outPath) fs.writeFileSync(outPath, text); else process.stdout.write(text);
