# R3 "No framework"

Spec rung R3 (docs/spec.md, "The ladder"): hand-written TypeScript on the DOM, a fixed pool of
recycled row nodes, CSS containment, no layout reads in the write path, and filtering in a Web
Worker. The layer removed relative to R2 is the framework and its virtual DOM. It follows
`rungs/shared/CONTRACT.md` and matches R1's look, pixel for pixel in the states tested below.

No UI framework and no runtime dependencies. The build uses esbuild (bundling and minifying),
TypeScript (type check only) and `@fontsource-variable/geist` (the same Geist woff2 files R1
ships, bundled as static assets). Nothing from `parity/` is imported at runtime.

## Files

| File | What |
| --- | --- |
| `index.html` | Static markup of the whole palette (input, listbox, empty state, live region). Loads `/ladder/probe.js` first, then the CSS and `assets/main.js` (module, deferred). |
| `src/main.ts` | Main thread: row pool, rendering, scheduling of worker queries, keyboard/pointer, `__ladder.results()`, marker flip. |
| `src/worker.ts` | Web Worker: fetches and lowercases the dataset once, ranks queries, keeps a small cache of recent results. |
| `src/scorer.ts` | Exact fast transliteration of cmdk's scorer, the normative ranking, `canNarrow`. |
| `src/vendor/command-score.ts` | cmdk 1.1.1 `command-score.ts` **verbatim** (with `Math.pow`), MIT. It is the fallback scorer and the test oracle. |
| `src/styles.css` | R1's computed styles written by hand, with light/dark via `prefers-color-scheme`. |
| `src/ladder-types.ts` | Copy of `rungs/shared/ladder-probe.d.ts`. |
| `build.mjs`, `serve.mjs` | esbuild build into `dist/`, and a static server that sends COOP/COEP/CORP. |
| `test/scorer.test.ts` | Offline tests: vendored SHA-256, fast scorer vs vendored, `rankIds` vs `ranker-ts` `rank()`, narrowing exactness. |
| `test/browser-supersession.ts` | In-page stress check of the supersession policy (input faster than the worker). |

## Architecture

**DOM.** The markup is static HTML. The list is `[data-ladder-list]` (role `listbox`, fixed
height). Inside it are a sizer (height = count × 32 px, which gives the scrollbar extent) and a
window (`translateY(start × 32 px)`) that holds up to **50 pooled rows**. The pool rows are
created once at startup. After that, only a row's text node (`Text.data`), its `data-id`,
`aria-selected`, `aria-posinset` and `aria-setsize` are rewritten, and only when the value
changes. Pool row *k* always shows result `start + k`, so DOM order equals rank order. Rows past
the end of the results are detached, so the attached rows are always a prefix of the pool. The
empty query shows all items in dataset order, but it still renders only 50 rows.

**Why 50 rows.** About 9 rows are visible (a 288 px list of 32 px rows). The probe's flip record
reads `top50` by walking the DOM from the first result row, and the parity check compares that
against the reference top 50. A virtualized list therefore needs the first 50 results in the DOM
when it flips. Fifty rows are also plenty of overscan (8 above, about 33 below).

**Containment and geometry.** `.list`, `.sizer` and `.item` have `contain: strict`; `.window`
has `contain: layout style`. Rows are `white-space: nowrap` with a fixed 32 px height. Every
height and offset is computed from the count and the row height and then written, never
measured.

- The list height is `min(count, 9) × 32`, or 68 px for "No results found.".
- `scrollTop` is tracked from `scroll` events. That handler is the only place layout is read, and
  it reads before it writes anything.
- Keyboard scrolling computes the new `scrollTop` arithmetically, using R1's `scroll-py-1`
  "nearest" rule, then writes it and re-renders the window in the same task.

**Worker.** The worker fetches the dataset itself (in parallel with the main thread's fetch, which
it needs for row text) and precomputes `formatInput(item)` once. For each query it returns the
**full** ranked id list as a transferred `Int32Array` (at most 200 kB at 50k). The main thread
keeps that list for rendering while scrolling and for `__ladder.results()`, so scrolling needs no
further round trips. Answering "only the visible window, fetch more on scroll" would save a copy
of at most 200 kB but would cost a round trip per scroll, so I did not do it.

**Scorer.** `src/scorer.ts` is a mechanical, value-preserving transliteration of cmdk's
`commandScoreInner`:

- the item is lowercased once instead of on every call;
- the memo is a flat `Float64Array` with generation stamps instead of an object keyed by strings;
- string compares use `charCodeAt`, with -1 standing for `charAt`'s `''`;
- the regexes become a 64K lookup table built from the same regexes;
- the `slice().match().length` counts become loops;
- `Math.pow(0.999, n)` becomes a table filled by this engine's `Math.pow`.

In the pinned Chromium that table is bit-identical to `parity/tables/pow0999.json`. The same
floating-point operations happen in the same order.

Strings whose lowercase changes UTF-16 length (U+0130 İ in Chromium 141, which the dataset bans)
fall back to the verbatim vendored function. Ranking uses a stable counting sort over the few
distinct scores, keyed by exact binary64 equality, so order is score descending, then id
ascending.

Headless Node timings at 50k: 8–27 ms per query warm, about 10× faster than the vendored
function. The ranking p50 is about 10 ms and p95 about 25 ms over 100 dev-1 queries.

**Incremental narrowing and caching (exact).**

- *Cache.* `rank(items, q)` is a pure function of `q`, so the worker keeps an LRU of recent
  answers (64 entries, at most 4M ids). Every backspace, and every retyped query, is a cache
  hit.
- *Narrowing.* When `canNarrow(p, q)` holds for a cached `p`, the worker scores only `p`'s hits
  and recomputes their scores and order from scratch. `canNarrow(p, q)` requires `q.startsWith(p)`,
  that `formatInput(q)` extends `formatInput(p)`, and that neither lowercase changes the length.
- *Proof sketch.* Whether a score is positive depends only on the lowercased strings. A positive
  match path for `q` either matches query unit `n-1` (where `n = p.length`) or skips it through
  the transposition branch from `n-2`. That branch's condition reads only units up to `n-1`,
  which are the same in `p`. Cut at that point, the path is a positive path for `p`.
- *Why `startsWith` alone is not enough.* The fuzz test found that it is unsound because of
  final sigma: `"- ΣΣ"` lowercases its last Σ to ς, but `"- ΣΣA"` lowercases it to σ.
  `canNarrow` refuses that case.
- *Evidence.* 3M fuzz cases (about 204k positive (item, q+ext) pairs) with no violation, and
  narrowed rankings equal full rankings along every typed prefix chain checked.

## Supersession policy (queued input)

The main thread is the only scheduler; the worker answers what it is sent, in order.

1. **At most one query in flight.** While the worker is busy, keystrokes only change the input's
   value. When a result comes back, the main thread sends the **latest** value, if it differs
   from what is (or is about to be) on screen. Intermediate values are never computed,
   displayed or flipped for.
2. **A result is displayed if it is newer than the screen.** Results are ordered by sequence
   number. A result is also displayed if it is older than the *input*: while typing continues
   faster than the worker, the list shows the most recent completed query, which is progress,
   not starvation.
   - It is dropped if the screen already matches the input. For example: type `b`, then
     Backspace, while `ab` is in flight. Neither change flips, because the list does not change.
3. **The empty query is answered on the main thread,** in the input task (all ids, dataset
   order), so clearing the box needs no worker. Any older result still in flight is then dropped
   by sequence number.
4. **At most one list update, and so one marker flip, per frame.** An update that would land in a
   frame that already has one waits for the next rAF callback, where only the newest waiting
   update is applied. Two flips in one frame would return the marker to its old colour in the
   presented frame while the list had changed. `test/browser-supersession.ts` found exactly this
   (the honesty report flagged `markerLast: false`) before the rule was added.
5. **No cancellation.** Without `SharedArrayBuffer` (an open decision, not used here), a running
   ranking cannot be interrupted short of chunking it and yielding, which would slow every
   query. Instead, coalescing bounds the queue.

**Consequence for latency.** A key typed while the worker is busy waits for at most the rest of
the in-flight ranking plus its own (usually a narrowed one, often a few ms). At about 100 ms per
key (A.seq), the 50k worker time (10–70 ms here) keeps up, so normally every key gets its own
flip.

**Consequence for the harness.** When keys arrive faster than the worker, some keys never get a
flip of their own. Their latency ends at the first flip whose query includes them. Phase A's
per-key trial matching should attribute a key to "the first flip at or after it whose query
extends or equals the key's query" (see "Harness notes").

## Contract (rungs/shared/CONTRACT.md)

| Point | How |
| --- | --- |
| `npm run build`, `npm run serve -- --port N` | `tsc` + `build.mjs` into `dist/`. `serve.mjs` serves `dist/` with `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp` and `Cross-Origin-Resource-Policy: same-origin`. The worker is same-origin, and `crossOriginIsolated === true`. |
| Probe first, dataset from `items`, `datasetLoaded` | The `<script src="/ladder/probe.js">` in `<head>` comes before the app. `?items=` is used as given (default `/dataset/items.json`). `datasetLoaded({url, count})` is called once the first 50 rows are rendered **and** the worker is ready to search. |
| DOM contract | `[data-ladder-input]`, `[data-ladder-list]`, and `[data-ladder-item]` with `data-id` and `aria-selected`; the row's `textContent` is exactly the item. |
| `results()` | `{ids, selected, query}` for what is on screen: the full ranked list as an array, the selected id, and the displayed query. |
| First result selected, empty query = all in dataset order | Done in `show()`. |
| Strict at every step, including backspace | The worker always returns a full normative ranking (cache hits are exact, narrowing is provably exact), and nothing depends on DOM history. |
| `markerFlip` once per query change, same frame, after the last list write | `show()` is the only function that changes the list for a query. It writes the list, then the input's `aria-activedescendant` and the live region, and calls `markerFlip(q, {count})` last, in the same task: a worker `message` task, the `input` task (empty query), or a rAF callback (deferred per rule 4). At most one flip per frame. The flip marks the **list** update, which usually comes a frame or more after the keystroke, because the worker answers in a later task. |

## Parity basics

- **Keyboard.** These match cmdk:
  - ArrowDown/ArrowUp, Ctrl+N/J and Ctrl+P/K move the selection without wrapping, as `loop` is
    off in R1;
  - Meta+Arrow goes to the last or first result;
  - Enter runs R1's visible no-op (`<html data-last-selected>`);
  - hovering or clicking a row selects it;
  - keys during IME composition (`isComposing` / 229) are left alone.

  The selection stays in view with R1's scroll padding. The screenshot after 12 × ArrowDown is
  pixel-identical to R1.

  Two keys deliberately differ from R1:
  - **Home and End are not intercepted**, so they edit the text input (cmdk moves the selection
    instead, 05 §0.3).
  - **PageUp and PageDown move the selection by 9 rows** (cmdk ignores them).

  Escape does nothing, as in R1 (see "Open questions").
- **Accessibility.**
  - The input is `role=combobox` with `aria-expanded`, `aria-controls`, `aria-autocomplete=list`,
    an `aria-label`, and `aria-activedescendant` pointing at the selected pooled row while that
    row is rendered.
  - The list is `role=listbox`, and rows are `role=option` with `aria-selected`,
    `aria-posinset` and `aria-setsize`, because the list is virtualized.
  - A polite `role=status` live region announces "N results" or "No results found." on each
    list update.
- **Text shaping.** Native DOM text in the same Geist font and fallback chain as R1.
- **Visual match.** I screenshot R1 (Vite) and R3 in 12 states, including 10k items, `open`,
  `open` + 3 × ArrowDown, no results, `sett` + 12 × ArrowDown (scrolled), and CJK, each in light
  and dark. All 12 pairs are **pixel-identical** (0 differing pixels).
  - One trap on the way: `will-change: transform` on the row window put the rows on their own
    compositor layer, which changed text rasterization (4–5% of pixels differed). It is gone.
  - Light and dark follow `prefers-color-scheme`, as R1's ThemeProvider does by default.

## Build, run, test

```sh
cd rungs/r3-no-framework && npm ci                 # esbuild, typescript, @fontsource-variable/geist
node ../scripts/prepare.mjs                         # dataset + probe into public/ (and dist/ if present)
npm run build                                       # tsc --noEmit, then esbuild -> dist/ (copies public/)
npm run serve -- --port 3104                        # http://localhost:3104/?items=/dataset/10k/items.json
npm run test:scorer                                 # offline scorer/narrowing tests (R3_FUZZ, R3_TEST_QUERIES)
node test/browser-supersession.ts http://localhost:3104 50k
cd ../tests && npx playwright test --project r3-no-framework --grep-invert @timing
cd ../tests && npx playwright test --project r3-no-framework --grep @timing --workers=1
```

`prepare.mjs` must run before `npm run build`, because the build copies `public/` into `dist/`.
Rebuilding wipes `dist/`, so do not rebuild while a suite is running against the server.

## Results (2026-09-30, headless Chromium 141, 4 vCPU, dataset dev-1)

**Shared parity suite** (`npx playwright test --project r3-no-framework --grep-invert @timing`,
defaults: 100 queries per size, 8 typed at 10k and 2 at 50k): **211 / 211 passed** in 1.9 min
(3 workers).

| Check | R3 |
| --- | --- |
| (a) COOP/COEP, `crossOriginIsolated`, probe and marker contract | pass |
| (b) strict, fresh mount + one fill, 100 queries × {10k, 50k} | 200 / 200 |
| (b) typed key by key and backspaced to empty, **strict on every step** (backspace included) | 10 / 10 sequences |
| (c) one flip per query change, same rAF frame as every list mutation, marker last; flip `top50` and `count` = reference | every change |
| (d) `aria-selected` row = first result = `rank().selected` | always |

**Other checks.**

- `npm run test:scorer` passes all 6 tests:
  - the vendored SHA-256;
  - the fast scorer is bit-identical to the vendored one on 10k × 1,000 dev-1 queries (10M pairs)
    and on 300k fuzz cases;
  - `rankIds` with the frozen table strict-equals `ranker-ts` `rank()` on 50k × 100 queries;
  - narrowing exactness (and 3M fuzz cases in a separate run).
- `test/browser-supersession.ts` at 50k: 12 / 12 input timelines pass (gaps of 0, 2 and 8 ms
  between input events, including clears and final sigma). Flips only move forward, every flip is
  honest, and the final list is strict-equal to `rank()`.
- The visual diff against R1 has 0 differing pixels in 12 / 12 states (see "Parity basics").

**Rough headless timings** (`--grep @timing --workers=1`, CDP input, software rendering: context
only, never a result). Times are ms from the `input` event's `timeStamp`. R1-vite ran in the same
session. Its 50k `sett` and `git br` rows failed there because the R1 preview server died
mid-run, so those two come from `rungs/README.md`.

| Rung | Size | Query (results) | → marker flip | → next rAF | → end of that frame's main-thread work | Mount (page load → ready) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| R1-vite | 10k | `open` (1,322) | 592 | 669 | 714 | 3.1 s |
| **R3** | 10k | `open` (1,322) | **9.5** | 10.0 | 22.7 | 0.41 s |
| R1-vite | 10k | `a` (7,971) | 623 | 949 | 1,202 | 3.2 s |
| **R3** | 10k | `a` (7,971) | **8.0** | 12.0 | 16.3 | 0.41 s |
| R1-vite | 10k | `sett` (4,208) | 537 | 790 | 954 | 3.1 s |
| **R3** | 10k | `sett` (4,208) | **19.4** | 24.9 | 32.4 | 0.42 s |
| R1-vite | 10k | `git br` (73) | 426 | 428 | 452 | 2.7 s |
| **R3** | 10k | `git br` (73) | **8.1** | 11.9 | 18.4 | 0.40 s |
| R1-vite | 50k | `open` (6,729) | 3,153 | 3,630 | 3,828 | 17.0 s |
| **R3** | 50k | `open` (6,729) | **39.3** | 43.6 | 53.4 | 0.42 s |
| R1-vite | 50k | `a` (40,292) | 2,823 | 3,320 | 4,137 | 16.4 s |
| **R3** | 50k | `a` (40,292) | **32.7** | 43.6 | 48.7 | 0.41 s |
| R1-vite (README) | 50k | `git br` (440) | 1,816 | 1,835 | 1,845 | 18.5 s |
| **R3** | 50k | `sett` (21,124) | **39.8** | 49.2 | 56.7 | 0.41 s |
| **R3** | 50k | `git br` (440) | **22.9** | 23.2 | 27.3 | 0.43 s |

The R3 flip time is almost all worker ranking; the main thread's part of the list update is well
under 1 ms. These are cold first queries on a fresh page (no cache and no narrowing, and the
worker's JIT is cold). Typed sequences are cheaper, because narrowing makes each forward key
score only the previous hits and each backspace is a cache hit. No long animation frames were
recorded for R3.

Element Timing `renderTime` sometimes comes out *earlier* than the flip (10k `sett`, 50k
`git br`). This looks like a probe or Element Timing quirk worth checking on the harness side; I
did not investigate it.

## Deviations from R1 (visible)

- **Long items are truncated, not wrapped.** Rows are fixed at 32 px, so an item too long for the
  526 px text box ends in an ellipsis. R1 wraps it to a 52 px row. `textContent` is still the full
  string. This affects 3 of the first 10,000 dev-1 items (0.03%).
- Home/End edit text, and PageUp/PageDown move the selection (see above).
- The list is virtualized: 50 rows are in the DOM, not all of them. `aria-setsize` and
  `aria-posinset` carry the full count.
- The live region is empty until the first query (R1 has none).
- Before the data loads, the list shows "No results found.", as R1 does.

## Harness notes (not changed here: harness-owned)

1. **Fixed 2026-09-30:** `marker.json`'s `resultSelector` is now `[data-ladder-item], [cmdk-item]`, so R3 no longer adds a `cmdk-item` attribute to its rows.
2. **`top50` reads rendered rows** (the correctness test now accepts fewer than 50, as long as they are the start of the ranking). The probe walks DOM siblings. A virtualized rung must
   therefore render at least 50 rows at flip time (R3's pool is sized for this), or the probe
   could read `window.__ladder.results()` when a rung defines it.
3. **Per-key trial matching under coalescing.** A queued key can have no flip of its own (policy
   above). Phase A §4.5 should attribute each key to the first flip at or after it whose query
   includes the key, and count "coalesced" keys separately rather than as timeouts.
4. **Fixed 2026-09-30** (a rung now joins only once its `package.json` has a `serve` script). **Running one project starts every rung's server.** `playwright.config.ts` starts a web server
   for every rung whose `package.json` exists, even with `--project`. While `r2-diligent` had a
   `package.json` but no `serve` script, `--project r3-no-framework` failed to start. I ran with
   `LADDER_R2_PORT=3104`, so Playwright reused R3's server for the R2 slot. Suggest filtering
   `webServer` by the selected project, or tolerating a missing server.

## Open questions

- **Escape.** It does nothing, as in R1. Should it clear the query? The spec checklist asks for
  Escape to "work", but R1 has no Escape behaviour outside `CommandDialog`.
- **Stale-but-newest results.** Displaying a result for a query older than the input (policy rule
  2) is a choice. The strict alternative, showing only results equal to the input, starves the
  list while typing outpaces the worker.
- **Live region chattiness.** The live region announces on every list update. A debounce would
  add a write outside the flip path. Left undebounced.
- **`SharedArrayBuffer`.** Not used. It would allow cancelling in-flight work and a zero-copy
  result.
