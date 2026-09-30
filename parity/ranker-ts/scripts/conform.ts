// Conformance runner (§2.2 levels).
//   node scripts/conform.ts --golden goldens.jsonl --candidate cand.jsonl --level strict|tie-insensitive
//        [--items items.json --queries queries.json] [--verbose-local] [--report report.json]
// candidate JSONL: {"qid", "ids":[...], "selected"?: id|null}  (a rank-cli `ranked` file works as-is)
// Output: one line per qid "<qid> PASS|FAIL <code>" plus a summary. Never prints query text or
// ranked results. --verbose-local (human-only, never on held-out logs) adds the first divergent
// rank with expected/actual ids. items+queries are needed for tie-insensitive checks when the hash
// differs, and for --verbose-local.
import fs from 'node:fs';
import { rank, type Ranking } from '../src/index.ts';
import { conformQuery, type Level } from '../src/conform.ts';
import { loadItems, loadQueries, readJsonl, arg, flag } from './io.ts';

const level = arg('--level', 'strict') as Level;
if (level !== 'strict' && level !== 'tie-insensitive') throw new Error('--level strict|tie-insensitive');
const goldens = readJsonl(arg('--golden')!);
const cands = new Map(readJsonl(arg('--candidate')!).map((c) => [JSON.stringify(c.qid), c]));
const verbose = flag('--verbose-local');
const itemsPath = arg('--items'), queriesPath = arg('--queries');
const items = itemsPath ? loadItems(itemsPath) : null;
const qtext = queriesPath ? new Map(loadQueries(queriesPath).map((r) => [JSON.stringify(r.qid), r.q])) : null;

let pass = 0, fail = 0, skip = 0;
const verdicts = [];
for (const g of goldens) {
  const key = JSON.stringify(g.qid);
  let memo: Ranking | undefined;
  const reference = items && qtext?.has(key) ? () => (memo ??= rank(items, qtext.get(key)!)) : undefined;
  const v = conformQuery(g, cands.get(key), level, reference);
  // only compute the divergence detail when asked
  const shown = verbose ? v : { qid: v.qid, pass: v.pass, skipped: v.skipped, code: v.code };
  verdicts.push(shown);
  if (v.skipped) skip++; else if (v.pass) pass++; else fail++;
  let line = `${v.qid}\t${v.skipped ? 'SKIP' : v.pass ? 'PASS' : 'FAIL'}\t${v.code}`;
  if (verbose && !v.pass && v.firstDivergentRank !== undefined) line += `\tfirstDivergentRank=${v.firstDivergentRank} expected=${v.expectedId} actual=${v.actualId}`;
  console.log(line);
}
console.log(`conform level=${level}: ${pass} pass, ${fail} fail, ${skip} skipped (golden records an upstream error), ${goldens.length} total`);
const report = arg('--report');
if (report) fs.writeFileSync(report, JSON.stringify({ level, pass, fail, skip, total: goldens.length, verdicts }, null, 1) + '\n');
process.exitCode = fail ? 1 : 0;
