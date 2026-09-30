// R3 search core: an exact, faster transliteration of cmdk 1.1.1's commandScore
// (src/vendor/command-score.ts, verbatim) plus the normative ranking (05 §2.2).
//
// Differences from the vendored source are purely mechanical and do not change any value:
//   - the dataset is lowercased + whitespace-normalised ONCE (formatInput), not per call;
//   - the memo object keyed by `${si},${ai}` becomes a flat Float64Array with generation stamps
//     (the memoized function is pure, so the memo representation cannot change results);
//   - charAt(i) string compares become charCodeAt compares, with -1 standing for charAt's ''
//     (so '' === '' stays true, as in JS);
//   - regex tests on one code unit become a 64K lookup table built from the SAME regexes;
//   - slice(a, b).match(re).length becomes a counting loop over [a, b) (empty when b <= a);
//   - Math.pow(0.999, n) comes from `pow`, a table the caller fills with this engine's Math.pow
//     (in the pinned Chromium that is bit-identical to parity/tables/pow0999.json).
// Floating-point operations happen in the same order, on the same operands, as upstream.
//
// The fast path needs lowerString.length === string.length and
// lowerAbbreviation.length === abbreviation.length, because upstream indexes the original and the
// lowercased strings with the same index. When lowercasing changes the UTF-16 length (U+0130 İ is
// the only such code point in Chromium 141; the dataset bans it), the verbatim vendored function
// is used instead, so its exact behaviour (including a RangeError on the known crash inputs)
// is preserved.
import { commandScore } from './vendor/command-score.ts';

const COUNT_SPACE_REGEXP = /[\s-]/g;
/** upstream formatInput */
export function formatInput(s: string): string {
  return s.toLowerCase().replace(COUNT_SPACE_REGEXP, ' ');
}

// Code-unit classes, derived from upstream's own regexes (1 = gap, 2 = space/hyphen).
const GAP = 1, SPACE = 2;
const CLASS = new Uint8Array(65536);
{
  const isGap = /[\\\/_+.#"@\[\(\{&]/, isSpace = /[\s-]/;
  for (let c = 0; c < 65536; c++) {
    const ch = String.fromCharCode(c);
    CLASS[c] = isGap.test(ch) ? GAP : isSpace.test(ch) ? SPACE : 0;
  }
}

export const POW_N = 4097;
/** Math.pow(0.999, n) for n < POW_N, evaluated by THIS engine. */
export function enginePowTable(): Float64Array {
  const t = new Float64Array(POW_N);
  for (let n = 0; n < POW_N; n++) t[n] = Math.pow(0.999, n);
  return t;
}

export interface Scorer {
  /** commandScore(item, q, []) given item, formatInput(item), q, formatInput(q). */
  score(s: string, l: string, a: string, la: string): number;
}

export function createScorer(pow: Float64Array): Scorer {
  const P = (n: number) => (n < pow.length ? pow[n] : Math.pow(0.999, n));
  let S = '', L = '', A = '', LA = '';
  let sLen = 0, aLen = 0, W = 0;
  let memo = new Float64Array(4096);
  let stamp = new Uint32Array(4096);
  let gen = 0;
  let laCodes = new Int32Array(64);
  let laChars: string[] = [];

  function count(cls: number, from: number, to: number): number {
    let n = 0;
    for (let i = from; i < to; i++) if (CLASS[S.charCodeAt(i)] === cls) n++;
    return n;
  }

  function inner(si: number, ai: number): number {
    if (ai === aLen) return si === sLen ? 1 : 0.99;
    const key = si * W + ai;
    if (stamp[key] === gen) return memo[key];
    const ac = laCodes[ai];
    const an = laCodes[ai + 1]; // -1 past the end (charAt -> '')
    const ch = laChars[ai];
    const orig = A.charCodeAt(ai);
    let index = L.indexOf(ch, si);
    let high = 0;
    while (index >= 0) {
      let score = inner(index + 1, ai + 1);
      if (score > high) {
        if (index === si) {
          // score *= SCORE_CONTINUE_MATCH (1): exact no-op
        } else {
          const cls = CLASS[S.charCodeAt(index - 1)]; // index > si >= 0, in range
          if (cls === GAP) {
            score *= 0.8;
            if (si > 0) { const n = count(GAP, si, index - 1); if (n > 0) score *= P(n); }
          } else if (cls === SPACE) {
            score *= 0.9;
            if (si > 0) { const n = count(SPACE, si, index - 1); if (n > 0) score *= P(n); }
          } else {
            score *= 0.17;
            if (si > 0) score *= P(index - si);
          }
        }
        if (S.charCodeAt(index) !== orig) score *= 0.9999;
      }
      const lp = index > 0 ? L.charCodeAt(index - 1) : -1;
      if ((score < 0.1 && lp === an) || (an === ac && lp !== ac)) {
        // ai + 2 <= aLen whenever this is reachable with equal lengths (an === -1 needs lp === -1,
        // i.e. index === 0 === si, where score >= 0.99 * 0.9999 > 0.1). Guard anyway.
        if (ai + 2 > aLen) throw new FastPathError();
        const t = inner(index + 1, ai + 2);
        if (t * 0.1 > score) score = t * 0.1;
      }
      if (score > high) high = score;
      index = L.indexOf(ch, index + 1);
    }
    stamp[key] = gen;
    memo[key] = high;
    return high;
  }

  function setQuery(a: string, la: string) {
    if (a === A && la === LA) return;
    A = a; LA = la; aLen = a.length; W = aLen + 1;
    if (laCodes.length < aLen + 2) laCodes = new Int32Array(aLen + 2);
    laChars = new Array(aLen);
    for (let i = 0; i < aLen; i++) { laCodes[i] = la.charCodeAt(i); laChars[i] = la.charAt(i); }
    laCodes[aLen] = -1; laCodes[aLen + 1] = -1;
  }

  return {
    score(s, l, a, la) {
      if (l.length !== s.length || la.length !== a.length) return commandScore(s, a, []);
      setQuery(a, la);
      S = s; L = l; sLen = s.length;
      const need = (sLen + 2) * W;
      if (need > memo.length) {
        let n = memo.length; while (n < need) n *= 2;
        memo = new Float64Array(n); stamp = new Uint32Array(n); gen = 0;
      }
      gen = (gen + 1) >>> 0;
      if (gen === 0) { stamp.fill(0); gen = 1; }
      try {
        return inner(0, 0);
      } catch (e) {
        if (e instanceof FastPathError) return commandScore(s, a, []);
        throw e;
      }
    },
  };
}

class FastPathError extends Error {}

export interface Prepared { items: string[]; lower: string[] }
export function prepare(items: string[]): Prepared {
  const lower = new Array<string>(items.length);
  for (let i = 0; i < items.length; i++) lower[i] = formatInput(items[i]);
  return { items, lower };
}

/**
 * rank(items, q) (05 §2.2) over `candidates` (ascending ids; null = every item).
 * Returns the ids with score > 0, by score descending then id ascending. q is used as given.
 * Callers must only pass a candidate set that is a superset of {id : score(id, q) > 0}.
 */
export function rankIds(p: Prepared, scorer: Scorer, q: string, candidates: Int32Array | null): Int32Array {
  const n = candidates ? candidates.length : p.items.length;
  if (q === '') {
    const out = new Int32Array(n);
    for (let k = 0; k < n; k++) out[k] = candidates ? candidates[k] : k;
    return out;
  }
  const lq = formatInput(q);
  const ids = new Int32Array(n);
  const scores = new Float64Array(n);
  let h = 0;
  const { items, lower } = p;
  for (let k = 0; k < n; k++) {
    const id = candidates ? candidates[k] : k;
    const s = scorer.score(items[id], lower[id], q, lq);
    if (s > 0) { ids[h] = id; scores[h] = s; h++; }
  }
  return sortHits(ids, scores, h);
}

/**
 * Hits arrive in ascending id order. Output: score descending, then id ascending.
 * Scores have few distinct values (05 §0.1: `op` at 50k has 34), so a stable counting sort over
 * the distinct values is O(h + d log d). Map keys use SameValueZero, i.e. exact binary64 equality
 * for positive finite scores, which is the normative tie rule. Falls back to a comparator sort
 * when there are many distinct scores.
 */
function sortHits(ids: Int32Array, scores: Float64Array, h: number): Int32Array {
  const out = new Int32Array(h);
  const bucket = new Map<number, number>();
  const limit = Math.max(64, h >>> 3);
  for (let k = 0; k < h; k++) {
    const s = scores[k];
    const c = bucket.get(s);
    bucket.set(s, c === undefined ? 1 : c + 1);
    if (bucket.size > limit) break;
  }
  if (bucket.size <= limit) {
    const distinct = Float64Array.from(bucket.keys()).sort(); // ascending numeric
    let at = 0;
    for (let d = distinct.length - 1; d >= 0; d--) {
      const s = distinct[d];
      const c = bucket.get(s)!;
      bucket.set(s, at);
      at += c;
    }
    for (let k = 0; k < h; k++) {
      const s = scores[k];
      const pos = bucket.get(s)!;
      out[pos] = ids[k];
      bucket.set(s, pos + 1);
    }
    return out;
  }
  const order = new Uint32Array(h);
  for (let k = 0; k < h; k++) order[k] = k;
  order.sort((x, y) => {
    const sx = scores[x], sy = scores[y];
    return sx > sy ? -1 : sx < sy ? 1 : x - y;
  });
  for (let k = 0; k < h; k++) out[k] = ids[order[k]];
  return out;
}

/**
 * May the result set of `p` be used as the candidate set for `q`? True when every item with
 * score(item, q) > 0 also has score(item, p) > 0. Proof sketch (README "Incremental narrowing"):
 * positivity depends only on the lowercased strings; if formatInput(q) extends formatInput(p)
 * (and neither lowercasing changes the UTF-16 length), any positive match path for q either
 * matches query unit n-1 (n = p.length) or skips it with the transposition branch from n-2
 * (whose condition only reads units <= n-1, identical in p); cut there, it is a positive path
 * for p. `q.startsWith(p)` alone is NOT enough: final sigma makes "ΣΣ" lowercase to "σς" but
 * "ΣΣa" to "σσa" (a counterexample the fuzz test found).
 */
export function canNarrow(p: string, q: string): boolean {
  if (p === '' || q.length <= p.length || !q.startsWith(p)) return false;
  const lp = formatInput(p), lq = formatInput(q);
  return lp.length === p.length && lq.length === q.length && lq.startsWith(lp);
}
