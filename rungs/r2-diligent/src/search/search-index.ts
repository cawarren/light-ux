// R2 search: the normative rank(items, q) (docs/phase-0/05 §2.2, parity/README.md) computed with
// cmdk 1.1.1's own scorer, plus a precomputed index built once after the dataset loads
// (ALLOWLIST.md A7) and a memo cache of past queries (A2). Everything here is exact: the index only
// skips items that provably score 0, and the cache returns what the same pure function returned.
//
//   rank(items, q): q === "" -> all ids in dataset order. Otherwise ids with
//   commandScore(item, q, []) > 0, by score descending, then id ascending. No trim.
//
// Pruning, and why it is exact (see README "Exactness of the index" for the full argument):
//   P1 (character mask). In commandScoreInner every query code unit P[f], f < q.length, is either
//      matched at some lowerItem[c] === P[f], or skipped by the transposition branch, which needs
//      lowerItem[c-1] === P[f+1] or P[f+1] === P[f] (a matched unit). So a positive score implies
//      every code unit of the formatted query occurs in the formatted item. The index stores, per
//      item, a 64-bit bucket mask of its units; an item is scored only if its mask covers the
//      query's. Buckets may collide, which only lets extra items through (they are then scored).
//      Only used when formatInput(q).length === q.length (the recursion stops at q.length).
//   P2 (prefix narrowing). If formatInput(p) is a prefix of formatInput(q), both with unchanged
//      length, then score(item, q) > 0 implies score(item, p) > 0 (truncate the match path of q at
//      p.length). So when p's results are cached, only they are scored for q. This is what makes
//      typing forward cheap. The length/prefix check matters: Final_Sigma makes "ΑΣ" -> "ας" but
//      "ΑΣΑ" -> "ασα", so the original-string prefix alone is not enough.
// Neither rule assumes the query is a subsequence of the item: transposed and doubled-letter
// matches (05 §0.5, e.g. score("ba", "ab") = 0.1) keep every unit of the query in the item.
import { commandScoreInner, formatInput } from "./command-score.ts"

export interface Results {
  /** The query these results are for (as typed, untrimmed). */
  readonly query: string
  /** Matching ids, ranked: score descending, then id ascending. */
  readonly ids: Int32Array
  /** The same ids in ascending id order (the candidate list for P2 narrowing). */
  readonly asc: Int32Array
  readonly count: number
}

const CACHE_ENTRIES = 32

/** 64 buckets over UTF-16 code units: a-z, 0-9 and space get their own bit; the rest share 27. */
function bucket(u: number): number {
  if (u >= 97 && u <= 122) return u - 97 // a-z: 0..25
  if (u >= 48 && u <= 57) return 26 + (u - 48) // 0-9: 26..35
  if (u === 32) return 36 // space (formatInput maps every \s and "-" to it)
  return 37 + (u % 27) // everything else: 37..63
}

function maskOf(s: string): [number, number] {
  let lo = 0
  let hi = 0
  for (let i = 0; i < s.length; i++) {
    const b = bucket(s.charCodeAt(i))
    if (b < 32) lo |= 1 << b
    else hi |= 1 << (b - 32)
  }
  return [lo, hi] // int32 bit patterns; compare only with bitwise ops
}

export class SearchIndex {
  readonly items: readonly string[]
  /** formatInput(item): what commandScore() computes from each item on every call. */
  private readonly lower: string[]
  private readonly maskLo: Uint32Array
  private readonly maskHi: Uint32Array
  /** Empty query: every item in dataset order. */
  readonly all: Results
  /** LRU memo of search(q), most recent last. */
  private readonly cache = new Map<string, Results>()

  constructor(items: readonly string[]) {
    const n = items.length
    this.items = items
    this.lower = new Array<string>(n)
    this.maskLo = new Uint32Array(n)
    this.maskHi = new Uint32Array(n)
    for (let i = 0; i < n; i++) {
      const l = formatInput(items[i]) as string
      this.lower[i] = l
      const [lo, hi] = maskOf(l)
      this.maskLo[i] = lo
      this.maskHi[i] = hi
    }
    const ids = new Int32Array(n)
    for (let i = 0; i < n; i++) ids[i] = i
    this.all = { query: "", ids, asc: ids, count: n }
  }

  search(q: string): Results {
    if (q === "") return this.all
    const hit = this.cache.get(q)
    if (hit) {
      this.cache.delete(q)
      this.cache.set(q, hit)
      return hit
    }
    const r = this.compute(q)
    this.cache.set(q, r)
    if (this.cache.size > CACHE_ENTRIES) this.cache.delete(this.cache.keys().next().value!)
    return r
  }

  private compute(q: string): Results {
    const lowerQ = formatInput(q) as string
    const sameLength = lowerQ.length === q.length

    // P2: the longest cached prefix whose formatted form is a prefix of ours.
    let candidates: Int32Array | null = null
    if (sameLength) {
      for (let k = q.length - 1; k >= 1 && !candidates; k--) {
        const p = q.slice(0, k)
        const prev = this.cache.get(p)
        if (!prev) continue
        const lowerP = formatInput(p) as string
        if (lowerP.length === p.length && lowerQ.startsWith(lowerP)) candidates = prev.asc
      }
    }
    // P1: required bucket bits (none when the formatted length differs: then score everything).
    const [qLo, qHi] = sameLength ? maskOf(lowerQ) : [0, 0]

    const { items, lower, maskLo, maskHi } = this
    const n = candidates ? candidates.length : items.length
    const matchIds: number[] = []
    const matchScores: number[] = []
    for (let k = 0; k < n; k++) {
      const i = candidates ? candidates[k] : k
      if (((qLo & ~maskLo[i]) | (qHi & ~maskHi[i])) !== 0) continue // a required unit is missing
      // Exactly commandScore(items[i], q, []): the same call with formatInput() hoisted out.
      const s = commandScoreInner(items[i], q, lower[i], lowerQ, 0, 0, {}) as number
      if (s > 0) {
        matchIds.push(i)
        matchScores.push(s)
      }
    }
    // Candidates are visited in ascending id order, so a stable sort by score gives id ascending
    // within equal scores. Skip the sort when every score is the same (e.g. most 1-char queries).
    const m = matchIds.length
    const asc = Int32Array.from(matchIds)
    let ids = asc
    let uniform = true
    for (let k = 1; k < m && uniform; k++) uniform = matchScores[k] === matchScores[0]
    if (!uniform) {
      const order = new Array<number>(m)
      for (let k = 0; k < m; k++) order[k] = k
      order.sort((a, b) => matchScores[b] - matchScores[a] || a - b)
      ids = new Int32Array(m)
      for (let k = 0; k < m; k++) ids[k] = matchIds[order[k]]
    }
    return { query: q, ids, asc, count: m }
  }
}
