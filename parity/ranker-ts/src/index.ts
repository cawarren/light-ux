// Latency Ladder reference ranker (normative text: docs/phase-0/05-software-foundations.md §2.2).
import { commandScore } from './vendor/command-score.ts';
import { POW_0999, POW_MAX, LOWERCASE_TABLE, TABLE_ENGINE } from './tables.ts';

export { POW_0999, POW_MAX, LOWERCASE_TABLE, TABLE_ENGINE, commandScore };

/** Thrown when an input is longer than the frozen POW table supports. */
export class InputTooLongError extends Error {}

/**
 * cmdkScore_1_1_1(item, q, []) with Math.pow(0.999, n) replaced by POW_0999[n].
 * Queries are used as given (no trim, no normalization).
 * Note: for a few pathological queries (e.g. "İ̇" against an item containing U+0307
 * not preceded by U+0307), upstream cmdk recurses without bound and throws RangeError;
 * this function throws the same RangeError. See README "Known upstream crash".
 */
export function score(item: string, q: string): number {
  if (item.length * 2 > POW_MAX && item.toLowerCase().length > POW_MAX) {
    throw new InputTooLongError(`item longer than POW table (${POW_MAX} UTF-16 units after lowercasing)`);
  }
  return commandScore(item, q, []);
}

export interface Ranking {
  /** Matching ids (dataset indices) in normative order. */
  ids: number[];
  /** Scores parallel to ids; null for the empty query (no scoring happens). */
  scores: number[] | null;
  /** ids[0], or null when there are no results. */
  selected: number | null;
}

/** §2.2: empty q -> all ids in dataset order; else ids with score > 0 by score desc, id asc. */
export function rank(items: readonly string[], q: string): Ranking {
  if (q === '') {
    const ids = items.map((_, i) => i);
    return { ids, scores: null, selected: ids.length ? 0 : null };
  }
  const hits: { id: number; s: number }[] = [];
  for (let i = 0; i < items.length; i++) {
    const s = score(items[i], q);
    if (s > 0) hits.push({ id: i, s });
  }
  // Exact binary64 comparison; ties (===) broken by id ascending.
  hits.sort((a, b) => (a.s > b.s ? -1 : a.s < b.s ? 1 : a.id - b.id));
  const ids = hits.map((h) => h.id);
  return { ids, scores: hits.map((h) => h.s), selected: ids.length ? ids[0] : null };
}

const dv = new DataView(new ArrayBuffer(8));
/** IEEE-754 binary64 bit pattern as 16 lowercase hex digits (big-endian). */
export function bitsHex(x: number): string {
  dv.setFloat64(0, x);
  return dv.getBigUint64(0).toString(16).padStart(16, '0');
}
export function fromBitsHex(h: string): number {
  dv.setBigUint64(0, BigInt('0x' + h));
  return dv.getFloat64(0);
}

/** Runs of equal scores in a ranking (scores sorted descending): [[scoreBitsHex, count], ...]. */
export function tieGroups(scores: readonly number[]): [string, number][] {
  const out: [string, number][] = [];
  for (let i = 0; i < scores.length; ) {
    let j = i + 1;
    while (j < scores.length && scores[j] === scores[i]) j++;
    out.push([bitsHex(scores[i]), j - i]);
    i = j;
  }
  return out;
}

/**
 * Check that this engine's String.prototype.toLowerCase agrees with the frozen Chromium
 * table (context-free map + final-sigma classes). ranker-ts uses the engine's native
 * toLowerCase (the vendored source is unmodified there), so a disagreeing engine gives
 * non-reference scores for strings containing the listed code points.
 * Returns the code points that disagree.
 */
export function engineLowercaseMismatches(): number[] {
  const bad: number[] = [];
  const mapped = new Map(LOWERCASE_TABLE.map.map(([c, u]) => [c, String.fromCharCode(...u)]));
  const seen = new Set<number>();
  for (const [c, want] of mapped) {
    seen.add(c);
    if (String.fromCodePoint(c).toLowerCase() !== want) bad.push(c);
  }
  for (let c = 0; c <= 0x10ffff; c++) {
    if (seen.has(c) || (c >= 0xd800 && c <= 0xdfff)) continue;
    const s = String.fromCodePoint(c);
    if (s.toLowerCase() !== s) bad.push(c);
  }
  return bad;
}
