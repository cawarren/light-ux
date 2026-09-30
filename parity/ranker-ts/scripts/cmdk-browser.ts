// Helpers to load the UNMODIFIED cmdk 1.1.1 dist scorer into a Chromium page.
import { build } from 'esbuild';
import path from 'node:path';
import { createRequire } from 'node:module';
import type { Page } from 'playwright';

const require = createRequire(import.meta.url);
/** Path to node_modules/cmdk/dist (cmdk@1.1.1, unmodified). */
export const CMDK_DIST = path.dirname(require.resolve('cmdk'));

/** IIFE that sets window.cmdkCommandScore = cmdk/dist commandScore (bundled from node_modules, unmodified). */
export async function cmdkScoreBundle(): Promise<string> {
  const entry = path.join(CMDK_DIST, 'command-score.mjs');
  const r = await build({
    stdin: { contents: `import { commandScore } from ${JSON.stringify(entry)}; window.cmdkCommandScore = commandScore;`, resolveDir: import.meta.dirname, loader: 'js' },
    bundle: true, write: false, format: 'iife', minify: false, target: 'es2020',
  });
  return r.outputFiles[0].text;
}

/** In-page batch scorer: returns hex bits or 'RangeError' per (item, query). */
export async function installBatchScorer(page: Page) {
  await page.addScriptTag({ content: await cmdkScoreBundle() });
  await page.evaluate(() => {
    const dv = new DataView(new ArrayBuffer(8));
    (window as any).scoreBatch = (pairs: [string, string][]) => pairs.map(([it, q]) => {
      try {
        dv.setFloat64(0, (window as any).cmdkCommandScore(it, q, []));
        return dv.getBigUint64(0).toString(16).padStart(16, '0');
      } catch (e) {
        if (e instanceof RangeError) return 'RangeError';
        throw e;
      }
    });
    (window as any).scoreAll = (items: string[], q: string) => {
      const out: string[] = [];
      try {
        for (const it of items) { dv.setFloat64(0, (window as any).cmdkCommandScore(it, q, [])); out.push(dv.getBigUint64(0).toString(16).padStart(16, '0')); }
        return out;
      } catch (e) { if (e instanceof RangeError) return 'RangeError'; throw e; }
    };
  });
}
