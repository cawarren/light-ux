# rungs/: web rungs of the Latency Ladder

| Path | What | Owner |
| --- | --- | --- |
| `shared/` | Latency marker + in-page probe (`ladder-probe.js`, `marker.json`, types). Framework-free. | Harness |
| `r1-typical/` | R1 "Typical": Next.js 16 + shadcn Command (cmdk 1.1.1), production build. Standalone app, own lockfile. | Frozen (never optimized) |
| `r1-vite/` | R1 on Vite 8 + React 19.3 from the same `Palette`, for Next vs Vite. Standalone app, own lockfile. | Frozen |
| `scripts/prepare.mjs` | Generates dataset dev-1 (`npm run -w dataset gen`) and copies 1k/10k/50k `items.json` (+ `queries.json`) and the probe into each rung's `public/` (gitignored). | Harness |
| `scripts/check-sync.mjs` | Checks the files the two R1 variants share are identical. | Harness |
| `tests/` | npm workspace `@latency-ladder/rung-tests`: Playwright 1.56.1 parity specs for both R1 variants against their production builds. | Harness |

The rung apps are **not** npm workspaces: each is what a team would ship (its own
`package-lock.json`), and workspace hoisting would otherwise mix their React with the React
`parity/ranker-ts` pins for its oracle. Only `rungs/tests` is a root workspace.

## Commands (repo root)

```sh
npm install
npm run -w @latency-ladder/rung-tests install:rungs   # npm ci in r1-typical, r1-vite
npm run -w @latency-ladder/rung-tests build:rungs     # prepare (dataset + probe) -> next build -> vite build
npm run -w @latency-ladder/rung-tests test            # check-sync + all parity specs (~1 h, 3 workers)
npm run -w @latency-ladder/rung-tests test:quick      # 10 queries/size, 2 typed sequences
npm run -w @latency-ladder/rung-tests test:timing     # rough headless fill timings, 1 worker
npm run -w @latency-ladder/rung-tests typecheck
```

Playwright starts `next start -p 3101` and `vite preview --port 3102` itself (or reuses running
ones). Chromium comes from `/opt/pw-browsers` (chromium-1194 = Chromium 141, the build the ranker
tables were made in); never `playwright install`. Env knobs: `LADDER_QUERIES` (default 100),
`LADDER_TYPED_10K` (8), `LADDER_TYPED_50K` (2), `LADDER_WORKERS` (3), `LADDER_STRICT_BACKSPACE=1`,
`LADDER_SEED` (dev-1).

## What the specs check (per variant)

| Spec | Check |
| --- | --- |
| `isolation` | (a) COOP/COEP headers, `crossOriginIsolated === true`, probe installed, marker at the configured device-pixel rect, black at start, white/black flips, no transition, `?ladderMarker=` override |
| `correctness` | (b) fresh page + one `fill()` per query, 100 dev-1 queries (every 10th of the shared 1,000, all 14 classes) on 10k and 50k: DOM order **strict**-equal to `ranker-ts` `rank()`; (c) exactly one flip, in the same rAF frame as all list mutations and after the last one, flip `top50`/`count` equal to the reference; (d) `aria-selected` item = first result = `rank().selected` |
| `typed` | (b) typeable queries typed key by key, then backspaced to empty, checked after every key: forward keys **tie-insensitive** (via `conform`), backspace keys same result set (order recorded, see below); (c) and (d) after every key |
| `timing` (`@timing`) | Rough headless input→flip→next frame times for one fill at 10k and 50k |

## Results (2026-09-30, headless Chromium 141.0.7390.37, 4 vCPU, dataset dev-1)

`npm test`: **422 / 422 passed** in 33.8 min (3 workers), identical outcomes for both variants.

| Check | r1-typical (Next) | r1-vite |
| --- | --- | --- |
| (a) `crossOriginIsolated`, marker contract | pass | pass |
| (b) strict, fresh mount + one fill, 100 queries × {10k, 50k} (92 / 93 non-empty) | 200 / 200 | 200 / 200 |
| (b) typed forward, 8 queries @10k + 2 @50k, every key | all tie-insensitive (185 keys) | same |
| (b) backspace steps: same result **set** | 185 / 185 | 185 / 185 |
| (b) backspace steps: order tie-insensitive | **fails on 106 / 185** (known cmdk bug, below) | same |
| (c) one flip per query change, same rAF frame as every list mutation, marker last | every change (200 fills + 370 keys) | same |
| (c) flip `top50` and `count` = reference (fresh mounts) | 200 / 200 | 200 / 200 |
| (d) `aria-selected` = first displayed item (= `rank().selected` on fresh mounts) | always | always |

**Known R1 deviation (cmdk 1.1.1).** After a backspace, items that re-appear are inserted by React
at their original position and never re-sorted (cmdk sorts only the DOM nodes present inside
`setState('search')`, and item registration runs once per mount). The result set is right, but
the order is wrong beyond ties (for example an item scored 0.151 above one scored 0.891), and after
clearing the query the list is not in dataset order. The typed spec records these steps and fails
on them only with `LADDER_STRICT_BACKSPACE=1`. Phase-a §4.5 compares every A.seq trial
(including the 20 backspaces) tie-insensitively for R1, so as specified R1 would log
`wrong_result` on most backspace trials and be non-parity.

**Decided 2026-09-30:** R1's correctness check covers fresh-mount (pasted) queries, strictly, and forward typing, tie-insensitively. On backspace steps R1 must return the right result **set**; the order bug is recorded as a known R1 finding, not a parity failure, because it is what stock shadcn/cmdk ships and R1 is never optimized. R2 and above must match the reference strictly on every step, backspace included.

**Rough headless timings** (`npm run test:timing`, 1 worker, software rendering, CDP input: context
only, never a result). ms from the `input` event's `timeStamp`:

| Variant | Size | Query (results) | → marker flip | → next rAF | → end of that frame's main-thread work | Mount of the list |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Next | 10k | `open` (1,322) | 460 | 523 | 556 | 2.1 s |
| Vite | 10k | `open` (1,322) | 331 | 390 | 422 | 2.3 s |
| Next | 50k | `open` (6,729) | 2,739 | 3,026 | 3,224 | 19.5 s |
| Vite | 50k | `open` (6,729) | 1,984 | 2,371 | 2,529 | 18.6 s |
| Next | 50k | `a` (40,292) | 2,679 | 3,082 | 3,948 | 18.5 s |
| Vite | 50k | `a` (40,292) | 2,680 | 3,598 | 4,462 | 17.5 s |
| Next | 50k | `git br` (440) | 2,814 | 2,825 | 2,855 | 18.4 s |
| Vite | 50k | `git br` (440) | 1,816 | 1,835 | 1,845 | 18.5 s |

These are single samples on a shared 4-vCPU box; do not read Next-vs-Vite differences from them. Element Timing entries
arrive for every flip with `renderTime` (≈ next rAF + 10–20 ms) but no
`paintTime`/`presentationTime` in Chromium 141. Full rows: `tests/out/timing-*.json`.
