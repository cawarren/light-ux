#!/usr/bin/env node
// R1 Next and R1 Vite must render the same palette from the same source. The shared files are
// duplicated (each rung is a standalone app with its own lockfile) and kept identical here.
// shadcn generates ui/*.tsx with a leading "use client" only for RSC projects (Next), so that
// directive is ignored when comparing the ui files.
import fs from 'node:fs';
import path from 'node:path';

const rungs = path.resolve(import.meta.dirname, '..');
const next = (p) => path.join(rungs, 'r1-typical', p);
const vite = (p) => path.join(rungs, 'r1-vite', 'src', p);
const PAIRS = [
  ['components/palette.tsx', 'exact'],
  ['components/latency-marker.tsx', 'exact'],
  ['components/ladder-types.ts', 'exact'],
  ['components/ui/command.tsx', 'use-client'],
  ['components/ui/dialog.tsx', 'use-client'],
  ['components/ui/input-group.tsx', 'use-client'],
  ['components/ui/input.tsx', 'use-client'],
  ['components/ui/textarea.tsx', 'use-client'],
  ['components/ui/button.tsx', 'use-client'],
  ['lib/utils.ts', 'exact'],
];
const strip = (s) => s.replace(/^"use client"\n\n?/, '');
let bad = 0;
for (const [p, mode] of PAIRS) {
  const a = fs.readFileSync(next(p), 'utf8');
  const b = fs.readFileSync(vite(p), 'utf8');
  const same = mode === 'exact' ? a === b : strip(a) === strip(b);
  if (!same) { bad++; console.error(`OUT OF SYNC: r1-typical/${p} vs r1-vite/src/${p}`); }
}
// ladder-types.ts must match the shared d.ts (minus its header line and `declare global` block).
const dts = fs.readFileSync(path.join(rungs, 'shared/ladder-probe.d.ts'), 'utf8');
const body = (s) => s.split('\n').slice(1).join('\n').replace(/\ndeclare global[\s\S]*$/, '\n').trimEnd();
if (body(dts) !== body(fs.readFileSync(next('components/ladder-types.ts'), 'utf8'))) {
  bad++; console.error('OUT OF SYNC: shared/ladder-probe.d.ts vs components/ladder-types.ts');
}
console.log(bad ? `check-sync: ${bad} file(s) out of sync` : `check-sync: ${PAIRS.length + 1} files in sync`);
process.exitCode = bad ? 1 : 0;
