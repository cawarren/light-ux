// Golden files (§2.3): one JSONL line per query.
//   {"qid","count","idsSha256","selected","top":[[id,scoreBitsHex|null]×≤100],"tieGroups":[[scoreBitsHex,count],...]}
// idsSha256 = SHA-256 (lowercase hex) of the UTF-8 string ids.join(",") (decimal ids, no spaces;
// the empty list hashes the empty string). For the empty query nothing is scored: the top
// entries carry null scores and tieGroups is []. A query on which upstream cmdk throws
// (see README) is recorded as {"qid","error":"RangeError"}.
import { createHash } from 'node:crypto';
import { rank, bitsHex, tieGroups, type Ranking } from './index.ts';

export const GOLDEN_TOP = 100;

export interface GoldenLine {
  qid: string | number;
  count: number;
  idsSha256: string;
  selected: number | null;
  top: [number, string | null][];
  tieGroups: [string, number][];
}
export interface GoldenError { qid: string | number; error: string }

export function idsSha256(ids: readonly number[]): string {
  return createHash('sha256').update(ids.join(','), 'utf8').digest('hex');
}

export function goldenFromRanking(qid: string | number, r: Ranking): GoldenLine {
  return {
    qid,
    count: r.ids.length,
    idsSha256: idsSha256(r.ids),
    selected: r.selected,
    top: r.ids.slice(0, GOLDEN_TOP).map((id, k) => [id, r.scores ? bitsHex(r.scores[k]) : null]),
    tieGroups: r.scores ? tieGroups(r.scores) : [],
  };
}

export function goldenLine(items: readonly string[], qid: string | number, q: string): GoldenLine | GoldenError {
  try {
    return goldenFromRanking(qid, rank(items, q));
  } catch (e) {
    if (e instanceof RangeError) return { qid, error: 'RangeError' };
    throw e;
  }
}
