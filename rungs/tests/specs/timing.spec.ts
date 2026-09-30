/// <reference path="../../shared/ladder-probe.d.ts" />
// @timing: ROUGH headless numbers for context only (Playwright-launched Chromium, CDP input,
// software rendering, no real display). Never a headline number (docs/phase-a/README.md §0.6).
// Per fill: input event timeStamp -> marker flip (end of React/cmdk work) -> first rAF after the
// flip (next frame start) -> post-frame task (end of that frame's rendering update on the main
// thread) -> Element Timing entry of the flip's probe span (paintTime/presentationTime if any).
import fs from 'node:fs';
import path from 'node:path';
import { test } from '@playwright/test';
import { openPalette, queryChange, SEL } from './helpers.ts';

const FILLS = ['open', 'a', 'sett', 'git br'];
const results: Record<string, unknown>[] = [];

for (const size of ['10k', '50k'] as const) {
  for (const q of FILLS) {
    test(`@timing ${size} fill ${JSON.stringify(q)}`, async ({ page }, info) => {
      const t0 = Date.now();
      await openPalette(page, size);
      const mountMs = Date.now() - t0;
      const c = await queryChange(page, () => page.fill(SEL.input, q));
      await page.waitForTimeout(300);
      const r = await page.evaluate((seq) => {
        const L = window.__ladder!;
        const flip = L.flips.find((f) => f.seq === seq)!;
        const input = [...L.inputs].reverse().find((e) => e.type === 'input' && e.t <= flip.t)!;
        const frame = L.frames.find((f) => f.raf >= flip.t);
        const el = L.entries.element.find((e) => e.identifier === `ladder-flip-${seq}`);
        return {
          count: flip.count,
          inputToFlipMs: flip.t - input.t,
          inputToNextRafMs: frame ? frame.raf - input.t : null,
          inputToPostFrameMs: frame && frame.post ? frame.post - input.t : null,
          elementTiming: el ? {
            renderTimeMs: el.renderTime ? el.renderTime - input.t : null,
            paintTimeMs: el.paintTime ? el.paintTime - input.t : null,
            presentationTimeMs: el.presentationTime ? el.presentationTime - input.t : null,
          } : null,
          loafs: L.entries.loaf.filter((l: any) => l.startTime + l.duration >= input.t).length,
        };
      }, c.flip.seq);
      const row = { rung: info.project.name, size, q, mountMs, fillWallMs: c.wallMs, ...r };
      results.push(row);
      console.log(JSON.stringify(row));
    });
  }
}

test.afterAll(async ({}, info) => {
  const dir = path.resolve(import.meta.dirname, '../out');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `timing-${info.project.name}.json`), JSON.stringify(results, null, 1) + '\n');
});
