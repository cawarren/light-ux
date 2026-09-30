// Golden generator (§2.3).
//   node scripts/golden.ts --items items.json --queries queries.json --out goldens.jsonl
// One JSONL line per query: {"qid","count","idsSha256","selected","top":[[id,scoreBitsHex]×≤100],"tieGroups":[[scoreBitsHex,count],...]}
// Prints only counts (held-out hygiene).
import fs from 'node:fs';
import { goldenLine } from '../src/golden.ts';
import { loadItems, loadQueries, arg } from './io.ts';

const items = loadItems(arg('--items')!);
const queries = loadQueries(arg('--queries')!);
const out = arg('--out');
if (!out) throw new Error('--out required');
const lines = queries.map(({ qid, q }) => JSON.stringify(goldenLine(items, qid, q)));
fs.writeFileSync(out, lines.join('\n') + '\n');
console.log(`golden: ${lines.length} queries x ${items.length} items -> ${out}`);
