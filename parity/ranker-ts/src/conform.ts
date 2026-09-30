// Conformance comparison (§2.2 levels). Reports carry only qids and pass/fail codes;
// divergence details are produced only on request (--verbose-local) and never include
// query text.
import { idsSha256, type GoldenLine, type GoldenError } from './golden.ts';
import type { Ranking } from './index.ts';

export type Level = 'strict' | 'tie-insensitive';

export interface Candidate { qid: string | number; ids: number[]; selected?: number | null }

export interface Verdict {
  qid: string | number;
  pass: boolean;
  /** True when the golden records an upstream error (query excluded from scoring; counted neither pass nor fail). */
  skipped?: boolean;
  /** Machine code, safe to print: ok | ok-hash | missing | golden-error-skipped | count | selected | order | tie-set | duplicate | needs-reference */
  code: string;
  /** Only filled when a full reference was supplied: first rank where candidate != reference. */
  firstDivergentRank?: number;
  expectedId?: number;
  actualId?: number;
}

/**
 * Compare one candidate list against a golden line.
 * `reference` (the full ranking from ranker-ts) is needed for tie-insensitive checks when the
 * hash differs, and for locating the first divergent rank; pass a thunk so it is computed lazily.
 */
export function conformQuery(
  golden: GoldenLine | GoldenError,
  cand: Candidate | undefined,
  level: Level,
  reference?: () => Ranking,
): Verdict {
  const qid = golden.qid;
  if ('error' in golden) return { qid, pass: false, skipped: true, code: 'golden-error-skipped' };
  if (!cand) return { qid, pass: false, code: 'missing' };
  const ids = cand.ids;
  const withDiv = (v: Verdict): Verdict => {
    if (!reference) return v;
    const ref = reference().ids;
    let k = 0;
    while (k < ids.length && k < ref.length && ids[k] === ref[k]) k++;
    return { ...v, firstDivergentRank: k, expectedId: ref[k], actualId: ids[k] };
  };
  if (ids.length !== golden.count) return withDiv({ qid, pass: false, code: 'count' });
  const hashOk = idsSha256(ids) === golden.idsSha256;
  const candSelected = cand.selected === undefined ? (ids.length ? ids[0] : null) : cand.selected;
  const firstOk = candSelected === (ids.length ? ids[0] : null);
  if (hashOk) {
    return firstOk ? { qid, pass: true, code: 'ok-hash' } : { qid, pass: false, code: 'selected' };
  }
  if (level === 'strict') return withDiv({ qid, pass: false, code: 'order' });

  // tie-insensitive: same length, same score at every rank, same id set within each tie group.
  if (!reference) return { qid, pass: false, code: 'needs-reference' };
  const ref = reference();
  if (!ref.scores) return withDiv({ qid, pass: false, code: 'order' }); // empty query: order is total
  if (new Set(ids).size !== ids.length) return withDiv({ qid, pass: false, code: 'duplicate' });
  let start = 0;
  for (const [, n] of golden.tieGroups) {
    const want = new Set(ref.ids.slice(start, start + n));
    for (let k = start; k < start + n; k++) {
      if (!want.has(ids[k])) return withDiv({ qid, pass: false, code: 'tie-set' });
    }
    start += n;
  }
  if (!firstOk) return { qid, pass: false, code: 'selected' };
  return { qid, pass: true, code: 'ok' };
}
