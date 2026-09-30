// Offline checks of R3's search core (node --test test/). Test-only imports from parity/ are fine;
// the app never imports parity/ at runtime.
//   1. src/vendor/command-score.ts is upstream cmdk 1.1.1 verbatim (SHA-256).
//   2. Fast scorer == vendored scorer (same engine, same Math.pow) on the dev-1 dataset x queries,
//      and on a fuzz corpus over a hazard alphabet.
//   3. rankIds with the frozen Chromium POW table == ranker-ts rank() (strict) on dev-1.
//   4. Incremental narrowing: score(item, q + c) > 0  =>  score(item, q) > 0 (fuzz, vendored oracle),
//      and narrowed rankings == full rankings along every typed prefix chain.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { commandScore } from '../src/vendor/command-score.ts';
import { canNarrow, createScorer, enginePowTable, formatInput, prepare, rankIds } from '../src/scorer.ts';
import { rank, POW_0999 } from '../../../parity/ranker-ts/src/index.ts';

const ROOT = path.resolve(import.meta.dirname, '../../..');
const OUT = path.join(ROOT, 'dataset/out', process.env.LADDER_SEED ?? 'dev-1');
const items = (n: number): string[] => JSON.parse(fs.readFileSync(path.join(OUT, String(n), 'items.json'), 'utf8')).items;
const queries = (): { qid: number; q: string; typeable: boolean; class: string }[] =>
  JSON.parse(fs.readFileSync(path.join(OUT, 'queries.json'), 'utf8')).queries;
const QN = Number(process.env.R3_TEST_QUERIES ?? 1000);

test('vendored command-score.ts is upstream cmdk 1.1.1 verbatim', () => {
  const src = fs.readFileSync(path.resolve(import.meta.dirname, '../src/vendor/command-score.ts'), 'utf8');
  const marker = '// ---- BEGIN VERBATIM UPSTREAM ----\n';
  const body = src.slice(src.indexOf(marker) + marker.length);
  assert.equal(createHash('sha256').update(body).digest('hex'), 'ccfd0d66e3d31b8197fc4dbb217c9672e7562569775ec3a4f3b7e567eb81dfc7');
  assert.ok(src.includes('Copyright (c) 2022 Paco Coursey'));
});

test('fast scorer == vendored scorer, dev-1 10k items x queries (bit-exact)', () => {
  const it = items(10_000);
  const qs = queries().slice(0, QN);
  const sc = createScorer(enginePowTable());
  const lower = it.map(formatInput);
  let pairs = 0, positive = 0;
  for (const { qid, q } of qs) {
    const lq = formatInput(q);
    for (let i = 0; i < it.length; i++) {
      const want = commandScore(it[i], q, []);
      const got = sc.score(it[i], lower[i], q, lq);
      if (!Object.is(got, want)) assert.fail(`qid ${qid} item ${i}: fast ${got} vendored ${want}`);
      pairs++; if (want > 0) positive++;
    }
  }
  console.log(`  ${pairs} pairs, ${positive} positive`);
});

// Hazard alphabet: separators, the JS whitespace set, hyphen, case pairs, repeated letters,
// astral characters (surrogate halves match unit by unit), Σ/σ/ς.
const ALPHA = ['a', 'b', 'c', 'A', 'B', 'o', 'p', 'P', ' ', '-', '_', '/', '.', '#', '@', '(', '[', '{', '&', '+', '"', '\\',
  ' ', '\t', '　', '﻿', 'Σ', 'σ', 'ς', '😀', '👩', '‍', 'é', 'e', '́', '設', '定', 'İ', 'i', '\u0307'];
function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function randStr(r: () => number, max: number, alpha = ALPHA) {
  const n = Math.floor(r() * (max + 1));
  let s = '';
  for (let i = 0; i < n; i++) s += alpha[Math.floor(r() * alpha.length)];
  return s;
}

test('fast scorer == vendored scorer, fuzz (hazard alphabet)', () => {
  const r = rng(1);
  const sc = createScorer(enginePowTable());
  const N = Number(process.env.R3_FUZZ ?? 300_000);
  for (let k = 0; k < N; k++) {
    const s = randStr(r, 24), a = randStr(r, 6);
    let want: number;
    try { want = commandScore(s, a, []); } catch { continue; } // upstream RangeError inputs
    const got = sc.score(s, formatInput(s), a, formatInput(a));
    if (!Object.is(got, want)) assert.fail(`fuzz ${k}: ${JSON.stringify(s)} / ${JSON.stringify(a)}: fast ${got} vendored ${want}`);
  }
});

test('rankIds (frozen Chromium POW table) == ranker-ts rank(), strict, dev-1 50k', () => {
  const it = items(50_000);
  const p = prepare(it);
  const sc = createScorer(Float64Array.from(POW_0999));
  const qs = queries().filter((_, i) => i % 10 === 0).slice(0, Math.max(1, Math.floor(QN / 10)));
  for (const { qid, q } of qs) {
    const want = rank(it, q).ids;
    const got = Array.from(rankIds(p, sc, q, null));
    assert.deepEqual(got, want, `qid ${qid} ${JSON.stringify(q)}`);
  }
});

test('narrowing is exact when canNarrow(q, q + ext): score(item, q + ext) > 0 implies score(item, q) > 0 (fuzz, vendored oracle)', () => {
  const r = rng(7);
  const small = ['a', 'b', 'A', 'o', 'p', ' ', '-', '/', '.', 'Σ', 'σ', 'ς', '😀', 'İ'];
  const N = Number(process.env.R3_FUZZ ?? 300_000);
  let checked = 0, refused = 0, refusedWouldFail = 0;
  for (let k = 0; k < N; k++) {
    const s = randStr(r, 16, small);
    const q = randStr(r, 5, small) || 'a';
    const ext = randStr(r, 3, small) || small[Math.floor(r() * small.length)];
    let wide: number, narrow: number;
    try { narrow = commandScore(s, q + ext, []); wide = commandScore(s, q, []); } catch { continue; }
    if (!(narrow > 0)) continue;
    if (!canNarrow(q, q + ext)) { refused++; if (!(wide > 0)) refusedWouldFail++; continue; }
    checked++;
    assert.ok(wide > 0, `${JSON.stringify(s)}: score(${JSON.stringify(q + ext)}) = ${narrow} but score(${JSON.stringify(q)}) = ${wide}`);
  }
  console.log(`  ${checked} positive (item, q + ext) pairs checked; ${refused} refused by canNarrow (${refusedWouldFail} of them real counterexamples)`);
  assert.ok(!canNarrow('- ΣΣ', '- ΣΣA'), 'final sigma counterexample is refused');
});

test('narrowed rankings == full rankings along typed prefix chains, dev-1 50k', () => {
  const it = items(50_000);
  const p = prepare(it);
  const sc = createScorer(enginePowTable());
  const qs = queries().filter((q) => q.typeable).slice(0, Math.max(4, Math.floor(QN / 25)));
  for (const { qid, q } of qs) {
    let prev: Int32Array | null = null;
    for (let k = 1; k <= q.length; k++) {
      const pq = q.slice(0, k);
      const cand = prev && canNarrow(q.slice(0, k - 1), pq) ? prev.slice().sort() : null;
      const narrowed = rankIds(p, sc, pq, cand);
      const full = rankIds(p, sc, pq, null);
      assert.deepEqual(Array.from(narrowed), Array.from(full), `qid ${qid} prefix ${JSON.stringify(pq)}`);
      prev = narrowed;
    }
  }
});
