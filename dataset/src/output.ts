// Serialization (UTF-8, LF, one entry per line), hashing, the byte-stable
// manifest.json and the separate provenance.json (git SHA, Node version).

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { PKG_DIR } from "./data.ts";
import type { ItemTag } from "./items.ts";
import type { Query, QueryClass } from "./queries.ts";
import { QUERY_CLASSES } from "./queries.ts";
import { approxGraphemeLength, cpLength, hasAsciiLetter, isAscii, scriptsOf } from "./unicode.ts";

export function sha256(s: string | Buffer): string {
  return createHash("sha256").update(s).digest("hex");
}

export function itemsJson(seedId: string, items: readonly string[]): string {
  const head = `{"schema":1,"seedId":${JSON.stringify(seedId)},"count":${items.length},"items":[`;
  if (items.length === 0) return head + "]}\n";
  return head + "\n" + items.map((s) => JSON.stringify(s)).join(",\n") + "\n]}\n";
}

export interface QueryTarget {
  /** sha256 of items.json at every nested size the set applies to. */
  datasetSha256BySize: Record<string, string>;
  /** Size of the prefix queries were drawn from (smallest). */
  drawnFromSize: number;
  /** Size the no-match / NFD guarantees were checked against (largest). */
  checkedAgainstSize: number;
}

/** `datasetSha256` is the sha256 of the largest (check) size's items.json. */
export function queriesJson(seedId: string, target: QueryTarget, queries: readonly Query[]): string {
  const largest = target.datasetSha256BySize[String(target.checkedAgainstSize)];
  if (largest === undefined) throw new Error("queriesJson: missing sha256 for the check size");
  const bySize = sortedBySize(target.datasetSha256BySize);
  const head =
    `{"schema":1,"seedId":${JSON.stringify(seedId)},"datasetSha256":${JSON.stringify(largest)},` +
    `"datasetSha256BySize":${JSON.stringify(bySize)},"drawnFromSize":${target.drawnFromSize},` +
    `"checkedAgainstSize":${target.checkedAgainstSize},"queries":[`;
  if (queries.length === 0) return head + "]}\n";
  const lines = queries.map(
    (q) =>
      `{"qid":${q.qid},"q":${JSON.stringify(q.q)},"class":${JSON.stringify(q.class)},"typeable":${q.typeable}}`,
  );
  return head + "\n" + lines.join(",\n") + "\n]}\n";
}

export function writeLf(path: string, content: string): void {
  if (content.includes("\r")) throw new Error(`refusing to write CR to ${path}`);
  writeFileSync(path, content, { encoding: "utf8" });
}

function gitInfo(): { sha: string | null; dirty: boolean | null } {
  try {
    const sha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: PKG_DIR, stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
    const status = execFileSync("git", ["status", "--porcelain", "--", "."], {
      cwd: PKG_DIR,
      stdio: ["ignore", "pipe", "ignore"],
    }).toString();
    return { sha, dirty: status.trim().length > 0 };
  } catch {
    return { sha: null, dirty: null };
  }
}

function bucket(n: number): string {
  const lo = Math.floor(n / 8) * 8;
  return `${lo}-${lo + 7}`;
}

function histogram(values: number[]): Record<string, number> {
  const counts = new Map<number, number>();
  for (const v of values) {
    const lo = Math.floor(v / 8) * 8;
    counts.set(lo, (counts.get(lo) ?? 0) + 1);
  }
  const out: Record<string, number> = {};
  for (const lo of [...counts.keys()].sort((a, b) => a - b)) out[bucket(lo)] = counts.get(lo) as number;
  return out;
}

function minOf(xs: number[]): number {
  let m = Number.MAX_SAFE_INTEGER;
  for (const x of xs) if (x < m) m = x;
  return m;
}

function maxOf(xs: number[]): number {
  let m = 0;
  for (const x of xs) if (x > m) m = x;
  return m;
}

function countBy<T extends string>(keys: T[]): Record<string, number> {
  const m = new Map<string, number>();
  for (const k of keys) m.set(k, (m.get(k) ?? 0) + 1);
  const out: Record<string, number> = {};
  for (const k of [...m.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))) out[k] = m.get(k) as number;
  return out;
}

/** Percentage with 3 decimals from integers, as a string (no float formatting drift). */
function pct(num: number, den: number): string {
  if (den === 0) return "0.000";
  const scaled = Math.floor((num * 100000 + Math.floor(den / 2)) / den); // round half up, 1e-3 %
  return `${Math.floor(scaled / 1000)}.${String(scaled % 1000).padStart(3, "0")}`;
}

function sortedBySize(m: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of Object.keys(m).sort((a, b) => Number(a) - Number(b))) out[k] = m[k] as string;
  return out;
}

/**
 * provenance.json: how the outputs were produced. Deliberately separate from
 * manifest.json, which must stay byte-stable across commits and machines.
 * No timestamp is recorded (wall-clock time is banned in src/).
 */
export function buildProvenance(): Record<string, unknown> {
  const git = gitInfo();
  return {
    schema: 1,
    generator: { package: "@latency-ladder/dataset", gitSha: git.sha, gitDirty: git.dirty },
    node: process.version,
    platform: process.platform,
    arch: process.arch,
  };
}

export interface ManifestInput {
  seedId: string;
  source: string;
  variant: string;
  items: readonly string[];
  tags: readonly ItemTag[];
  itemsJson: string;
}

/** Byte-stable item manifest: a pure function of the generated items. */
export function buildManifest(m: ManifestInput): Record<string, unknown> {
  const n = m.items.length;
  const nonLatin = m.tags.filter((t) => t.nonLatin).length;
  const scriptKeys: string[] = [];
  let mixed = 0;
  let latinAscii = 0;
  m.items.forEach((s, i) => {
    const tag = m.tags[i] as ItemTag;
    if (tag.nonLatin) {
      for (const sc of scriptsOf(s)) scriptKeys.push(sc);
      if (hasAsciiLetter(s)) mixed++;
    } else if (isAscii(s)) latinAscii++;
  });
  const cpLens = m.items.map(cpLength);
  const manifest: Record<string, unknown> = {
    schema: 1,
    seedId: m.seedId,
    seedSource: m.source,
    variant: m.variant,
    count: n,
    scriptMix: {
      latin: n - nonLatin,
      latinAscii,
      latinNonAscii: n - nonLatin - latinAscii,
      nonLatin,
      nonLatinPercent: pct(nonLatin, n),
      nonLatinByCategory: countBy(m.tags.filter((t) => t.nonLatin).map((t) => t.kind)),
      nonLatinItemsContainingScript: countBy(scriptKeys),
      nonLatinMixedWithAsciiLetters: mixed,
    },
    templateMix: countBy(m.tags.filter((t) => !t.nonLatin).map((t) => t.kind)),
    lengthHistogram: {
      bucketWidth: 8,
      codePoints: { min: minOf(cpLens), max: maxOf(cpLens), buckets: histogram(cpLens) },
      utf16CodeUnits: { buckets: histogram(m.items.map((s) => s.length)) },
      graphemesApprox: {
        note: "approximate: simple committed rule (unicode.ts approxClusters), not UAX #29 segmentation",
        buckets: histogram(m.items.map(approxGraphemeLength)),
      },
      utf8Bytes: { buckets: histogram(m.items.map((s) => Buffer.byteLength(s, "utf8"))) },
    },
    files: {
      "items.json": { sha256: sha256(m.itemsJson), bytes: Buffer.byteLength(m.itemsJson, "utf8") },
    } as Record<string, unknown>,
  };
  return manifest;
}

/** Byte-stable manifest for a (shared) query set. */
export function buildQueryManifest(
  seedId: string,
  source: string,
  variant: string,
  target: QueryTarget,
  queries: readonly Query[],
  qJson: string,
): Record<string, unknown> {
  const manifest: Record<string, unknown> = {
    schema: 1,
    seedId,
    seedSource: source,
    variant,
    datasetSha256BySize: sortedBySize(target.datasetSha256BySize),
    drawnFromSize: target.drawnFromSize,
    checkedAgainstSize: target.checkedAgainstSize,
    files: {} as Record<string, unknown>,
  };
  addQueriesToManifest(manifest, queries, qJson);
  return manifest;
}

export function addQueriesToManifest(
  manifest: Record<string, unknown>,
  queries: readonly Query[],
  qJson: string,
): void {
  const perClass: Record<string, { count: number; typeable: number }> = {};
  for (const c of QUERY_CLASSES) perClass[c] = { count: 0, typeable: 0 };
  for (const q of queries) {
    const pc = perClass[q.class as QueryClass] as { count: number; typeable: number };
    pc.count++;
    if (q.typeable) pc.typeable++;
  }
  manifest.queries = {
    count: queries.length,
    typeable: queries.filter((q) => q.typeable).length,
    perClass,
    note: "match-count and tie histograms per class need the reference ranker (parity/); not computed here",
  };
  (manifest.files as Record<string, unknown>)["queries.json"] = {
    sha256: sha256(qJson),
    bytes: Buffer.byteLength(qJson, "utf8"),
  };
}

export function manifestJson(manifest: Record<string, unknown>): string {
  return JSON.stringify(manifest, null, 2) + "\n";
}

export function writeOutputs(
  dir: string,
  files: { itemsJson?: string; queriesJson?: string; manifest: Record<string, unknown> },
): void {
  mkdirSync(dir, { recursive: true });
  if (files.itemsJson !== undefined) writeLf(join(dir, "items.json"), files.itemsJson);
  if (files.queriesJson !== undefined) writeLf(join(dir, "queries.json"), files.queriesJson);
  writeLf(join(dir, "manifest.json"), manifestJson(files.manifest));
  writeLf(join(dir, "provenance.json"), JSON.stringify(buildProvenance(), null, 2) + "\n");
}

export function readManifest(dir: string): Record<string, unknown> | undefined {
  const p = join(dir, "manifest.json");
  if (!existsSync(p)) return undefined;
  return JSON.parse(readFileSync(p, "utf8")) as Record<string, unknown>;
}
