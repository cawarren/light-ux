import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { score, rank, tieGroups, bitsHex, commandScore, POW_0999, POW_MAX } from '../src/index.ts';

const golden = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../goldens/edge-cases.json'), 'utf8'));
const str = (v: string | { units: number[] }) => (typeof v === 'string' ? v : String.fromCharCode(...v.units));

for (const c of golden.cases) {
  test(`edge ${c.id}: ${c.row}`, () => {
    const item = str(c.item), q = str(c.q);
    if (c.error) {
      assert.throws(() => score(item, q), RangeError);
    } else {
      assert.equal(bitsHex(score(item, q)), c.bits, `expr ${c.expr}`);
    }
  });
}

for (const r of golden.rankCases) {
  test(`rank ${r.id}: ${r.row}`, () => {
    const out = rank(r.items, r.q);
    assert.deepEqual(out.ids, r.ids);
    assert.equal(out.selected, r.selected);
    if (r.tieGroups) assert.deepEqual(tieGroups(out.scores!), r.tieGroups);
    if (r.q === '') assert.equal(out.scores, null);
  });
}

test('keywords are concatenated with spaces (upstream behaviour; rank() never passes keywords)', () => {
  assert.equal(commandScore('open', 'fi', ['file', 'x']), commandScore('open file x', 'fi', []));
});

test('POW_0999 is frozen, covers 0..POW_MAX, POW[0] = 1', () => {
  assert.ok(Object.isFrozen(POW_0999));
  assert.equal(POW_0999.length, POW_MAX + 1);
  assert.equal(POW_0999[0], 1);
  assert.equal(POW_0999[1], 0.999);
  assert.ok(POW_MAX >= 4096);
});

test('score() rejects inputs beyond the POW table', () => {
  assert.throws(() => score('a'.repeat(POW_MAX + 1), 'a'));
  assert.doesNotThrow(() => score('a'.repeat(POW_MAX), 'a'));
});

test('tie equality is exact binary64 (no epsilon)', () => {
  const a = 0.1 + 0.2, b = 0.3;
  assert.deepEqual(tieGroups([a, b]).map((g) => g[1]), [1, 1]);
});
