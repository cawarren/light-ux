// Turns parity/goldens/edge-cases.src.json (hand derivations) into edge-cases.json with bit patterns.
// Evaluates each case's 'expr' (products of constants and P(n) = POW_0999[n]); never calls the scorer.
import fs from 'node:fs';
import path from 'node:path';
import { POW_0999, bitsHex } from '../src/index.ts';

const dir = path.resolve(import.meta.dirname, '../../goldens');
const src = JSON.parse(fs.readFileSync(path.join(dir, 'edge-cases.src.json'), 'utf8'));
const P = (n: number) => POW_0999[n];
const evalExpr = (e: string) => {
  if (!/^[0-9.*P() ]+$/.test(e)) throw new Error('bad expr ' + e);
  // left-to-right product, same order as the scorer's `score *= ...` sequence
  return e.split('*').map((t) => (t.startsWith('P(') ? P(Number(t.slice(2, -1))) : Number(t))).reduce((a, b) => a * b);
};
for (const c of src.cases) if (c.expr !== undefined) c.bits = bitsHex(evalExpr(c.expr));
for (const r of src.rankCases) if (r.tieGroupsExpr) r.tieGroups = r.tieGroupsExpr.map(([e, n]: [string, number]) => [bitsHex(evalExpr(e)), n]);
src.about += ' GENERATED from edge-cases.src.json by parity/ranker-ts/scripts/gen-edge-goldens.ts; edit the .src.json.';
fs.writeFileSync(path.join(dir, 'edge-cases.json'), JSON.stringify(src, null, 1) + '\n');
console.log(`wrote ${src.cases.length} score cases, ${src.rankCases.length} rank cases`);
