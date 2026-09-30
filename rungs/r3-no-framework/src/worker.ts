// R3 search worker. Holds the dataset (fetched and lowercased once) and answers one query at a
// time with the full ranked id list as a transferred Int32Array.
//
// Protocol (main -> worker):  {type:'init', url}  then  {type:'query', seq, q}
//           (worker -> main):  {type:'ready', count, prepMs}
//                              {type:'result', seq, q, ids: Int32Array (transferred), ms, how}
// The main thread keeps at most ONE query in flight (see main.ts, "Supersession"), so this worker
// never has a backlog to skip; it simply answers what it is asked, in order.
//
// Exactness shortcuts (both provably exact, both tested in test/scorer.test.ts):
//   - exact cache hit: rank(items, q) is a pure function of q, so a cached answer is the answer
//     (typing back over a query, e.g. every backspace, costs a copy);
//   - narrowing: when canNarrow(p, q) holds for a cached p, only p's hits can match q, so only they
//     are rescored (every hit is rescored against q; scores and order are recomputed from scratch).
import { canNarrow, createScorer, enginePowTable, prepare, rankIds, type Prepared } from './scorer.ts';

declare const self: DedicatedWorkerGlobalScope;

let data: Prepared | null = null;
const scorer = createScorer(enginePowTable());

// LRU of recent answers (Map keeps insertion order; re-set on use). Bounded by entries and ids.
const CACHE_ENTRIES = 64;
const CACHE_IDS = 4_000_000; // 16 MB of Int32
const cache = new Map<string, Int32Array>();
let cachedIds = 0;
function remember(q: string, ids: Int32Array) {
  const old = cache.get(q);
  if (old) { cache.delete(q); cachedIds -= old.length; }
  cache.set(q, ids);
  cachedIds += ids.length;
  for (const [k, v] of cache) {
    if (cache.size <= CACHE_ENTRIES && cachedIds <= CACHE_IDS) break;
    cache.delete(k);
    cachedIds -= v.length;
  }
}

function answer(q: string): { ids: Int32Array; how: string } {
  const d = data!;
  const hit = cache.get(q);
  if (hit) { remember(q, hit); return { ids: hit, how: 'cache' }; }
  if (q === '') {
    // Not normally asked (the main thread shows the empty query itself), but keep it total.
    const ids = rankIds(d, scorer, '', null);
    remember(q, ids);
    return { ids, how: 'all' };
  }
  // Smallest cached candidate set we may narrow from.
  let base: Int32Array | null = null;
  for (const [p, ids] of cache) {
    if ((base === null || ids.length < base.length) && canNarrow(p, q)) base = ids;
  }
  const cand = base ? base.slice().sort() : null; // ascending ids
  const ids = rankIds(d, scorer, q, cand);
  remember(q, ids);
  return { ids, how: base ? `narrow ${base.length}` : 'full' };
}

self.onmessage = async (e: MessageEvent) => {
  const m = e.data;
  if (m.type === 'init') {
    const t0 = performance.now();
    const res = await fetch(m.url);
    const json = (await res.json()) as { items: string[] };
    data = prepare(json.items);
    self.postMessage({ type: 'ready', count: json.items.length, prepMs: performance.now() - t0 });
    return;
  }
  if (m.type === 'query') {
    const t0 = performance.now();
    const { ids, how } = answer(m.q);
    const out = ids.slice(); // the cache keeps its copy; this one is transferred
    self.postMessage({ type: 'result', seq: m.seq, q: m.q, ids: out, ms: performance.now() - t0, how }, [out.buffer]);
  }
};
