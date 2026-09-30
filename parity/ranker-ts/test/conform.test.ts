import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rank } from '../src/index.ts';
import { goldenLine, idsSha256, type GoldenLine } from '../src/golden.ts';
import { conformQuery } from '../src/conform.ts';

const items = ['zz open', 'open x', 'xopen', 'nothing', 'aa open', 'bb open'];
const q = 'open';
const g = goldenLine(items, 7, q) as GoldenLine;
const ref = () => rank(items, q);

test('golden line shape', () => {
  assert.deepEqual(Object.keys(g), ['qid', 'count', 'idsSha256', 'selected', 'top', 'tieGroups']);
  assert.equal(g.count, 5);
  assert.equal(g.selected, 1);
  assert.equal(g.idsSha256, idsSha256([1, 0, 4, 5, 2]));
  assert.deepEqual(g.tieGroups.map((t) => t[1]), [1, 3, 1]);
  assert.equal(idsSha256([]), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
});

test('strict: exact order passes, tie swap fails', () => {
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 0, 4, 5, 2] }, 'strict').pass, true);
  const v = conformQuery(g, { qid: 7, ids: [1, 4, 0, 5, 2] }, 'strict', ref);
  assert.equal(v.pass, false);
  assert.equal(v.code, 'order');
  assert.equal(v.firstDivergentRank, 1);
});

test('tie-insensitive: permutation within tie group passes, across groups fails', () => {
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 5, 4, 0, 2] }, 'tie-insensitive', ref).pass, true);
  assert.equal(conformQuery(g, { qid: 7, ids: [0, 1, 4, 5, 2] }, 'tie-insensitive', ref).code, 'tie-set');
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 5, 4, 0, 2] }, 'tie-insensitive').code, 'needs-reference');
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 5, 5, 0, 2] }, 'tie-insensitive', ref).code, 'duplicate');
});

test('count / selected / missing', () => {
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 0, 4, 5] }, 'strict').code, 'count');
  assert.equal(conformQuery(g, { qid: 7, ids: [1, 0, 4, 5, 2], selected: 0 }, 'strict').code, 'selected');
  assert.equal(conformQuery(g, undefined, 'strict').code, 'missing');
});
