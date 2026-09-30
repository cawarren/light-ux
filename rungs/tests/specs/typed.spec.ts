/// <reference path="../../shared/ladder-probe.d.ts" />
// (b) typed key-by-key sequences, then backspaces. Typing (narrowing the query) must be at least
// tie-insensitive equal to rank() after every key (05 §0.1: displayed tie order depends on typing
// history). Also (c) one honest flip per key and (d) selected = first displayed result.
//
// KNOWN R1 DEVIATION (cmdk 1.1.1, not fixed: R1 is never optimized or patched): on a BACKSPACE
// (widening the query) items that re-appear are not sorted. cmdk's sort() runs inside
// setState('search') over the items currently in the DOM; re-appearing items are inserted later by
// React at their fiber position, and their one-time registration (useLayoutEffect with []) does not
// schedule another sort. So after a backspace R1 can show a lower-scored item above a higher-scored
// one (not just a tie-order difference). Backspace steps therefore assert the same result SET and
// selected = first displayed item; order conformance is recorded (annotation `result`) and only
// enforced with LADDER_STRICT_BACKSPACE=1.
import { test, expect } from '@playwright/test';
import { check, dataset, expectHonestFlip, openPalette, queries, queryChange, readResults, resolveResults, r1Leniency, SEL } from './helpers.ts';

function typedQueries(count: number, offset: number) {
  // Typeable queries (US layout, lower-case ASCII, digits, space): ascii-20 and multi-word prefixes.
  const qs = queries().filter((q) => q.typeable && (q.class === 'ascii-20' || q.class === 'multi-word-prefix'));
  const a20 = qs.filter((q) => q.class === 'ascii-20');
  const mw = qs.filter((q) => q.class === 'multi-word-prefix');
  const out = [];
  for (let i = 0; out.length < count; i++) out.push(i % 2 ? mw[offset + i] : a20[offset + i]);
  return out;
}

const PLAN = [
  { size: '10k' as const, n: 10_000, qs: typedQueries(Number(process.env.LADDER_TYPED_10K ?? 8), 0) },
  { size: '50k' as const, n: 50_000, qs: typedQueries(Number(process.env.LADDER_TYPED_50K ?? 2), 20) },
];

for (const { size, n, qs } of PLAN) {
  test.describe(`typed ${size}`, () => {
    for (const { qid, q } of qs) {
      test(`q${qid} ${JSON.stringify(q)}`, async ({ page }) => {
        const { items, idOf } = dataset(n);
        await openPalette(page, size);
        await page.focus(SEL.input);
        const steps: string[] = [];
        for (let k = 1; k <= q.length; k++) steps.push(q.slice(0, k));
        for (let k = q.length - 1; k >= 0; k--) steps.push(q.slice(0, k));
        let strictHits = 0;
        const backspaceOrderFailures: { step: number; code: string; rank?: number }[] = [];
        for (let s = 0; s < steps.length; s++) {
          const want = steps[s];
          const c = await queryChange(page, () =>
            s < q.length ? page.keyboard.type(q[s]) : page.keyboard.press('Backspace'));
          const dom = await readResults(page);
          expect(dom.inputValue).toBe(want);
          const { ids, selected, selectedIndex } = resolveResults(dom, items, idOf);
          const strict = check('strict', items, want, `${qid}/${s}`, ids, selected).verdict.pass;
          if (strict) strictHits++;
          const { verdict, ref } = check('tie-insensitive', items, want, `${qid}/${s}`, ids, selected);
          const msg = `step ${s} ${JSON.stringify(want)}: ${verdict.code} at rank ${verdict.firstDivergentRank}`;
          if (!r1Leniency(test.info().project.name)) {
            // R2 and above: strict on every step, backspace included (decided 2026-09-30).
            expect(strict, `${msg} (strict required for ${test.info().project.name})`).toBe(true);
          } else if (s < q.length || process.env.LADDER_STRICT_BACKSPACE === '1') {
            expect(verdict.pass, msg).toBe(true);
          } else {
            expect([...ids].sort((a, b) => a - b), `${msg}: same result set`).toEqual([...ref.ids].sort((a, b) => a - b));
            if (!verdict.pass) backspaceOrderFailures.push({ step: s, code: verdict.code, rank: verdict.firstDivergentRank });
          }
          expect(selectedIndex).toBe(ids.length ? 0 : -1);
          expectHonestFlip(c, want);
        }
        test.info().annotations.push({ type: 'result', description: JSON.stringify({ size, qid, steps: steps.length, strictHits, backspaceOrderFailures }) });
        if (backspaceOrderFailures.length) console.log(`[${test.info().project.name}] ${size} q${qid}: backspace order failures ${JSON.stringify(backspaceOrderFailures)}`);
      });
    }
  });
}
