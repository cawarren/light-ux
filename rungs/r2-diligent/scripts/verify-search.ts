// Offline checks for R2's search (node scripts/verify-search.ts [--quick]); not part of the app.
// 1. The vendored scorer is upstream cmdk 1.1.1 byte-for-byte (SHA-256 of the marked region).
// 2. SearchIndex.search(q) equals a brute-force rank() built on cmdk's own published scorer
//    (npm cmdk@1.1.1 `defaultFilter`, i.e. dist/chunk-NZJY6EH4.mjs), for every shared dev-1 query,
//    on 10k and 50k, fresh (full scan with the P1 mask), while typing every prefix (P2 narrowing
//    from the cache) and while backspacing (cache hits), plus a random fuzz over a hostile alphabet
//    (transpositions, doubled letters, separators, Σ/ς, İ-free Unicode, astral units).
// Node's Math.pow differs from Chrome's on some exponents (parity/README.md), so this checks the
// index against the scorer in the SAME engine; bit-exactness against the Chromium reference is
// what the Playwright parity suite checks. Pruning only depends on score > 0, never on pow.
import { createHash } from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import { defaultFilter } from "cmdk"
import { SearchIndex } from "../src/search/search-index.ts"

const root = path.resolve(import.meta.dirname, "../../..")
const quick = process.argv.includes("--quick")
let failures = 0
const fail = (msg: string) => { failures++; console.error("FAIL", msg) }

// 1. Provenance.
{
  const src = fs.readFileSync(path.resolve(import.meta.dirname, "../src/search/command-score.ts"), "utf8")
  const begin = "// ---- BEGIN VERBATIM UPSTREAM ----\n"
  const end = "// ---- END VERBATIM UPSTREAM ----\n"
  const body = src.slice(src.indexOf(begin) + begin.length, src.indexOf(end))
  const sha = createHash("sha256").update(body).digest("hex")
  if (sha !== "ccfd0d66e3d31b8197fc4dbb217c9672e7562569775ec3a4f3b7e567eb81dfc7") fail(`vendored scorer hash ${sha}`)
  else console.log("vendored command-score.ts == upstream cmdk v1.1.1 (sha256 ok)")
}

function bruteRank(items: readonly string[], q: string): Int32Array {
  if (q === "") return Int32Array.from(items.keys())
  const scored: [number, number][] = []
  items.forEach((it, id) => { const s = defaultFilter(it, q, []); if (s > 0) scored.push([s, id]) })
  scored.sort((a, b) => b[0] - a[0] || a[1] - b[1])
  return Int32Array.from(scored, (x) => x[1])
}
function same(a: Int32Array, b: Int32Array) {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}
function check(ix: SearchIndex, items: readonly string[], q: string, label: string) {
  const got = ix.search(q)
  const want = bruteRank(items, q)
  if (got.query !== q || got.count !== want.length || !same(got.ids, want)) {
    fail(`${label} q=${JSON.stringify(q)}: got ${got.count} want ${want.length}`)
    return false
  }
  return true
}

// 2. Shared dev-1 queries.
const queries: { qid: number; q: string; typeable: boolean }[] =
  JSON.parse(fs.readFileSync(path.join(root, "dataset/out/dev-1/queries.json"), "utf8")).queries
for (const n of quick ? [10000] : [10000, 50000]) {
  const items: string[] = JSON.parse(fs.readFileSync(path.join(root, `dataset/out/dev-1/${n}/items.json`), "utf8")).items
  const t0 = performance.now()
  let checked = 0
  const qs = quick ? queries.filter((_, i) => i % 10 === 0) : queries
  // Fresh index per query: full scan (P1 only).
  for (const { q } of qs) { check(new SearchIndex(items), items, q, `${n} fresh`); checked++ }
  // Typing then backspacing on one long-lived index: P2 narrowing and cache hits.
  const typed = qs.filter((x) => x.typeable).slice(0, quick ? 10 : 60)
  const ix = new SearchIndex(items)
  for (const { q } of typed) {
    for (let k = 1; k <= q.length; k++) { check(ix, items, q.slice(0, k), `${n} typed`); checked++ }
    for (let k = q.length - 1; k >= 0; k--) { check(ix, items, q.slice(0, k), `${n} backspace`); checked++ }
  }
  console.log(`${n}: ${checked} searches checked in ${((performance.now() - t0) / 1000).toFixed(1)} s`)
}

// 3. Fuzz: random items and queries over a hostile alphabet; type each query key by key on one
// index (so P2 is exercised on every prefix) and compare with brute force.
{
  const alphabet = [..."abcop  -_/.Aaβσςσ", "Σ", "ΑΣ", "i", "I", " ", "\t", "😀".slice(0, 1), "😀", "ab", "ba", "pp", "ö", "Ö", "5", "59", "文", "件", "\u0947"]
  let seed = 12345
  const rnd = (k: number) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % k }
  const str = (len: number) => Array.from({ length: len }, () => alphabet[rnd(alphabet.length)]).join("")
  const items = Array.from({ length: 3000 }, () => str(1 + rnd(12)))
  const ix = new SearchIndex(items)
  let checked = 0
  for (let t = 0; t < (quick ? 100 : 600); t++) {
    const q = str(1 + rnd(5))
    for (let k = 1; k <= q.length; k++) { check(ix, items, q.slice(0, k), "fuzz"); checked++ }
  }
  console.log(`fuzz: ${checked} searches checked`)
}

if (failures) { console.error(`${failures} failures`); process.exit(1) }
console.log("all search checks passed")
