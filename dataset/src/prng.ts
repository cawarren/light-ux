// Deterministic PRNG: xoshiro128** (32-bit words) seeded by SplitMix64.
//
// References:
//   https://prng.di.unimi.it/xoshiro128starstar.c  (Blackman & Vigna, v1.1)
//   https://prng.di.unimi.it/splitmix64.c           (Vigna)
//
// Only Math.imul, >>> 0 and shifts are used on the 32-bit path, so every
// JS engine produces identical sequences. SplitMix64 uses BigInt and runs
// only at seeding time. Sampling is integer-only with rejection; no floats.

const M64 = (1n << 64n) - 1n;

export class SplitMix64 {
  private state: bigint;
  constructor(seed: bigint) {
    this.state = BigInt.asUintN(64, seed);
  }
  next(): bigint {
    this.state = (this.state + 0x9e3779b97f4a7c15n) & M64;
    let z = this.state;
    z = ((z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n) & M64;
    z = ((z ^ (z >> 27n)) * 0x94d049bb133111ebn) & M64;
    return z ^ (z >> 31n);
  }
}

function rotl(x: number, k: number): number {
  return ((x << k) | (x >>> (32 - k))) >>> 0;
}

const TWO_32 = 4294967296;

export class Xoshiro128ss {
  private s0: number;
  private s1: number;
  private s2: number;
  private s3: number;

  /** Raw state constructor (used by reference test vectors). */
  constructor(s0: number, s1: number, s2: number, s3: number) {
    this.s0 = s0 >>> 0;
    this.s1 = s1 >>> 0;
    this.s2 = s2 >>> 0;
    this.s3 = s3 >>> 0;
    if ((this.s0 | this.s1 | this.s2 | this.s3) === 0) {
      throw new Error("xoshiro128**: all-zero state");
    }
  }

  /**
   * Seed from a 64-bit value: two SplitMix64 outputs a, b give the state
   * words [lo32(a), hi32(a), lo32(b), hi32(b)].
   */
  static fromSeed(seed: bigint): Xoshiro128ss {
    const sm = new SplitMix64(seed);
    const a = sm.next();
    const b = sm.next();
    return new Xoshiro128ss(
      Number(a & 0xffffffffn),
      Number(a >> 32n),
      Number(b & 0xffffffffn),
      Number(b >> 32n),
    );
  }

  nextU32(): number {
    const result = Math.imul(rotl(Math.imul(this.s1, 5) >>> 0, 7), 9) >>> 0;
    const t = (this.s1 << 9) >>> 0;
    this.s2 = (this.s2 ^ this.s0) >>> 0;
    this.s3 = (this.s3 ^ this.s1) >>> 0;
    this.s1 = (this.s1 ^ this.s2) >>> 0;
    this.s0 = (this.s0 ^ this.s3) >>> 0;
    this.s2 = (this.s2 ^ t) >>> 0;
    this.s3 = rotl(this.s3, 11);
    return result;
  }

  /** Uniform integer in [0, n), 1 <= n <= 2^32, by rejection sampling. */
  below(n: number): number {
    if (!Number.isInteger(n) || n < 1 || n > TWO_32) {
      throw new Error(`below(): bad bound ${n}`);
    }
    // limit is the largest multiple of n that is <= 2^32. All values are
    // integers < 2^53, so the arithmetic is exact.
    const limit = TWO_32 - (TWO_32 % n);
    for (;;) {
      const x = this.nextU32();
      if (x < limit) return x % n;
    }
  }

  /** Uniform integer in [lo, hi] inclusive. */
  range(lo: number, hi: number): number {
    return lo + this.below(hi - lo + 1);
  }

  pick<T>(arr: readonly T[]): T {
    if (arr.length === 0) throw new Error("pick(): empty array");
    return arr[this.below(arr.length)] as T;
  }

  /** True with probability num/den (integers). */
  chance(num: number, den: number): boolean {
    return this.below(den) < num;
  }

  /** Pick a key by non-negative integer weights, in the array's order. */
  weighted<K>(entries: readonly (readonly [K, number])[]): K {
    let total = 0;
    for (const [, w] of entries) total += w;
    let r = this.below(total);
    for (const [k, w] of entries) {
      if (r < w) return k;
      r -= w;
    }
    throw new Error("weighted(): unreachable");
  }

  /** In-place Fisher-Yates shuffle. */
  shuffle<T>(arr: T[]): T[] {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = this.below(i + 1);
      const tmp = arr[i] as T;
      arr[i] = arr[j] as T;
      arr[j] = tmp;
    }
    return arr;
  }
}

/** Stream constants: one PRNG stream per concern, seeded from seed ^ const. */
export const STREAM = {
  mix: 0x6d69782d73747265n, // "mix-stre"
  latin: 0x6c6174696e2d7374n, // "latin-st"
  nonLatin: 0x6e6f6e6c6174696en, // "nonlatin"
  queries: 0x7175657269657321n, // "queries!"
} as const;

export function stream(seed: bigint, streamConst: bigint): Xoshiro128ss {
  return Xoshiro128ss.fromSeed(BigInt.asUintN(64, seed ^ streamConst));
}
