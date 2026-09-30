import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveSeeds, loadSecret, hmacSeed, isPublicSeedId } from "../src/seeds.ts";
import { buildAll, type SizeOutput } from "../src/pipeline.ts";
import { manifestJson } from "../src/output.ts";

// A throwaway test secret. Real held-out secrets are never committed.
const SECRET = Buffer.from("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff", "hex");

test("held-out seeds are HMAC-SHA256(secret, '<seedId>/items') first 8 bytes BE", () => {
  const s = resolveSeeds("heldout-v1", SECRET);
  const mac = createHmac("sha256", SECRET).update("heldout-v1/items").digest();
  assert.equal(s.items, mac.readBigUInt64BE(0));
  assert.equal(s.queries, hmacSeed(SECRET, "heldout-v1/queries"));
  assert.notEqual(s.items, s.queries);
  assert.equal(s.source, "hmac-sha256-secret");
  assert.equal(resolveSeeds("heldout-v1-shifted", SECRET).shifted, true);
  assert.notEqual(resolveSeeds("heldout-v1-shifted", SECRET).items, s.items);
});

test("held-out ids require a secret; dev ids are public", () => {
  assert.throws(() => resolveSeeds("heldout-v1", undefined), /secret/);
  assert.equal(isPublicSeedId("dev-1"), true);
  assert.equal(isPublicSeedId("dev-1-shifted"), true);
  assert.equal(isPublicSeedId("heldout-v1"), false);
});

test("secret loads from a hex file and must be >= 256 bits", () => {
  const dir = mkdtempSync(join(tmpdir(), "ll-secret-"));
  const f = join(dir, "s.hex");
  writeFileSync(f, SECRET.toString("hex") + "\n");
  assert.deepEqual(loadSecret(f), SECRET);
  writeFileSync(f, "abcd\n");
  assert.throws(() => loadSecret(f), /256 bits/);
});

test("held-out outputs never contain the secret or seed", () => {
  const b = buildAll("heldout-v1", { sizes: [1000], queryCount: 100, secret: SECRET });
  const o = b.sizes[0] as SizeOutput;
  const all = o.itemsJson + b.shared?.queriesJson + manifestJson(o.manifest) + manifestJson(b.shared?.manifest ?? {});
  assert.ok(!all.includes(SECRET.toString("hex")));
  assert.ok(!all.includes(String(b.seeds.items)));
  assert.equal(o.manifest.seedSource, "hmac-sha256-secret");
});
