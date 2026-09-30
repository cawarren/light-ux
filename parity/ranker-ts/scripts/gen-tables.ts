// Generate the frozen tables in parity/tables/ from the pinned Chromium, and
// compare Node's V8/ICU output with Chromium's.  Usage: node scripts/gen-tables.ts
import fs from 'node:fs';
import path from 'node:path';
import { launchChromium } from './chromium.ts';
import { engineProbe } from './probe.ts';

const POW_MAX = 4096;
const OUT = path.resolve(import.meta.dirname, '../../tables');

const browser = await launchChromium();
const page = await browser.newPage();
const ua = await page.evaluate(() => navigator.userAgent);
const chromiumVersion = browser.version();
const probeSrc = engineProbe.toString();
const chrome: ReturnType<typeof engineProbe> = await page.evaluate(([src, n]) => (0, eval)('(' + src + ')')(n), [probeSrc, POW_MAX] as const);
await browser.close();
const node = engineProbe(POW_MAX);

const engine = {
  chromium: chromiumVersion,
  userAgent: ua,
  playwrightBuild: 'chromium-1194',
};
const generatedBy = 'parity/ranker-ts/scripts/gen-tables.ts';

// Review-friendly JSON: top-level keys on their own lines; arrays of entries one entry per line.
function jsonLines(obj: Record<string, unknown>): string {
  const parts = Object.entries(obj).map(([k, v]) => {
    if (Array.isArray(v) && v.length > 8) return `${JSON.stringify(k)}: [\n  ${v.map((e) => JSON.stringify(e)).join(',\n  ')}\n]`;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const inner = Object.entries(v).map(([k2, v2]) => Array.isArray(v2) && v2.length > 8
        ? `  ${JSON.stringify(k2)}: [\n    ${v2.map((e) => JSON.stringify(e)).join(',\n    ')}\n  ]`
        : `  ${JSON.stringify(k2)}: ${JSON.stringify(v2)}`);
      return `${JSON.stringify(k)}: {\n${inner.join(',\n')}\n}`;
    }
    return `${JSON.stringify(k)}: ${JSON.stringify(v)}`;
  });
  return `{\n${parts.join(',\n')}\n}\n`;
}

// Invariant needed by the Rust port: lowercasing never shortens a string.
for (const [c, u] of chrome.lower) {
  const srcLen = c > 0xffff ? 2 : 1;
  if (u.length < srcLen) throw new Error(`lowercase of U+${c.toString(16)} shortens`);
}

fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, 'pow0999.json'), jsonLines({
  schema: 1,
  what: 'POW_0999[n] = Math.pow(0.999, n) as IEEE-754 binary64 bit patterns (big-endian hex), n = 0..nMax',
  generatedBy, engine, nMax: POW_MAX,
  bitsHex: chrome.pow,
}));

fs.writeFileSync(path.join(OUT, 'lowercase.json'), jsonLines({
  schema: 1,
  what: 'String.prototype.toLowerCase() as measured in the pinned Chromium (root locale).',
  generatedBy, engine,
  rules: [
    'map: for every code point c (0..0x10FFFF) with String.fromCodePoint(c).toLowerCase() !== String.fromCodePoint(c): [c, [UTF-16 units of the result]]. Code points not listed map to themselves (lone surrogates included).',
    'Final sigma (the only context-dependent rule in the root locale): U+03A3 lowers to U+03C2 (ς) iff (scanning backwards from Σ, skipping code points in preIgnorable, the first remaining code point exists and is in preCased) AND NOT (scanning forwards, skipping code points in folIgnorable, the first remaining code point exists and is in folCased); otherwise to U+03C3 (σ). Classes are measured, not taken from UCD: ICU treats a code point that is both Cased and Case_Ignorable as ignorable. Context is taken from the original (not yet lowercased) string, by code point; lone surrogates are neither cased nor ignorable.',
    'jsWhitespace: the UTF-16 code units matched by the JS RegExp /\\s/ (non-unicode mode). cmdk replaces /[\\s-]/g with U+0020 after lowercasing.',
  ],
  map: chrome.lower,
  sigma: chrome.sigma,
  jsWhitespace: chrome.jsWhitespace,
  sigmaSamples: chrome.sigmaSamples,
}));

// ---- Node vs Chromium comparison ----
const diffPow: [number, string, string][] = chrome.pow.flatMap((b: string, n: number): [number, string, string][] => (b === node.pow[n] ? [] : [[n, b, node.pow[n]]]));
const key = (e: [number, number[]]) => `${e[0]}:${e[1].join(',')}`;
const cm = new Map<number, string>(chrome.lower.map((e) => [e[0], key(e)]));
const nm = new Map<number, string>(node.lower.map((e) => [e[0], key(e)]));
const diffLower: string[] = [];
for (const c of new Set([...cm.keys(), ...nm.keys()])) if (cm.get(c) !== nm.get(c)) diffLower.push(`U+${c.toString(16).toUpperCase().padStart(4, '0')} chromium=${cm.get(c) ?? 'identity'} node=${nm.get(c) ?? 'identity'}`);
const expand = (l: [number, number][]) => { const s = new Set<number>(); for (const [a, b] of l) for (let x = a; x <= b; x++) s.add(x); return s; };
const fmt = (x: number) => 'U+' + x.toString(16).toUpperCase().padStart(4, '0');
const diffSigma = Object.fromEntries(Object.keys(chrome.sigma).map((k) => {
  const A = expand((chrome.sigma as any)[k]), B = expand((node.sigma as any)[k]);
  return [k, { onlyChromium: [...A].filter((x) => !B.has(x)).map(fmt), onlyNode: [...B].filter((x) => !A.has(x)).map(fmt) }];
}));
const ulp = (a: string, b: string) => Number(BigInt('0x' + a) - BigInt('0x' + b));
const ulpHist: Record<string, number> = {};
for (const [, c, n] of diffPow) { const k = String(ulp(c, n)); ulpHist[k] = (ulpHist[k] ?? 0) + 1; }
const report = {
  schema: 1, generatedBy,
  chromium: engine,
  node: { version: process.version, v8: process.versions.v8, icu: process.versions.icu, unicode: process.versions.unicode },
  powDifferences: diffPow.length, powDiffUlpHistogram_chromiumMinusNode: ulpHist, powDiffs: diffPow,
  lowercaseDifferences: diffLower.length, lowercaseDiffs: diffLower.sort().slice(0, 200),
  sigmaClassDiffs: diffSigma,
  jsWhitespaceEqual: JSON.stringify(chrome.jsWhitespace) === JSON.stringify(node.jsWhitespace),
  sigmaSamplesEqual: JSON.stringify(chrome.sigmaSamples) === JSON.stringify(node.sigmaSamples),
};
fs.writeFileSync(path.join(OUT, 'engine-diff.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ ...report, powDiffs: `${diffPow.length} (first: ${diffPow.slice(0, 8).map((d) => d[0]).join(',')})`, lowercaseDiffs: report.lowercaseDiffs.length, sigmaClassDiffs: Object.fromEntries(Object.entries(diffSigma).map(([k, v]: any) => [k, { onlyChromium: v.onlyChromium.length, onlyNode: v.onlyNode.length }])) }, null, 2));
console.log('lowercase entries:', chrome.lower.length, 'sigma:', Object.fromEntries(Object.entries(chrome.sigma).map(([k, v]) => [k, v.length])));
