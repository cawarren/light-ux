import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// The vendored file must be upstream cmdk v1.1.1 cmdk/src/command-score.ts byte-for-byte,
// except Math.pow(PENALTY_SKIPPED, n) -> POW_0999[n].
const UPSTREAM_SHA256 = 'ccfd0d66e3d31b8197fc4dbb217c9672e7562569775ec3a4f3b7e567eb81dfc7';

test('vendored command-score.ts == upstream v1.1.1 modulo the POW_0999 substitution', () => {
  const src = fs.readFileSync(path.resolve(import.meta.dirname, '../src/vendor/command-score.ts'), 'utf8');
  const marker = '// ---- BEGIN VERBATIM UPSTREAM (modulo POW_0999) ----\n';
  const body = src.slice(src.indexOf(marker) + marker.length);
  assert.equal((body.match(/POW_0999\[/g) ?? []).length, 3);
  assert.ok(!body.includes('Math.pow'));
  const restored = body.replace(/POW_0999\[([^\]]+)\]/g, 'Math.pow(PENALTY_SKIPPED, $1)');
  assert.equal(createHash('sha256').update(restored).digest('hex'), UPSTREAM_SHA256);
  assert.ok(src.includes('Copyright (c) 2022 Paco Coursey'));
});
