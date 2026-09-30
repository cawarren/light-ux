#!/usr/bin/env node
// Dataset + probe hook for the rungs (run from anywhere):
//   node rungs/scripts/prepare.mjs [--seed-id dev-1] [--no-gen] [--default 50000]
// 1. Generates the dataset via the dataset package (npm run -w dataset gen -- --seed-id <id>),
//    the default nested sweep, so hashes match dataset/golden/<id>.json.
// 2. Copies 1k/10k/50k items.json into each rung's served dataset dir:
//      <rung>/public/dataset/{1k,10k,50k}/items.json, and <default size> as dataset/items.json,
//    plus queries.json (for tests). Also copies rungs/shared/{ladder-probe.js,marker.json} to
//    <rung>/public/ladder/{probe.js,marker.json}. Both dirs are gitignored; rungs fetch them at
//    runtime (nothing is bundled).
// 3. For Vite (served from dist/ by `vite preview`), also refreshes dist/ if it exists.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const seedId = arg('--seed-id', 'dev-1');
const defaultSize = arg('--default', '50000');
const SIZES = { '1k': 1000, '10k': 10000, '50k': 50000 };
const RUNGS = [
  { dir: 'rungs/r1-typical', served: ['public'] },
  { dir: 'rungs/r1-vite', served: ['public', 'dist'] },
];

const out = path.join(root, 'dataset/out', seedId);
if (!args.includes('--no-gen')) {
  execFileSync('npm', ['run', '-s', '-w', 'dataset', 'gen', '--', '--seed-id', seedId], { cwd: root, stdio: 'inherit' });
}

for (const rung of RUNGS) {
  const rungDir = path.join(root, rung.dir);
  if (!fs.existsSync(rungDir)) continue;
  for (const s of rung.served) {
    const base = path.join(rungDir, s);
    if (s !== 'public' && !fs.existsSync(base)) continue;
    const ds = path.join(base, 'dataset');
    fs.rmSync(ds, { recursive: true, force: true });
    for (const [name, n] of Object.entries(SIZES)) {
      fs.mkdirSync(path.join(ds, name), { recursive: true });
      fs.copyFileSync(path.join(out, String(n), 'items.json'), path.join(ds, name, 'items.json'));
    }
    fs.copyFileSync(path.join(out, defaultSize, 'items.json'), path.join(ds, 'items.json'));
    fs.copyFileSync(path.join(out, 'queries.json'), path.join(ds, 'queries.json'));
    const lad = path.join(base, 'ladder');
    fs.mkdirSync(lad, { recursive: true });
    fs.copyFileSync(path.join(root, 'rungs/shared/ladder-probe.js'), path.join(lad, 'probe.js'));
    fs.copyFileSync(path.join(root, 'rungs/shared/marker.json'), path.join(lad, 'marker.json'));
    console.log(`prepared ${path.relative(root, base)}/{dataset,ladder} (${seedId}; default ${defaultSize})`);
  }
}
