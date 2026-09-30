import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { generateItems, NON_LATIN_BLOCK } from "../src/items.ts";
import { generateStream, buildAll, goldenHashes, GOLDEN_SIZES, type SizeOutput } from "../src/pipeline.ts";
import { sha256, itemsJson, manifestJson } from "../src/output.ts";
import { validateItem, isLatinItem, cpLength, MIN_LEN, MAX_LEN } from "../src/unicode.ts";
import { PKG_DIR, loadCorpus } from "../src/data.ts";
import { DEV_SEEDS } from "../src/seeds.ts";

const DEV = DEV_SEEDS["dev-1"] as bigint;
const full = generateStream("dev-1", 100000);

test("generating twice gives identical bytes (items, queries, manifests)", () => {
  const a = buildAll("dev-1", { sizes: [1000, 10000] });
  const b = buildAll("dev-1", { sizes: [1000, 10000] });
  for (let i = 0; i < 2; i++) {
    const x = a.sizes[i] as SizeOutput;
    const y = b.sizes[i] as SizeOutput;
    assert.equal(x.itemsJson, y.itemsJson);
    assert.equal(manifestJson(x.manifest), manifestJson(y.manifest));
  }
  assert.equal(a.shared?.queriesJson, b.shared?.queriesJson);
  assert.equal(manifestJson(a.shared?.manifest ?? {}), manifestJson(b.shared?.manifest ?? {}));
});

test("manifest.json is byte-stable: no git SHA, Node version or time", () => {
  const b = buildAll("dev-1", { sizes: [1000] });
  const m = manifestJson((b.sizes[0] as SizeOutput).manifest) + manifestJson(b.shared?.manifest ?? {});
  assert.ok(!m.includes(process.version));
  assert.ok(!/gitSha|gitDirty|"node"|generatedAt/.test(m));
});

test("dev-1 outputs match the committed golden hashes", () => {
  const golden = JSON.parse(readFileSync(join(PKG_DIR, "golden", "dev-1.json"), "utf8")) as {
    sha256: unknown;
  };
  assert.deepEqual(goldenHashes(), golden.sha256);
});

test("sizes are nested prefixes of one stream", () => {
  const small = generateItems({ seed: DEV, size: 1000 });
  assert.deepEqual(small.items, full.gen.items.slice(0, 1000));
  const mid = generateItems({ seed: DEV, size: 10007 });
  assert.deepEqual(mid.items, full.gen.items.slice(0, 10007));
});

test("every item validates and is 8-80 code points", () => {
  for (const s of full.gen.items) {
    assert.equal(validateItem(s), null, s);
    const n = cpLength(s);
    assert.ok(n >= MIN_LEN && n <= MAX_LEN);
  }
});

test("items are unique by exact string and by toLowerCase()", () => {
  const exact = new Set(full.gen.items);
  const lower = new Set(full.gen.items.map((s) => s.toLowerCase()));
  assert.equal(exact.size, full.gen.items.length);
  assert.equal(lower.size, full.gen.items.length);
});

test("non-Latin share is 5.0% +/- 0.2% at every sweep prefix", () => {
  for (const size of [...GOLDEN_SIZES, 20, 60, 999, 1001, 12345]) {
    let nl = 0;
    for (let i = 0; i < size; i++) if (!isLatinItem(full.gen.items[i] as string)) nl++;
    const pctX1000 = Math.round((nl * 100000) / size);
    if (size % NON_LATIN_BLOCK === 0) assert.equal(nl * NON_LATIN_BLOCK, size, `size ${size}`);
    if (size >= 1000) assert.ok(pctX1000 >= 4800 && pctX1000 <= 5200, `size ${size}: ${nl}`);
  }
  // tags agree with the content classifier
  full.gen.items.forEach((s, i) => assert.equal(!isLatinItem(s), (full.gen.tags[i] as { nonLatin: boolean }).nonLatin));
});

test("every non-Latin category appears in the 1k prefix; scripts covered at 10k", () => {
  const cats = new Set(full.gen.tags.slice(0, 1000).filter((t) => t.nonLatin).map((t) => t.kind));
  for (const c of ["ja", "ko", "ar", "he", "hi", "emoji", "zh-Hans", "zh-Hant"]) assert.ok(cats.has(c as never), c);
  const text = full.gen.items.slice(0, 10000).join("\n");
  assert.ok(text.includes("‍"), "ZWJ sequence");
  assert.ok(/[\u{1F3FB}-\u{1F3FF}]/u.test(text), "skin tone");
  assert.ok(text.includes("️⃣"), "keycap");
  assert.ok(/[\u{1F1E6}-\u{1F1FF}]{2}/u.test(text), "flag");
  assert.ok(/[٠-٩]/.test(text), "Arabic-Indic digits");
  assert.ok(text.includes("لا") || text.includes("لأ") || text.includes("لإ"), "lam-alef");
  assert.ok(text.includes("्"), "Devanagari virama (conjunct)");
  assert.ok(text.includes("़"), "Devanagari nukta");
});

test("output is LF-only UTF-8 JSON with the documented shape", () => {
  const b = buildAll("dev-1", { sizes: [1000], queryCount: 0 }).sizes[0] as SizeOutput;
  assert.ok(!b.itemsJson.includes("\r"));
  const parsed = JSON.parse(b.itemsJson);
  assert.deepEqual(Object.keys(parsed), ["schema", "seedId", "count", "items"]);
  assert.equal(parsed.schema, 1);
  assert.equal(parsed.seedId, "dev-1");
  assert.equal(parsed.count, 1000);
  assert.equal(sha256(itemsJson("dev-1", parsed.items)), sha256(b.itemsJson));
  assert.equal((b.manifest.scriptMix as { nonLatinPercent: string }).nonLatinPercent, "5.000");
});

test("shifted variant differs, stays valid, and keeps the 5% mix", () => {
  const s = generateStream("dev-1-shifted", 5000);
  assert.equal(s.variant, "shifted");
  const overlap = s.gen.items.filter((x) => new Set(full.gen.items.slice(0, 5000)).has(x)).length;
  assert.ok(overlap < 250, `overlap ${overlap}`);
  for (const x of s.gen.items) assert.equal(validateItem(x), null);
  assert.equal(s.gen.tags.filter((t) => t.nonLatin).length, 250);
  const paths = s.gen.tags.filter((t) => t.kind === "path").length;
  const devPaths = full.gen.tags.slice(0, 5000).filter((t) => t.kind === "path").length;
  assert.ok(paths > devPaths * 1.3, `paths ${paths} vs ${devPaths}`);
});

test("denylisted EFF words never appear as words in items", () => {
  const deny = loadCorpus().denylist;
  assert.ok(deny.size > 0);
  for (const s of full.gen.items) {
    for (const w of s.toLowerCase().split(/[^a-z]+/)) assert.ok(!deny.has(w), `${w} in ${s}`);
  }
});

test("non-Latin positions are random within each block of 20 (no fixed stride)", () => {
  const positions = new Set<number>();
  for (let b = 0; b < 500; b++) {
    const block = full.gen.tags.slice(b * NON_LATIN_BLOCK, (b + 1) * NON_LATIN_BLOCK);
    const idx = block.findIndex((t) => t.nonLatin);
    assert.equal(block.filter((t) => t.nonLatin).length, 1);
    positions.add(idx);
  }
  assert.equal(positions.size, NON_LATIN_BLOCK); // every offset 0..19 occurs
});
