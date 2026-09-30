// Production build: esbuild bundles src/ into dist/ (no framework, no runtime dependencies).
//   dist/index.html, dist/assets/{main.js,app.css,<fonts>}, dist/worker.js, plus public/ (the
//   dataset and probe copied there by rungs/scripts/prepare.mjs).
import fs from 'node:fs';
import path from 'node:path';
import * as esbuild from 'esbuild';

const root = import.meta.dirname;
const dist = path.join(root, 'dist');
fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(path.join(dist, 'assets'), { recursive: true });

const common = { bundle: true, minify: true, sourcemap: true, target: 'chrome120', legalComments: 'inline', logLevel: 'info' };
await esbuild.build({ ...common, entryPoints: { main: 'src/main.ts' }, outdir: 'dist/assets', format: 'esm' });
await esbuild.build({ ...common, entryPoints: { worker: 'src/worker.ts' }, outdir: 'dist', format: 'iife' });
await esbuild.build({
  ...common, entryPoints: { app: 'src/styles.css' }, outdir: 'dist/assets',
  loader: { '.woff2': 'file', '.woff': 'file' }, assetNames: '[name]-[hash]',
});
fs.copyFileSync(path.join(root, 'index.html'), path.join(dist, 'index.html'));
if (fs.existsSync(path.join(root, 'public'))) fs.cpSync(path.join(root, 'public'), dist, { recursive: true });
console.log('built dist/');
