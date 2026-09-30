import { test } from "node:test";
import assert from "node:assert/strict";
import { generateStream, buildAll } from "../src/pipeline.ts";
import { generateQueries, quotas, QUERY_CLASSES, relaxedMatch, isSubsequence, type Query } from "../src/queries.ts";
import { Xoshiro128ss } from "../src/prng.ts";

const full = generateStream("dev-1", 100000);
const shared = buildAll("dev-1", { sizes: [1000, 10000, 50000, 100000] }).shared;
if (!shared) throw new Error("expected a shared query set");
interface QSet {
  name: string;
  draw: string[]; // items the queries were drawn from
  check: string[]; // items the guarantees were checked against
  qs: Query[];
}
const sets: QSet[] = [
  // Default: one shared set drawn from the 1k prefix, checked against 100k.
  { name: "shared", draw: full.gen.items.slice(0, 1000), check: full.gen.items, qs: shared.queries },
  // Per-size mode (flag): drawn from and checked against the same size.
  {
    name: "per-size-10k",
    draw: full.gen.items.slice(0, 10000),
    check: full.gen.items.slice(0, 10000),
    qs: generateQueries(full.gen.items.slice(0, 10000), { seed: full.seeds.queries, count: 1000 }),
  },
];

test("quotas sum to the count and follow the weights", () => {
  for (const n of [1, 7, 100, 999, 1000, 1234, 5000]) {
    const q = quotas(n);
    let sum = 0;
    for (const c of QUERY_CLASSES) sum += q[c];
    assert.equal(sum, n);
  }
  const q = quotas(1000);
  assert.equal(q["word-prefix"], 140);
  assert.equal(q["ascii-20"], 100);
  assert.equal(q.nfd, 30);
});

test("each query set meets class quotas exactly, with sequential qids", () => {
  for (const { name: n, qs } of sets) {
    const want = quotas(1000);
    for (const c of QUERY_CLASSES) assert.equal(qs.filter((q) => q.class === c).length, want[c], `${n} ${c}`);
    qs.forEach((q, i) => assert.equal(q.qid, i));
  }
});

test("typeable flag is exactly ASCII lower-case/digits/space", () => {
  for (const { qs } of sets) {
    for (const q of qs) assert.equal(q.typeable, /^[a-z0-9 ]+$/.test(q.q), JSON.stringify(q));
    for (const c of ["single-char", "word-prefix", "multi-word-prefix", "ascii-20", "whitespace", "acronym"]) {
      assert.ok(qs.filter((q) => q.class === c).every((q) => q.typeable), c);
    }
    for (const c of ["case-variant", "separator-variant", "non-latin", "nfd"]) {
      assert.ok(qs.filter((q) => q.class === c).every((q) => !q.typeable), c);
    }
  }
});

test("class invariants", () => {
  for (const { draw, check, qs } of sets) {
    const items = draw.map((s) => s.toLowerCase());
    const checkLower = check.map((s) => s.toLowerCase());
    const by = (c: string) => qs.filter((q) => q.class === c).map((q) => q.q);
    for (const q of by("single-char")) assert.equal(q.length, 1);
    for (const q of by("ascii-20")) assert.equal(q.length, 20);
    assert.ok(by("whitespace").includes(" "));
    assert.ok(by("whitespace").every((q) => q.startsWith(" ") || q.endsWith(" ")));
    assert.ok(by("case-variant").every((q) => /[A-Z]/.test(q)));
    assert.ok(by("separator-variant").every((q) => /[-_/.:]/.test(q)));
    assert.ok(by("non-latin").every((q) => /[^\x00-\x7f]/.test(q) && q === q.normalize("NFC")));
    assert.ok(by("nfd").every((q) => q !== q.normalize("NFC") && q === q.normalize("NFD")));
    for (const q of [...by("no-match"), ...by("nfd")]) {
      const ql = q.toLowerCase();
      assert.ok(checkLower.every((s) => !relaxedMatch(ql, s)), `matches: ${q}`);
    }
    // Queries drawn from the dataset: word prefixes and ascii-20 occur in some item.
    for (const q of [...by("word-prefix"), ...by("ascii-20"), ...by("mid-word")]) {
      assert.ok(items.some((s) => s.includes(q)), q);
    }
    // Unique except single-char (weighted draws repeat by design).
    const nonSingle = qs.filter((q) => q.class !== "single-char").map((q) => q.q);
    assert.equal(new Set(nonSingle).size, nonSingle.length);
  }
});

test("queries are deterministic", () => {
  const again = generateQueries(full.gen.items.slice(0, 1000), {
    seed: full.seeds.queries,
    count: 1000,
    checkItems: full.gen.items,
  });
  assert.deepEqual(again, shared.queries);
});

test("shared queries.json records the target sizes and hashes", () => {
  const j = JSON.parse(shared.queriesJson);
  assert.deepEqual(Object.keys(j), [
    "schema", "seedId", "datasetSha256", "datasetSha256BySize", "drawnFromSize", "checkedAgainstSize", "queries",
  ]);
  assert.deepEqual(Object.keys(j.datasetSha256BySize), ["1000", "10000", "50000", "100000"]);
  assert.equal(j.datasetSha256, j.datasetSha256BySize["100000"]);
  assert.equal(j.drawnFromSize, 1000);
  assert.equal(j.checkedAgainstSize, 100000);
});

test("relaxedMatch covers cmdk's transposition / duplicate cases and subsequences", () => {
  // Cases from docs/phase-0/05-software-foundations.md §0.5 (score > 0 in cmdk).
  assert.ok(relaxedMatch("opp", "op"));
  assert.ok(relaxedMatch("ba", "ab"));
  assert.ok(relaxedMatch("reqeust", "request"));
  assert.ok(!relaxedMatch("xyz", "open file"));
  assert.ok(!relaxedMatch("op~", "open file"));
  const r = Xoshiro128ss.fromSeed(9n);
  const alpha = "abcde";
  for (let i = 0; i < 5000; i++) {
    let q = "";
    let s = "";
    for (let k = r.range(1, 4); k > 0; k--) q += alpha[r.below(5)];
    for (let k = r.range(0, 8); k > 0; k--) s += alpha[r.below(5)];
    if (isSubsequence(q, s)) assert.ok(relaxedMatch(q, s), `${q} ${s}`);
  }
});
