// Property-based differential fuzzing: random (item, query) pairs over a tricky alphabet,
// oracle in JS, candidate = Rust `rank-cli fuzz-serve` over a JSONL pipe (UTF-16 unit arrays,
// so lone surrogates survive transport). Also compares Rust's table-driven toLowerCase.
//   node scripts/fuzz.ts [--cases 200000] [--seed 1] [--oracle node|chromium] [--wide]
// --wide also draws arbitrary code points (whole Unicode range) and extra Σ, to exercise the
// lowercase table and Final_Sigma classes; use it with --oracle chromium (Node's ICU differs on
// a few post-Unicode-15 code points, see tables/engine-diff.json).
// oracle=node:     vendored score (frozen POW table) on Node's V8 + Node's toLowerCase.
// oracle=chromium: UNMODIFIED cmdk@1.1.1 dist commandScore + toLowerCase in the pinned Chromium.
import { spawnSync, execFileSync } from 'node:child_process';
import path from 'node:path';
import { arg } from './io.ts';
import { score, bitsHex } from '../src/index.ts';
import { launchChromium } from './chromium.ts';
import { installBatchScorer } from './cmdk-browser.ts';
import { LOWERCASE_TABLE } from '../src/tables.ts';

const cases = Number(arg('--cases', '200000'));
const oracle = arg('--oracle', 'node')!;
let st = Number(arg('--seed', '1')) >>> 0;
const rnd = () => { st = (st + 0x6d2b79f5) >>> 0; let t = st; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const ri = (n: number) => Math.floor(rnd() * n);
const pick = <T,>(a: readonly T[]) => a[ri(a.length)];

const G = {
  letters: ['a', 'a', 'b', 'o', 'p', 'p', 'e', 'n', 's', 'A', 'B', 'O', 'P', 'E', 'N', 'S', 'i', 'I', 'k', 'K', 'x'],
  seps: [' ', ' ', '-', '/', '\\', '_', '.', '#', '@', '+', '&', '(', '[', '{', '"', ')', ',', ':', "'"],
  // full JS \s set, plus look-alikes that are NOT \s in JS (U+0085, U+180E, U+200B)
  ws: ['\t', '\n', '\v', '\f', '\r', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', '　', '﻿', '\u0085', '᠎', '​'],
  casing: ['İ', 'ı', '̇', 'Σ', 'Σ', 'σ', 'ς', 'Α', 'Ο', 'ẞ', 'ß', 'ǅ', 'ǆ', 'Ǆ', 'ﬁ', 'K', 'Ω', 'Å', 'é', 'é', 'Ω'],
  ignorable: ["'", '­', 'ͅ', 'ʼ', '́', 'ʰ', 'ᴬ', ':', '.', '‍'],
  astral: ['\u{10400}', '\u{10428}', '😀', '👩', '💻', '\u{1D400}', '\u{1F1EF}', '\u{1F1F5}', '\ud83d', '\ude00', '\udc00'],
};
const wide = process.argv.includes('--wide');
const sigmaCps: number[] = [];
for (const k of ['preCased', 'preIgnorable', 'folCased', 'folIgnorable'] as const) for (const [a, b] of LOWERCASE_TABLE.sigma[k]) for (let c = a; c <= b; c++) sigmaCps.push(c);
const mapCps = LOWERCASE_TABLE.map.map((e) => e[0]);
const wideUnit = () => { const r = rnd(); return r < 0.3 ? 'Σ' : r < 0.65 ? String.fromCodePoint(pick(sigmaCps)) : r < 0.85 ? String.fromCodePoint(pick(mapCps)) : anyCp(); };
const anyCp = () => { let c; do c = ri(0x110000); while (c >= 0xd800 && c <= 0xdfff); return String.fromCodePoint(c); };
(G as any).wide = { length: 1 };
const classes: [keyof typeof G | 'wide', number][] = [['letters', 50], ['seps', 12], ['ws', 10], ['casing', 12], ['ignorable', 6], ['astral', 10], ...(wide ? [['wide', 20] as ['wide', number]] : [])];
const wsum = classes.reduce((a, c) => a + c[1], 0);
const unit = (): string => { let r = ri(wsum); for (const [k, w] of classes) { if ((r -= w) < 0) return k === 'wide' ? wideUnit() : pick(G[k]); } return 'a'; };
const randStr = (max: number) => { let s = ''; const n = ri(max + 1); for (let i = 0; i < n; i++) s += rnd() < 0.15 && s ? s[s.length - 1] : unit(); return s; };
const mutate = (it: string) => {
  // derive a query from the item so many pairs score > 0: subsequence, transpositions, case flips, dups
  let q = '';
  for (const ch of it) if (rnd() < 0.4) q += rnd() < 0.2 ? (rnd() < 0.5 ? ch.toUpperCase() : ch.toLowerCase()) : ch;
  const a = [...q];
  if (a.length > 1 && rnd() < 0.3) { const k = ri(a.length - 1); [a[k], a[k + 1]] = [a[k + 1], a[k]]; }
  if (a.length && rnd() < 0.2) { const k = ri(a.length); a.splice(k, 0, a[k]); }
  if (rnd() < 0.1) a.push(unit());
  return a.join('');
};
const units = (s: string) => Array.from({ length: s.length }, (_, i) => s.charCodeAt(i));

const pairs: [string, string][] = [];
for (let n = 0; n < cases; n++) {
  const it = randStr(24);
  const q = rnd() < 0.6 ? mutate(it) : randStr(8);
  pairs.push([it, q]);
}

// Oracle
let want: string[], wantLower: string[];
if (oracle === 'node') {
  want = pairs.map(([it, q]) => { try { return bitsHex(score(it, q)); } catch (e) { if (e instanceof RangeError) return 'RangeError'; throw e; } });
  wantLower = pairs.map(([it]) => it.toLowerCase());
} else {
  const browser = await launchChromium();
  const page = await browser.newPage();
  await installBatchScorer(page);
  want = []; wantLower = [];
  for (let k = 0; k < pairs.length; k += 20000) {
    const chunk = pairs.slice(k, k + 20000);
    want.push(...await page.evaluate((c) => (window as any).scoreBatch(c), chunk));
    wantLower.push(...await page.evaluate((c) => c.map(([it]: [string, string]) => it.toLowerCase()), chunk));
  }
  console.log(`oracle: unmodified cmdk@1.1.1 in Chromium ${browser.version()}`);
  await browser.close();
}

// Candidate: Rust over a JSONL pipe
const rsDir = path.resolve(import.meta.dirname, '../../ranker-rs');
execFileSync('cargo', ['build', '--release', '-q'], { cwd: rsDir, stdio: 'inherit' });
const input = pairs.map(([it, q]) => JSON.stringify({ i: units(it), q: units(q) })).join('\n') + '\n';
const r = spawnSync(path.join(rsDir, 'target/release/rank-cli'), ['fuzz-serve'], { input, maxBuffer: 1 << 30 });
if (r.status !== 0) throw new Error('rust fuzz-serve failed: ' + r.stderr);
const got = r.stdout.toString().trim().split('\n').map((l) => JSON.parse(l));

let mism = 0, lowerMism = 0, nonzero = 0, rangeErr = 0;
const shown: string[] = [];
for (let i = 0; i < pairs.length; i++) {
  const g = got[i].s ?? got[i].err;
  if (want[i] === 'RangeError') rangeErr++; else if (want[i] !== '0000000000000000') nonzero++;
  if (g !== want[i]) { mism++; if (shown.length < 10) shown.push(`score ${JSON.stringify(pairs[i])} oracle=${want[i]} rust=${g}`); }
  if (String.fromCharCode(...got[i].l) !== wantLower[i]) { lowerMism++; if (shown.length < 10) shown.push(`lower ${JSON.stringify(pairs[i][0])}`); }
}
for (const s of shown) console.log('  ' + s);
console.log(JSON.stringify({ oracle, wide, cases: pairs.length, nonzeroScores: nonzero, rangeErrorCases: rangeErr, scoreMismatches: mism, lowercaseMismatches: lowerMism }));
console.log(mism + lowerMism === 0 ? 'FUZZ PASS' : 'FUZZ FAIL');
process.exitCode = mism + lowerMism === 0 ? 0 : 1;
