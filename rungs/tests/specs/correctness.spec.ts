/// <reference path="../../shared/ladder-probe.d.ts" />
// (b) strict correctness on fresh mount + one fill, (c) marker honesty, (d) selected = first.
// One fresh page per query: cmdk's displayed order depends on typing history (05 §0.1), and a
// fresh mount with the whole query in one input event is the state where R1 must be STRICT.
import { test, expect } from '@playwright/test';
import { check, dataset, expectHonestFlip, openPalette, queryChange, readResults, resolveResults, strictQueries, SEL, SIZES } from './helpers.ts';

for (const size of Object.keys(SIZES) as (keyof typeof SIZES)[]) {
  test.describe(`strict ${size}`, () => {
    for (const { qid, q, class: cls } of strictQueries()) {
      test(`q${qid} ${cls}`, async ({ page }) => {
        const { items, idOf } = dataset(SIZES[size]);
        await openPalette(page, size);
        const c = await queryChange(page, () => page.fill(SEL.input, q));
        const dom = await readResults(page);
        expect(dom.inputValue).toBe(q);
        const { ids, selected, selectedIndex } = resolveResults(dom, items, idOf);
        const { verdict, ref } = check('strict', items, q, qid, ids, selected);
        expect(verdict.code, `strict vs rank(): first divergence at ${verdict.firstDivergentRank}`).toMatch(/^ok/);
        // (d) the selected item is the first result (and none when empty).
        expect(selectedIndex).toBe(ref.ids.length ? 0 : -1);
        expect(selected).toBe(ref.selected);
        // (c) one flip, same frame as the list, marker last; its top-50 matches the reference.
        expectHonestFlip(c, q);
        // The flip snapshot reads rendered rows, so a virtualized rung may show fewer than 50; they must be the start of the ranking.
        expect(c.flip.top50.length, 'flip snapshot has rows when there are results').toBe(ref.ids.length ? Math.max(1, c.flip.top50.length) : 0);
        expect(c.flip.top50).toEqual(ref.ids.slice(0, c.flip.top50.length).map((id) => items[id]));
        expect(c.flip.count).toBe(ref.ids.length);
        test.info().annotations.push({ type: 'result', description: JSON.stringify({ size, qid, count: ref.ids.length, fillMs: c.wallMs }) });
      });
    }
  });
}
