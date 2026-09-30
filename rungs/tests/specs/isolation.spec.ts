/// <reference path="../../shared/ladder-probe.d.ts" />
// (a) Cross-origin isolation + probe/marker contract, on the production build.
import { test, expect } from '@playwright/test';
import { openPalette } from './helpers.ts';

test('cross-origin isolated, probe installed, marker geometry and colours', async ({ page }) => {
  const res = await page.request.get('/');
  expect(res.headers()['cross-origin-opener-policy']).toBe('same-origin');
  expect(res.headers()['cross-origin-embedder-policy']).toBe('require-corp');
  const { errors } = await openPalette(page, '1k');
  const s = await page.evaluate(async () => {
    const L = window.__ladder!;
    await L.ready;
    const m = document.getElementById('ladder-marker')!;
    const r = m.getBoundingClientRect();
    const cs = getComputedStyle(m);
    return {
      coi: self.crossOriginIsolated, probeCoi: L.crossOriginIsolated, dpr: devicePixelRatio,
      rect: [r.left, r.top, r.width, r.height], bg: cs.backgroundColor, transition: cs.transitionDuration,
      cfg: L.config.marker, dataset: L.dataset, items: document.querySelectorAll('[cmdk-item]').length,
      errors: L.errors,
    };
  });
  expect(s.coi).toBe(true);
  expect(s.probeCoi).toBe(true);
  expect(s.errors).toEqual([]);
  expect(errors).toEqual([]);
  expect(s.dataset).toEqual({ url: '/dataset/1k/items.json', count: 1000 });
  expect(s.items).toBe(1000);
  const { x, y, size } = s.cfg;
  expect(s.rect).toEqual([x / s.dpr, y / s.dpr, size / s.dpr, size / s.dpr]);
  expect(s.bg).toBe('rgb(0, 0, 0)');
  expect(s.transition).toBe('0s');
  // The marker flips to white on the first query change, back to black on the next.
  await page.fill('[cmdk-input]', 'op');
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.getElementById('ladder-marker')!).backgroundColor)).toBe('rgb(255, 255, 255)');
  await page.fill('[cmdk-input]', 'o');
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.getElementById('ladder-marker')!).backgroundColor)).toBe('rgb(0, 0, 0)');
  // URL override of the marker rect (device px).
  await page.goto('/?items=/dataset/1k/items.json&ladderMarker=40,60,64');
  const r2 = await page.evaluate(() => { const r = document.getElementById('ladder-marker')!.getBoundingClientRect(); return [r.left, r.top, r.width, r.height, devicePixelRatio]; });
  expect(r2.slice(0, 4)).toEqual([40 / r2[4], 60 / r2[4], 64 / r2[4], 64 / r2[4]]);
});
