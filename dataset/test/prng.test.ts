import { test } from "node:test";
import assert from "node:assert/strict";
import { SplitMix64, Xoshiro128ss, stream, STREAM } from "../src/prng.ts";

// Reference vectors from rust-random/rngs, rand_xoshiro/src/xoshiro128starstar.rs,
// test `reference`: "These values were produced with the reference
// implementation (v1.1): http://xoshiro.di.unimi.it/xoshiro128starstar.c".
// State bytes [1,0,0,0, 2,0,0,0, 3,0,0,0, 4,0,0,0] = words [1, 2, 3, 4].
test("xoshiro128** matches the reference C test vectors", () => {
  const r = new Xoshiro128ss(1, 2, 3, 4);
  const expected = [
    11520, 0, 5927040, 70819200, 2031721883, 1637235492, 1287239034, 3734860849, 3729100597, 4258142804,
  ];
  for (const e of expected) assert.equal(r.nextU32(), e);
});

// Reference vectors from rust-random/rngs, rand_xoshiro/src/splitmix64.rs,
// test `reference`: seed 1477776061723855037, "produced with the reference
// implementation: http://xoshiro.di.unimi.it/splitmix64.c" (first 10 of 50).
test("SplitMix64 matches the reference C test vectors", () => {
  const s = new SplitMix64(1477776061723855037n);
  const expected = [
    1985237415132408290n, 2979275885539914483n, 13511426838097143398n, 8488337342461049707n,
    15141737807933549159n, 17093170987380407015n, 16389528042912955399n, 13177319091862933652n,
    10841969400225389492n, 17094824097954834098n,
  ];
  for (const e of expected) assert.equal(s.next(), e);
});

test("fromSeed splits two SplitMix64 outputs into lo/hi words", () => {
  const sm = new SplitMix64(42n);
  const a = sm.next();
  const b = sm.next();
  const x = new Xoshiro128ss(Number(a & 0xffffffffn), Number(a >> 32n), Number(b & 0xffffffffn), Number(b >> 32n));
  const y = Xoshiro128ss.fromSeed(42n);
  for (let i = 0; i < 100; i++) assert.equal(y.nextU32(), x.nextU32());
});

test("below() stays in range, hits every value, and handles 2^32", () => {
  const r = Xoshiro128ss.fromSeed(7n);
  for (const n of [1, 2, 3, 7, 10, 1000, 2 ** 31 + 1, 2 ** 32]) {
    for (let i = 0; i < 2000; i++) {
      const v = r.below(n);
      assert.ok(Number.isInteger(v) && v >= 0 && v < n);
    }
  }
  const seen = new Set<number>();
  for (let i = 0; i < 1000; i++) seen.add(r.below(7));
  assert.equal(seen.size, 7);
  assert.throws(() => r.below(0));
  assert.throws(() => r.below(1.5));
});

test("streams are independent and reproducible", () => {
  const a1 = stream(1n, STREAM.latin);
  const a2 = stream(1n, STREAM.latin);
  const b = stream(1n, STREAM.nonLatin);
  const xs = Array.from({ length: 16 }, () => a1.nextU32());
  const ys = Array.from({ length: 16 }, () => a2.nextU32());
  const zs = Array.from({ length: 16 }, () => b.nextU32());
  assert.deepEqual(xs, ys);
  assert.notDeepEqual(xs, zs);
});

test("weighted() and shuffle() are deterministic permutations", () => {
  const r = Xoshiro128ss.fromSeed(3n);
  const arr = Array.from({ length: 50 }, (_, i) => i);
  r.shuffle(arr);
  assert.deepEqual([...arr].sort((a, b) => a - b), Array.from({ length: 50 }, (_, i) => i));
  const counts = { a: 0, b: 0 };
  for (let i = 0; i < 10000; i++) counts[r.weighted([["a", 1], ["b", 3]] as const)]++;
  assert.ok(counts.b > counts.a * 2);
});
