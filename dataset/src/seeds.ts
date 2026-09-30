// Seed registry and held-out seed derivation.
//
// Dev seeds are public. Any other seed id is a held-out id: its seeds are
// HMAC-SHA256(secret, "<seedId>/items") and HMAC-SHA256(secret,
// "<seedId>/queries"), first 8 bytes big-endian. The secret is read from a
// file (--secret-file) or the LL_HELDOUT_SECRET env var and is never written
// to any output.

import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";

export const SHIFTED_SUFFIX = "-shifted";

/** Public dev seeds (64-bit). "LL-dev-1" in ASCII. */
export const DEV_SEEDS: Readonly<Record<string, bigint>> = {
  "dev-1": 0x4c4c2d6465762d31n,
};

/** XORed into a dev seed for its distribution-shifted variant. */
const SHIFT_CONST = 0x7368696674656421n; // "shifted!"

export interface ResolvedSeeds {
  seedId: string;
  base: string;
  shifted: boolean;
  source: "public-dev-seed" | "hmac-sha256-secret";
  items: bigint;
  queries: bigint;
}

export function splitSeedId(seedId: string): { base: string; shifted: boolean } {
  if (seedId.endsWith(SHIFTED_SUFFIX)) return { base: seedId.slice(0, -SHIFTED_SUFFIX.length), shifted: true };
  return { base: seedId, shifted: false };
}

export function isPublicSeedId(seedId: string): boolean {
  return Object.hasOwn(DEV_SEEDS, splitSeedId(seedId).base);
}

export function hmacSeed(secret: Buffer, label: string): bigint {
  const mac = createHmac("sha256", secret).update(label, "utf8").digest();
  return mac.readBigUInt64BE(0);
}

export function loadSecret(secretFile: string | undefined): Buffer | undefined {
  let raw: Buffer | undefined;
  if (secretFile) raw = readFileSync(secretFile);
  else if (process.env.LL_HELDOUT_SECRET) raw = Buffer.from(process.env.LL_HELDOUT_SECRET, "utf8");
  if (raw === undefined) return undefined;
  const text = raw.toString("utf8").trim();
  const secret = /^[0-9a-fA-F]+$/.test(text) && text.length % 2 === 0 ? Buffer.from(text, "hex") : raw;
  if (secret.length < 32) throw new Error("held-out secret must be at least 256 bits (64 hex chars)");
  return secret;
}

/** seedId may carry the "-shifted" suffix; that selects the shifted variant. */
export function resolveSeeds(seedId: string, secret: Buffer | undefined): ResolvedSeeds {
  const { base, shifted } = splitSeedId(seedId);
  const dev = DEV_SEEDS[base];
  if (dev !== undefined && Object.hasOwn(DEV_SEEDS, base)) {
    const s = shifted ? dev ^ SHIFT_CONST : dev;
    return { seedId, base, shifted, source: "public-dev-seed", items: s, queries: s };
  }
  if (secret === undefined) {
    throw new Error(
      `seed id "${seedId}" is not a public dev seed; pass --secret-file <path> or set LL_HELDOUT_SECRET`,
    );
  }
  return {
    seedId,
    base,
    shifted,
    source: "hmac-sha256-secret",
    items: hmacSeed(secret, `${seedId}/items`),
    queries: hmacSeed(secret, `${seedId}/queries`),
  };
}
