// Glue used by the CLI and the tests.
//
// Default ("shared") query mode: ONE query set per seed, drawn from the
// smallest nested prefix so every query targets items present at every size,
// with the no-match / NFD guarantees checked against the largest size.
// "per-size" mode draws and checks a separate set for each size.

import { generateItems, type GeneratedItems, type ItemTag, type Variant } from "./items.ts";
import { generateQueries, type Query } from "./queries.ts";
import { resolveSeeds, type ResolvedSeeds } from "./seeds.ts";
import {
  itemsJson,
  queriesJson,
  sha256,
  buildManifest,
  buildQueryManifest,
  addQueriesToManifest,
  manifestJson,
  type QueryTarget,
} from "./output.ts";

export const DEFAULT_QUERY_COUNT = 1000;
export const SWEEP_SIZES = [1000, 10000, 50000, 100000];
export const GOLDEN_SIZES = SWEEP_SIZES;

export type QueryMode = "shared" | "per-size" | "none";

export interface SizeOutput {
  size: number;
  items: string[];
  tags: ItemTag[];
  itemsJson: string;
  manifest: Record<string, unknown>;
  /** Only in per-size mode. */
  queries?: Query[];
  queriesJson?: string;
}

export interface SharedQueries {
  queries: Query[];
  queriesJson: string;
  manifest: Record<string, unknown>;
  target: QueryTarget;
}

export interface Built {
  seeds: ResolvedSeeds;
  variant: Variant;
  sizes: SizeOutput[];
  shared?: SharedQueries;
}

export function generateStream(seedId: string, maxSize: number, secret?: Buffer): {
  seeds: ResolvedSeeds;
  variant: Variant;
  gen: GeneratedItems;
} {
  const seeds = resolveSeeds(seedId, secret);
  const variant: Variant = seeds.shifted ? "shifted" : "standard";
  const gen = generateItems({ seed: seeds.items, size: maxSize, variant });
  return { seeds, variant, gen };
}

function sizeOutput(seeds: ResolvedSeeds, variant: Variant, gen: GeneratedItems, size: number): SizeOutput {
  const items = gen.items.slice(0, size);
  const tags = gen.tags.slice(0, size);
  const ij = itemsJson(seeds.seedId, items);
  const manifest = buildManifest({ seedId: seeds.seedId, source: seeds.source, variant, items, tags, itemsJson: ij });
  return { size, items, tags, itemsJson: ij, manifest };
}

export interface BuildOptions {
  sizes: number[];
  queryCount?: number;
  mode?: QueryMode;
  secret?: Buffer;
}

export function buildAll(seedId: string, opts: BuildOptions): Built {
  const sizes = [...new Set(opts.sizes)].sort((a, b) => a - b);
  if (sizes.length === 0) throw new Error("no sizes");
  const queryCount = opts.queryCount ?? DEFAULT_QUERY_COUNT;
  const mode: QueryMode = queryCount > 0 ? (opts.mode ?? "shared") : "none";
  const smallest = sizes[0] as number;
  const largest = sizes[sizes.length - 1] as number;
  const { seeds, variant, gen } = generateStream(seedId, largest, opts.secret);
  const outs = sizes.map((n) => sizeOutput(seeds, variant, gen, n));
  const built: Built = { seeds, variant, sizes: outs };

  if (mode === "shared") {
    const bySize: Record<string, string> = {};
    for (const o of outs) bySize[String(o.size)] = sha256(o.itemsJson);
    const target: QueryTarget = { datasetSha256BySize: bySize, drawnFromSize: smallest, checkedAgainstSize: largest };
    const queries = generateQueries(gen.items.slice(0, smallest), {
      seed: seeds.queries,
      count: queryCount,
      checkItems: gen.items.slice(0, largest),
    });
    const qj = queriesJson(seeds.seedId, target, queries);
    const manifest = buildQueryManifest(seeds.seedId, seeds.source, variant, target, queries, qj);
    built.shared = { queries, queriesJson: qj, manifest, target };
  } else if (mode === "per-size") {
    for (const o of outs) {
      const target: QueryTarget = {
        datasetSha256BySize: { [String(o.size)]: sha256(o.itemsJson) },
        drawnFromSize: o.size,
        checkedAgainstSize: o.size,
      };
      o.queries = generateQueries(o.items, { seed: seeds.queries, count: queryCount });
      o.queriesJson = queriesJson(seeds.seedId, target, o.queries);
      addQueriesToManifest(o.manifest, o.queries, o.queriesJson);
    }
  }
  return built;
}

/** sha256 of the dev-1 sweep outputs (shared 1,000-query set). */
export function goldenHashes(): Record<string, unknown> {
  const b = buildAll("dev-1", { sizes: GOLDEN_SIZES });
  const sizes: Record<string, Record<string, string>> = {};
  for (const o of b.sizes) {
    sizes[String(o.size)] = {
      "items.json": sha256(o.itemsJson),
      "manifest.json": sha256(manifestJson(o.manifest)),
    };
  }
  const shared = b.shared as SharedQueries;
  return {
    sizes,
    shared: {
      "queries.json": sha256(shared.queriesJson),
      "manifest.json": sha256(manifestJson(shared.manifest)),
    },
  };
}
