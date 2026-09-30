// CLI: gen | queries | commit | sample | golden
//
//   npm run -w dataset gen -- --seed-id dev-1        (default sweep 1k,10k,50k,100k + shared queries)
//   npm run -w dataset gen -- --seed-id dev-1 --sizes 1000,10000,50000,100000 [--per-size-queries]
//   npm run -w dataset gen -- --seed-id dev-1 --size 100000 [--out DIR]
//   npm run -w dataset queries -- --dataset out/dev-1/1000/items.json --check out/dev-1/100000/items.json
//   npm run -w dataset commit -- out/heldout-v1 out/heldout-v1/50000
//   npm run -w dataset sample -- --seed-id dev-1 --size 50000
//   npm run -w dataset golden            (rewrites golden/dev-1.json)

import { parseArgs } from "node:util";
import { readFileSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { PKG_DIR } from "./data.ts";
import {
  generateStream,
  buildAll,
  DEFAULT_QUERY_COUNT,
  SWEEP_SIZES,
  goldenHashes,
  type SizeOutput,
} from "./pipeline.ts";
import { generateQueries, isSubsequence } from "./queries.ts";
import { isPublicSeedId, loadSecret, resolveSeeds } from "./seeds.ts";
import {
  addQueriesToManifest,
  buildQueryManifest,
  queriesJson,
  type QueryTarget,
  readManifest,
  sha256,
  writeOutputs,
  writeLf,
  manifestJson,
} from "./output.ts";
import { validateItem } from "./unicode.ts";

// npm run -w sets cwd to the package; resolve user paths against where the
// user actually ran npm (INIT_CWD).
const USER_CWD = process.env.INIT_CWD ?? process.cwd();
function userPath(p: string): string {
  return isAbsolute(p) ? p : resolve(USER_CWD, p);
}

function ms(t0: bigint): string {
  return `${(process.hrtime.bigint() - t0) / 1000000n} ms`;
}

function parseSizes(v: { size?: string; sizes?: string }): number[] {
  const raw = v.sizes ?? v.size ?? SWEEP_SIZES.join(",");
  const sizes = raw.split(",").map((s) => Number(s.trim()));
  for (const s of sizes) if (!Number.isInteger(s) || s < 1) throw new Error(`bad size: ${s}`);
  return sizes;
}

function seedIdWithVariant(seedId: string, shift: boolean | undefined): string {
  return shift && !seedId.endsWith("-shifted") ? `${seedId}-shifted` : seedId;
}

function printHashes(dir: string, manifest: Record<string, unknown>): void {
  const files = manifest.files as Record<string, { sha256: string }>;
  for (const [name, f] of Object.entries(files)) process.stdout.write(`${f.sha256}  ${join(dir, name)}\n`);
  process.stdout.write(`${sha256(manifestJson(manifest))}  ${join(dir, "manifest.json")}\n`);
}

function cmdGen(argv: string[]): void {
  const { values } = parseArgs({
    args: argv,
    options: {
      "seed-id": { type: "string", default: "dev-1" },
      size: { type: "string" },
      sizes: { type: "string" },
      out: { type: "string" },
      shift: { type: "boolean", default: false },
      "secret-file": { type: "string" },
      queries: { type: "string", default: String(DEFAULT_QUERY_COUNT) },
      "per-size-queries": { type: "boolean", default: false },
    },
  });
  const seedId = seedIdWithVariant(values["seed-id"] as string, values.shift);
  const sizes = parseSizes(values);
  const secret = isPublicSeedId(seedId) ? undefined : loadSecret(values["secret-file"]);
  const t0 = process.hrtime.bigint();
  const b = buildAll(seedId, {
    sizes,
    queryCount: Number(values.queries),
    mode: values["per-size-queries"] ? "per-size" : "shared",
    secret,
  });
  process.stderr.write(`generated ${seedId} (${b.variant}) in ${ms(t0)}\n`);

  const single = values.size !== undefined && values.sizes === undefined && b.sizes.length === 1;
  if (single) {
    // One size: everything in one directory; the query section joins the item manifest.
    const o = b.sizes[0] as SizeOutput;
    const dir = values.out ? userPath(values.out) : join(PKG_DIR, "out", seedId, String(o.size));
    const qj = b.shared?.queriesJson ?? o.queriesJson;
    const qs = b.shared?.queries ?? o.queries;
    if (b.shared && qs && qj) addQueriesToManifest(o.manifest, qs, qj);
    writeOutputs(dir, { itemsJson: o.itemsJson, queriesJson: qj, manifest: o.manifest });
    printHashes(dir, o.manifest);
  } else {
    const root = values.out ? userPath(values.out) : join(PKG_DIR, "out", seedId);
    for (const o of b.sizes) {
      const dir = join(root, String(o.size));
      writeOutputs(dir, { itemsJson: o.itemsJson, queriesJson: o.queriesJson, manifest: o.manifest });
      printHashes(dir, o.manifest);
    }
    if (b.shared) {
      writeOutputs(root, { queriesJson: b.shared.queriesJson, manifest: b.shared.manifest });
      printHashes(root, b.shared.manifest);
    }
  }
  process.stderr.write(`total ${ms(t0)}\n`);
}

function readItems(path: string): { raw: string; seedId: string; items: string[] } {
  const raw = readFileSync(path, "utf8");
  const parsed = JSON.parse(raw) as { schema: number; seedId: string; items: string[] };
  if (parsed.schema !== 1) throw new Error(`${path}: unsupported items.json schema`);
  parsed.items.forEach((s, i) => {
    const bad = validateItem(s);
    if (bad !== null) throw new Error(`${path}: item ${i} invalid: ${bad}`);
  });
  return { raw, seedId: parsed.seedId, items: parsed.items };
}

function cmdQueries(argv: string[]): void {
  const { values } = parseArgs({
    args: argv,
    options: {
      dataset: { type: "string" },
      check: { type: "string" },
      "seed-id": { type: "string" },
      count: { type: "string", default: String(DEFAULT_QUERY_COUNT) },
      out: { type: "string" },
      "secret-file": { type: "string" },
    },
  });
  if (!values.dataset) throw new Error("--dataset <items.json> is required (the prefix queries are drawn from)");
  const drawPath = userPath(values.dataset);
  const checkPath = values.check ? userPath(values.check) : drawPath;
  const draw = readItems(drawPath);
  const check = checkPath === drawPath ? draw : readItems(checkPath);
  if (check.seedId !== draw.seedId) throw new Error("--dataset and --check have different seed ids");
  for (let i = 0; i < draw.items.length; i++) {
    if (draw.items[i] !== check.items[i]) throw new Error("--dataset must be a prefix of --check");
  }
  const seedId = values["seed-id"] ?? draw.seedId;
  const secret = isPublicSeedId(seedId) ? undefined : loadSecret(values["secret-file"]);
  const seeds = resolveSeeds(seedId, secret);
  const t0 = process.hrtime.bigint();
  const queries = generateQueries(draw.items, {
    seed: seeds.queries,
    count: Number(values.count),
    checkItems: check.items,
  });
  const target: QueryTarget = {
    datasetSha256BySize: { [String(draw.items.length)]: sha256(draw.raw), [String(check.items.length)]: sha256(check.raw) },
    drawnFromSize: draw.items.length,
    checkedAgainstSize: check.items.length,
  };
  const qj = queriesJson(seedId, target, queries);
  const dir = values.out ? userPath(values.out) : join(checkPath, "..");
  mkdirSync(dir, { recursive: true });
  writeLf(join(dir, "queries.json"), qj);
  const existing = readManifest(dir);
  const itemsSha = (existing?.files as Record<string, { sha256: string }> | undefined)?.["items.json"]?.sha256;
  const manifest =
    existing && itemsSha === sha256(check.raw)
      ? existing
      : buildQueryManifest(seedId, seeds.source, seeds.shifted ? "shifted" : "standard", target, queries, qj);
  if (manifest === existing) addQueriesToManifest(manifest, queries, qj);
  writeLf(join(dir, "manifest.json"), manifestJson(manifest));
  process.stderr.write(`generated ${queries.length} queries in ${ms(t0)}\n`);
  process.stdout.write(`${sha256(qj)}  ${join(dir, "queries.json")}\n`);
}

function cmdCommit(argv: string[]): void {
  const { positionals } = parseArgs({ args: argv, allowPositionals: true, options: {} });
  if (positionals.length === 0) throw new Error("usage: commit <output dir> [more dirs]");
  process.stdout.write("# sha256 of held-out outputs, for commit-reveal (publish these, not the files)\n");
  for (const d of positionals) {
    const dir = userPath(d);
    for (const name of ["items.json", "queries.json", "manifest.json", "goldens.json"]) {
      const p = join(dir, name);
      if (existsSync(p)) process.stdout.write(`${sha256(readFileSync(p))}  ${join(d, name)}\n`);
    }
  }
}

function cmdSample(argv: string[]): void {
  const { values } = parseArgs({
    args: argv,
    options: {
      "seed-id": { type: "string", default: "dev-1" },
      size: { type: "string", default: "50000" },
      n: { type: "string", default: "40" },
      queries: { type: "string", default: "a,o,op,open,open fi,sett,gtb,reqeust,設定,👩" },
    },
  });
  const seedId = values["seed-id"] as string;
  if (!isPublicSeedId(seedId)) throw new Error("sample prints item text; only public dev seeds are allowed");
  const size = Number(values.size);
  const n = Number(values.n);
  const { gen } = generateStream(seedId, size);
  const stride = Math.floor(size / n);
  process.stdout.write(`Sample of ${n} items (every ${stride}th) from ${seedId}, size ${size}:\n\n`);
  for (let i = 0; i < n; i++) {
    const id = i * stride;
    process.stdout.write(`${String(id).padStart(6)}  ${gen.items[id]}\n`);
  }
  const lower = gen.items.map((s) => s.toLowerCase());
  process.stdout.write(`\nMatch counts (case-insensitive subsequence approximation, not the ranker), size ${size}:\n\n`);
  for (const q of (values.queries as string).split(",")) {
    const ql = q.toLowerCase();
    let c = 0;
    for (const s of lower) if (isSubsequence(ql, s)) c++;
    process.stdout.write(`${JSON.stringify(q).padEnd(12)} ${String(c).padStart(7)}  (${Math.floor((c * 1000) / size) / 10}%)\n`);
  }
}

function cmdGolden(): void {
  const t0 = process.hrtime.bigint();
  const hashes = goldenHashes();
  const body = { seedId: "dev-1", queryCount: DEFAULT_QUERY_COUNT, queryMode: "shared", sha256: hashes };
  const p = join(PKG_DIR, "golden", "dev-1.json");
  writeFileSync(p, JSON.stringify(body, null, 2) + "\n");
  process.stdout.write(readFileSync(p, "utf8"));
  process.stderr.write(`golden written in ${ms(t0)}\n`);
}

function main(): void {
  const [cmd, ...rest] = process.argv.slice(2);
  switch (cmd) {
    case "gen":
      return cmdGen(rest);
    case "queries":
      return cmdQueries(rest);
    case "commit":
      return cmdCommit(rest);
    case "sample":
      return cmdSample(rest);
    case "golden":
      return cmdGolden();
    default:
      process.stderr.write("usage: cli.ts <gen|queries|commit|sample|golden> [options]\n");
      process.exit(2);
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("cli.ts")) {
  try {
    main();
  } catch (e) {
    process.stderr.write(`error: ${(e as Error).message}\n`);
    process.exit(1);
  }
}
