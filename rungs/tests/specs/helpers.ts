// Shared helpers for the rung parity specs.
/// <reference path="../../shared/ladder-probe.d.ts" />
import fs from 'node:fs';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { rank, type Ranking } from '@latency-ladder/ranker-ts';
import { goldenFromRanking } from '../../../parity/ranker-ts/src/golden.ts';
import { conformQuery, type Level, type Verdict } from '../../../parity/ranker-ts/src/conform.ts';

export const SEED = process.env.LADDER_SEED ?? 'dev-1';
const OUT = path.resolve(import.meta.dirname, '../../../dataset/out', SEED);
/** Rung-neutral DOM contract (rungs/shared/CONTRACT.md). R1 uses cmdk's own attributes. */
export const SEL = {
  input: '[data-ladder-input], [cmdk-input]',
  list: '[data-ladder-list], [cmdk-list]',
  item: '[data-ladder-item], [cmdk-item]',
};
/** Correctness level for a project: R1 is tie-insensitive when typing (and set-only on backspace); R2+ strict everywhere. */
export function r1Leniency(projectName: string): boolean { return projectName.startsWith('r1'); }

export const SIZES = { '10k': 10_000, '50k': 50_000 } as const;
export type SizeName = keyof typeof SIZES | '1k';

const itemCache = new Map<number, { items: string[]; idOf: Map<string, number> }>();
export function dataset(n: number) {
  let d = itemCache.get(n);
  if (!d) {
    const items: string[] = JSON.parse(fs.readFileSync(path.join(OUT, String(n), 'items.json'), 'utf8')).items;
    d = { items, idOf: new Map(items.map((t, i) => [t, i])) };
    itemCache.set(n, d);
  }
  return d;
}

export interface Query { qid: number; q: string; class: string; typeable: boolean }
export function queries(): Query[] {
  return JSON.parse(fs.readFileSync(path.join(OUT, 'queries.json'), 'utf8')).queries;
}
/** ~100 queries: every 10th of the shared dev-1 set (all 14 classes are represented). */
export function strictQueries(): Query[] {
  const n = Number(process.env.LADDER_QUERIES ?? 100);
  const all = queries();
  const step = Math.max(1, Math.floor(all.length / n));
  return all.filter((_, i) => i % step === 0).slice(0, n);
}

/** Open the palette on a fresh page and wait until all items are rendered and settled. */
export async function openPalette(page: Page, size: SizeName) {
  const n = size === '1k' ? 1000 : SIZES[size];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`/?items=/dataset/${size}/items.json`);
  await page.waitForFunction((n) => window.__ladder?.dataset?.count === n, n, { timeout: 300_000, polling: 250 });
  await settle(page);
  await page.evaluate((sel) => window.__ladder!.watchList(sel), SEL.list);
  return { n, errors };
}

/** Two rAFs plus a macrotask: everything scheduled by the last update has run and painted. */
export async function settle(page: Page) {
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 20)))));
}

export interface Change { flipsAdded: number; flip: any; honesty: any; wallMs: number }
/** Run an input action that changes the query once; wait for its flip; settle; report. */
export async function queryChange(page: Page, action: () => Promise<void>): Promise<Change> {
  const n0 = await page.evaluate(() => window.__ladder!.flips.length);
  const t0 = Date.now();
  await action();
  await page.waitForFunction((n0) => window.__ladder!.flips.length > n0, n0, { timeout: 300_000, polling: 'raf' });
  const wallMs = Date.now() - t0;
  await settle(page);
  await settle(page);
  return page.evaluate(({ n0, wallMs }) => {
    const L = window.__ladder!;
    const flip = L.flips[L.flips.length - 1];
    const honesty = L.honestyReport().find((h) => h.seq === flip.seq);
    return { flipsAdded: L.flips.length - n0, flip, honesty, wallMs };
  }, { n0, wallMs });
}

export interface DomResults {
  texts: string[]; domIds: (number | null)[]; selected: string | null; selectedIndex: number; inputValue: string;
  /** Full result list reported by a virtualized rung via window.__ladder.results(); null otherwise. */
  full: { ids: number[]; selected: number | null; query: string } | null;
}
export async function readResults(page: Page): Promise<DomResults> {
  return page.evaluate((SEL) => {
    const els = [...document.querySelectorAll<HTMLElement>(SEL.item)];
    const selectedIndex = els.findIndex((e) => e.getAttribute('aria-selected') === 'true');
    const L = window.__ladder as any;
    const full = typeof L?.results === 'function' ? L.results() : null;
    return {
      texts: els.map((e) => e.textContent ?? ''),
      domIds: els.map((e) => (e.dataset.id === undefined ? null : Number(e.dataset.id))),
      selected: selectedIndex >= 0 ? els[selectedIndex].textContent : null,
      selectedIndex,
      inputValue: (document.querySelector(SEL.input) as HTMLInputElement).value,
      full: full ? { ids: [...full.ids], selected: full.selected ?? null, query: full.query } : null,
    };
  }, SEL);
}

/**
 * Resolve the displayed ranking. Non-virtualized rungs: every result is in the DOM. Virtualized rungs
 * report the full list via __ladder.results(); the rows actually on screen must then be exactly the
 * start of that list (tests read at scrollTop 0), with matching text, and the selected row must agree,
 * so a rung cannot report one ranking and display another.
 */
export function resolveResults(dom: DomResults, items: string[], idOf: Map<string, number>): { ids: number[]; selected: number | null; selectedIndex: number } {
  const rowIds = dom.texts.map((t, i) => dom.domIds[i] ?? idOf.get(t));
  rowIds.forEach((id, i) => {
    if (id === undefined) throw new Error(`DOM item not in dataset: ${JSON.stringify(dom.texts[i])}`);
    expect(dom.texts[i], `row ${i} text matches dataset item ${id}`).toBe(items[id]);
  });
  if (!dom.full) {
    const selected = dom.selected === null ? null : idOf.get(dom.selected)!;
    return { ids: rowIds as number[], selected, selectedIndex: dom.selectedIndex };
  }
  const { ids, selected } = dom.full;
  expect(dom.full.query, 'results() query matches the input').toBe(dom.inputValue);
  expect(rowIds.length, 'virtualized rung renders some rows when there are results').toBe(ids.length ? Math.max(1, rowIds.length) : 0);
  expect(rowIds, 'rows on screen are the start of the reported ranking').toEqual(ids.slice(0, rowIds.length));
  const selectedIndex = selected === null ? -1 : ids.indexOf(selected);
  if (selectedIndex >= 0 && selectedIndex < rowIds.length) expect(dom.selectedIndex, 'selected row on screen agrees with results()').toBe(selectedIndex);
  return { ids, selected, selectedIndex };
}

export function toIds(texts: string[], idOf: Map<string, number>): number[] {
  return texts.map((t) => {
    const id = idOf.get(t);
    if (id === undefined) throw new Error(`DOM item not in dataset: ${JSON.stringify(t)}`);
    return id;
  });
}

export function check(level: Level, items: string[], q: string, qid: number | string, ids: number[], selected: number | null): { verdict: Verdict; ref: Ranking } {
  const ref = rank(items, q);
  const verdict = conformQuery(goldenFromRanking(qid, ref), { qid, ids, selected }, level, () => ref);
  return { verdict, ref };
}

/** Marker assertions shared by every query change. */
export function expectHonestFlip(c: Change, q: string) {
  expect(c.flipsAdded, `exactly one marker flip for query change to ${JSON.stringify(q)}`).toBe(1);
  expect(c.flip.query).toBe(q);
  expect(c.honesty, 'honesty row for the flip').toBeTruthy();
  expect(c.honesty.sameFrame, `list and marker in the same frame (stray frames ${c.honesty.strayFrames})`).toBe(true);
  expect(c.honesty.markerLast, `marker is the last DOM write (late list mutations: ${c.honesty.late})`).toBe(true);
}
