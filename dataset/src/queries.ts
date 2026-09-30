// Query generator (§1.3): stratified classes with fixed quotas, drawn from the
// target dataset with its own PRNG streams (one per class).

import { STREAM, stream, type Xoshiro128ss } from "./prng.ts";
import {
  approxClusters,
  codePoints,
  fromCodePoints,
  hasNfdDecomposition,
  isTypeable,
  LATIN_RANGES,
  toNfd,
} from "./unicode.ts";

export const QUERY_CLASSES = [
  "single-char",
  "word-prefix",
  "multi-word-prefix",
  "acronym",
  "mid-word",
  "transposition",
  "doubled-letter",
  "case-variant",
  "separator-variant",
  "whitespace",
  "non-latin",
  "nfd",
  "no-match",
  "ascii-20",
] as const;
export type QueryClass = (typeof QUERY_CLASSES)[number];

/** Quota weights per 1,000 queries. */
export const QUERY_WEIGHTS: Record<QueryClass, number> = {
  "single-char": 80,
  "word-prefix": 140,
  "multi-word-prefix": 100,
  acronym: 70,
  "mid-word": 80,
  transposition: 60,
  "doubled-letter": 50,
  "case-variant": 60,
  "separator-variant": 60,
  whitespace: 40,
  "non-latin": 80,
  nfd: 30,
  "no-match": 50,
  "ascii-20": 100,
};

export interface Query {
  qid: number;
  q: string;
  class: QueryClass;
  typeable: boolean;
}

/** Integer quotas summing to `count` (largest remainder, ties by class order). */
export function quotas(count: number): Record<QueryClass, number> {
  let total = 0;
  for (const c of QUERY_CLASSES) total += QUERY_WEIGHTS[c];
  const out = {} as Record<QueryClass, number>;
  const rems: [QueryClass, number, number][] = [];
  let assigned = 0;
  QUERY_CLASSES.forEach((c, idx) => {
    const num = count * QUERY_WEIGHTS[c];
    out[c] = Math.floor(num / total);
    assigned += out[c];
    rems.push([c, num % total, idx]);
  });
  rems.sort((a, b) => b[1] - a[1] || a[2] - b[2]);
  for (let i = 0; i < count - assigned; i++) {
    const c = (rems[i] as [QueryClass, number, number])[0];
    out[c] += 1;
  }
  return out;
}

// --- matching approximation used to *guarantee* no-match -----------------

/**
 * Over-approximation of cmdk's match set: q (lower-cased) matches s if q is
 * a subsequence of s after deleting any set of query characters that are not
 * the first and not adjacent to another deleted character. cmdk's
 * transposition / duplicate branch can only skip such characters, so if
 * this returns false, cmdk's score is 0. Works on UTF-16 code units like cmdk.
 */
export function relaxedMatch(qLower: string, sLower: string): boolean {
  const n = qLower.length;
  if (n === 0) return true;
  const INF = Number.MAX_SAFE_INTEGER;
  // P[i]: earliest string position after consuming i query chars, last matched.
  // S[i]: same, last skipped.
  const P = new Array<number>(n + 1).fill(INF);
  const S = new Array<number>(n + 1).fill(INF);
  P[0] = 0;
  for (let i = 0; i < n; i++) {
    const ch = qLower[i] as string;
    const p = P[i] as number;
    const s = S[i] as number;
    if (p !== INF) {
      const j = sLower.indexOf(ch, p);
      if (j >= 0 && j + 1 < (P[i + 1] as number)) P[i + 1] = j + 1;
      // cmdk skips q[i] only if q[i] occurs in s (just before the match of
      // q[i-1]) or q[i] === q[i-1]; "occurs anywhere in s" over-approximates.
      if (i >= 1 && p < (S[i + 1] as number) && (ch === qLower[i - 1] || sLower.includes(ch))) S[i + 1] = p;
    }
    if (s !== INF) {
      const j = sLower.indexOf(ch, s);
      if (j >= 0 && j + 1 < (P[i + 1] as number)) P[i + 1] = j + 1;
    }
  }
  return P[n] !== INF || S[n] !== INF;
}

/** Plain subsequence test (the realism report's approximation). */
export function isSubsequence(qLower: string, sLower: string): boolean {
  let j = 0;
  for (let i = 0; i < qLower.length; i++) {
    j = sLower.indexOf(qLower[i] as string, j);
    if (j < 0) return false;
    j++;
  }
  return true;
}

// --- helpers --------------------------------------------------------------

function isAlnum(c: number): boolean {
  return (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a);
}
function isLower(c: number): boolean {
  return c >= 0x61 && c <= 0x7a;
}
function isUpper(c: number): boolean {
  return c >= 0x41 && c <= 0x5a;
}
function isDigit(c: number): boolean {
  return c >= 0x30 && c <= 0x39;
}

/** Maximal runs of ASCII letters/digits. */
function asciiWords(s: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (isAlnum(c)) cur += s[i];
    else if (cur) {
      out.push(cur);
      cur = "";
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** Split a word at camelCase humps and letter/digit boundaries. */
function humps(w: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < w.length; i++) {
    const c = w.charCodeAt(i);
    const p = i > 0 ? w.charCodeAt(i - 1) : -1;
    const boundary = i > 0 && ((isUpper(c) && isLower(p)) || isDigit(c) !== isDigit(p));
    if (boundary && cur) {
      out.push(cur);
      cur = "";
    }
    cur += w[i];
  }
  if (cur) out.push(cur);
  return out;
}

function lowerWords(s: string, minLen: number): string[] {
  return asciiWords(s)
    .map((w) => w.toLowerCase())
    .filter((w) => w.length >= minLen && /^[a-z]/.test(w));
}

function isLatinCp(cp: number): boolean {
  for (const [lo, hi] of LATIN_RANGES) if (cp >= lo && cp <= hi) return true;
  return false;
}

// --- generator -----------------------------------------------------------

interface Ctx {
  /** Items queries are drawn from (the smallest nested prefix). */
  items: readonly string[];
  /** Lower-cased items that no-match / NFD guarantees are checked against (the largest size). */
  checkLower: readonly string[];
  nonLatinIdx: readonly number[];
  nfdIdx: readonly number[];
  rareChars: readonly string[];
  absentChars: readonly string[];
}

type Maker = (r: Xoshiro128ss, ctx: Ctx) => string | null;

function pickItem(r: Xoshiro128ss, ctx: Ctx): string {
  return ctx.items[r.below(ctx.items.length)] as string;
}

function wordPrefix(r: Xoshiro128ss, ctx: Ctx): string | null {
  const ws = lowerWords(pickItem(r, ctx), 3);
  if (ws.length === 0) return null;
  const w = r.pick(ws);
  const len = r.range(2, Math.min(w.length, 8));
  return w.slice(0, len);
}

function multiWordPrefix(r: Xoshiro128ss, ctx: Ctx): string | null {
  const toks = pickItem(r, ctx).toLowerCase().split(" ");
  if (toks.length < 2) return null;
  const k = r.range(1, Math.min(toks.length - 1, 3));
  const next = toks[k] as string;
  if (next.length === 0) return null;
  const q = toks.slice(0, k).join(" ") + " " + next.slice(0, r.range(1, Math.min(next.length, 4)));
  return isTypeable(q) ? q : null;
}

function noMatchAgainstAll(q: string, ctx: Ctx): boolean {
  const ql = q.toLowerCase();
  for (const s of ctx.checkLower) if (relaxedMatch(ql, s)) return false;
  return true;
}

const MAKERS: Record<QueryClass, Maker> = {
  "single-char": (r, ctx) => {
    // Weighted by first-character frequency: draw an item, use its first char.
    const c = pickItem(r, ctx).charCodeAt(0);
    const lc = isUpper(c) ? c + 32 : c;
    return isLower(lc) || isDigit(lc) ? String.fromCharCode(lc) : null;
  },
  "word-prefix": (r, ctx) => {
    const q = wordPrefix(r, ctx);
    return q !== null && q.length >= 2 ? q : null;
  },
  "multi-word-prefix": multiWordPrefix,
  acronym: (r, ctx) => {
    const hs: string[] = [];
    for (const w of asciiWords(pickItem(r, ctx))) for (const h of humps(w)) hs.push(h);
    const letters = hs.filter((h) => /^[A-Za-z]/.test(h));
    if (letters.length < 2) return null;
    const k = r.range(2, Math.min(4, letters.length));
    const start = r.chance(7, 10) ? 0 : r.below(letters.length - k + 1);
    return letters
      .slice(start, start + k)
      .map((h) => h[0])
      .join("")
      .toLowerCase();
  },
  "mid-word": (r, ctx) => {
    const ws = lowerWords(pickItem(r, ctx), 5);
    if (ws.length === 0) return null;
    const w = r.pick(ws);
    const start = r.range(1, w.length - 3);
    const len = r.range(3, Math.min(5, w.length - start));
    return w.slice(start, start + len);
  },
  transposition: (r, ctx) => {
    const ws = lowerWords(pickItem(r, ctx), 4).filter((w) => /^[a-z]+$/.test(w));
    if (ws.length === 0) return null;
    const w = r.pick(ws);
    const L = r.range(4, Math.min(w.length, 9));
    const p = r.range(1, L - 2);
    if (w[p] === w[p + 1]) return null;
    const pre = w.slice(0, L);
    return pre.slice(0, p) + pre[p + 1] + pre[p] + pre.slice(p + 2);
  },
  "doubled-letter": (r, ctx) => {
    const ws = lowerWords(pickItem(r, ctx), 3).filter((w) => /^[a-z]+$/.test(w));
    if (ws.length === 0) return null;
    const w = r.pick(ws);
    const L = r.range(3, Math.min(w.length, 8));
    const p = r.below(L);
    const pre = w.slice(0, L);
    return pre.slice(0, p + 1) + pre[p] + pre.slice(p + 1);
  },
  "case-variant": (r, ctx) => {
    const base = r.chance(1, 2) ? wordPrefix(r, ctx) : multiWordPrefix(r, ctx);
    if (base === null || base.length < 3) return null;
    const k = r.below(3);
    let q: string;
    if (k === 0) q = base.toUpperCase();
    else if (k === 1) q = base.split(" ").map((w) => (w ? (w[0] as string).toUpperCase() + w.slice(1) : w)).join(" ");
    else {
      q = "";
      for (let i = 0; i < base.length; i++) q += i % 2 === 1 ? (base[i] as string).toUpperCase() : base[i];
    }
    return q === base ? null : q;
  },
  "separator-variant": (r, ctx) => {
    const item = pickItem(r, ctx);
    const ws = lowerWords(item, 2);
    if (ws.length < 2) return null;
    const i = r.below(ws.length - 1);
    const a = ws[i] as string;
    const b = ws[i + 1] as string;
    const sep = r.pick(["-", "_", "/", ".", ":"]);
    if (r.chance(1, 4)) return `${a[0]}${sep}${b[0]}`; // "s/b"
    const q = a.slice(0, r.range(1, Math.min(a.length, 6))) + sep + b.slice(0, r.range(1, Math.min(b.length, 4)));
    return item.toLowerCase().includes(q) ? null : q; // must differ from the item's own separator
  },
  whitespace: (r, ctx) => {
    const w = wordPrefix(r, ctx);
    if (w === null) return null;
    const k = r.below(3);
    if (k === 0) return " " + w;
    if (k === 1) return w + " ";
    return " " + w + " ";
  },
  "non-latin": (r, ctx) => {
    if (ctx.nonLatinIdx.length === 0) return null;
    const item = ctx.items[r.pick(ctx.nonLatinIdx)] as string;
    const cl = approxClusters(item);
    const starts: number[] = [];
    for (let i = 0; i < cl.length; i++) if (!isLatinCp((cl[i] as number[])[0] as number)) starts.push(i);
    if (starts.length === 0) return null;
    const s = r.pick(starts);
    const first = cl[s] as number[];
    if (first.length > 1 && first[0] as number >= 0x1f000 && r.chance(1, 4)) {
      return fromCodePoints([first[0] as number]); // e.g. "👩" out of a ZWJ family
    }
    const k = r.range(1, 3);
    const out: number[] = [];
    for (let i = s; i < cl.length && i < s + k; i++) {
      const c = cl[i] as number[];
      if (isLatinCp(c[0] as number)) break;
      out.push(...c);
    }
    return fromCodePoints(out);
  },
  nfd: (r, ctx) => {
    if (ctx.nfdIdx.length === 0) return null;
    const item = ctx.items[r.pick(ctx.nfdIdx)] as string;
    const cl = approxClusters(item);
    const hits: number[] = [];
    for (let i = 0; i < cl.length; i++) if ((cl[i] as number[]).some(hasNfdDecomposition)) hits.push(i);
    if (hits.length === 0) return null;
    const h = r.pick(hits);
    const before = r.range(0, 2);
    const after = r.range(0, 2);
    const window = cl.slice(Math.max(0, h - before), h + after + 1).flat();
    while (window.length > 0 && window[0] === 0x20) window.shift();
    while (window.length > 0 && window[window.length - 1] === 0x20) window.pop();
    const src = fromCodePoints(window);
    const q = toNfd(src);
    if (q === src || !noMatchAgainstAll(q, ctx)) return null;
    return q;
  },
  "no-match": (r, ctx) => {
    const k = r.below(4);
    let q: string;
    if (k < 2) {
      const len = r.range(4, 7);
      q = "";
      for (let i = 0; i < len; i++) q += r.pick(ctx.rareChars);
    } else if (k === 2) {
      const w = wordPrefix(r, ctx);
      if (w === null) return null;
      q = w + r.pick(ctx.rareChars) + r.pick(ctx.rareChars) + r.pick(ctx.rareChars);
    } else {
      if (ctx.absentChars.length === 0) return null;
      const w = wordPrefix(r, ctx);
      if (w === null) return null;
      q = w + r.pick(ctx.absentChars);
    }
    return noMatchAgainstAll(q, ctx) ? q : null;
  },
  "ascii-20": (r, ctx) => {
    const s = pickItem(r, ctx).toLowerCase();
    if (s.length < 20) return null;
    const starts: number[] = [];
    for (let i = 0; i + 20 <= s.length; i++) {
      if (i > 0 && s[i - 1] !== " ") continue;
      const w = s.slice(i, i + 20);
      if (isTypeable(w) && w[0] !== " " && w[19] !== " ") starts.push(i);
    }
    if (starts.length === 0) return null;
    const i = r.pick(starts);
    return s.slice(i, i + 20);
  },
};

const ABSENT_CANDIDATES = ["~", "^", "`", "|", "\\", "<", ">", ";", "=", "%", "$", "!", "?", "*", "\""];

function buildCtx(items: readonly string[], checkItems: readonly string[]): Ctx {
  const checkLower = checkItems.map((s) => s.toLowerCase());
  const nonLatinIdx: number[] = [];
  const nfdIdx: number[] = [];
  const freq = new Map<string, number>();
  for (const ch of "abcdefghijklmnopqrstuvwxyz") freq.set(ch, 0);
  const present = new Set<string>();
  items.forEach((s, i) => {
    const cps = codePoints(s);
    if (cps.some((cp) => !isLatinCp(cp))) nonLatinIdx.push(i);
    if (cps.some(hasNfdDecomposition)) nfdIdx.push(i);
  });
  // Character statistics come from the check set, so "absent" means absent
  // from every nested size.
  checkLower.forEach((l) => {
    for (let k = 0; k < l.length; k++) {
      const ch = l[k] as string;
      const f = freq.get(ch);
      if (f !== undefined) freq.set(ch, f + 1);
      present.add(ch);
    }
  });
  // The 8 rarest ASCII letters in this dataset (ties by code unit).
  const rareChars = [...freq.entries()]
    .sort((a, b) => a[1] - b[1] || a[0].charCodeAt(0) - b[0].charCodeAt(0))
    .slice(0, 8)
    .map(([ch]) => ch);
  const absentChars = ABSENT_CANDIDATES.filter((c) => !present.has(c));
  return { items, checkLower, nonLatinIdx, nfdIdx, rareChars, absentChars };
}

const MAX_ATTEMPTS_PER_QUERY = 5000;

export interface QueryOptions {
  seed: bigint;
  count: number;
  /**
   * Items the no-match and NFD guarantees are checked against. Defaults to
   * `items`. For the shared set this is the largest nested size, while
   * `items` is the smallest prefix.
   */
  checkItems?: readonly string[];
}

export function generateQueries(items: readonly string[], opts: QueryOptions): Query[] {
  const ctx = buildCtx(items, opts.checkItems ?? items);
  const q = quotas(opts.count);
  const seen = new Set<string>();
  const out: Omit<Query, "qid">[] = [];
  QUERY_CLASSES.forEach((cls, idx) => {
    const r = stream(opts.seed, STREAM.queries + BigInt(idx + 1));
    const make = MAKERS[cls];
    // Whitespace-only queries come first in their class.
    const fixed: string[] = cls === "whitespace" ? [" ", "  "].slice(0, q[cls]) : [];
    for (const f of fixed) {
      seen.add(f);
      out.push({ q: f, class: cls, typeable: isTypeable(f) });
    }
    for (let n = fixed.length; n < q[cls]; n++) {
      let made: string | null = null;
      for (let a = 0; a < MAX_ATTEMPTS_PER_QUERY && made === null; a++) {
        const c = make(r, ctx);
        if (c === null || c.length === 0) continue;
        if (cls !== "single-char" && seen.has(c)) continue;
        made = c;
      }
      if (made === null) throw new Error(`query class ${cls}: could not make query ${n + 1} of ${q[cls]}`);
      seen.add(made);
      out.push({ q: made, class: cls, typeable: isTypeable(made) });
    }
  });
  const order = stream(opts.seed, STREAM.queries);
  order.shuffle(out);
  return out.map((x, qid) => ({ qid, ...x }));
}
