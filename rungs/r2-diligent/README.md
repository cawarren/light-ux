# R2 "Diligent"

R1 plus the care a diligent team would take, and nothing else (docs/spec.md, "The ladder"): **list
virtualization, memoization, deferred rendering, a precomputed search index**, using only the
documented React practices in [ALLOWLIST.md](ALLOWLIST.md). Same stack, same shadcn Command look
(light/dark per OS), same keyboard behaviour, same ranking, but **strict** at every step,
backspace included (R1's cmdk re-sort bug does not exist here).

## Stack, and the baseline to diff against

**Vite 8 + React 19.3, production build** (`vite build`, served by `vite preview`), started from a
copy of `rungs/r1-vite`. **Diff R2 against `r1-vite`, not `r1-typical`**: same toolchain, same
React, same shadcn files, same Tailwind/theme, so the R1→R2 delta is the techniques, not Next vs
Vite. Why Vite rather than Next: the palette is a client-only component with runtime data; after
first render Next and Vite run identical React code (05 §3.2), so Next would only add router/RSC
cold-start cost that R2's techniques do not touch and that the R1 pair already measures. The only
new runtime dependency is `@tanstack/react-virtual` **3.14.13** (pinned); `cmdk` 1.1.1 is kept as a
dev dependency to check the vendored scorer against it.

### Why not cmdk's components (allow-list A12)

R2 keeps shadcn's `command.tsx` **classes and markup** but renders them on plain elements
(`src/components/ui/command.tsx`), with the ARIA roles and `data-*` attributes cmdk renders
(combobox input, listbox, options with `aria-selected`/`data-selected`, empty label). Keeping
cmdk with `shouldFilter={false}` was the first option and was rejected because cmdk's selection
and keyboard logic read the **DOM** (`querySelectorAll` over mounted items), which virtualization
and deferred rendering both break:

- `End` / `Meta+↓` would select the last *mounted* row instead of the last result;
- after a query change cmdk selects the first item in a layout effect of the *input's* commit; with
  `useDeferredValue` the new rows commit later, so it would select the old first row;
- item registration, value/`textContent` bookkeeping and a store subscription per row are work R2
  exists to remove, and selection is keyed by the item string (trimmed), not by id.

Owning ~40 lines of keyboard handling (same keys as cmdk 1.1.1 with default props) was simpler and
exact. Screenshots of R1 and R2 are pixel-identical (light and dark, 1k, query `fix`, one arrow
press, including a wrapped two-line row).

## Techniques used (mapped to ALLOWLIST.md)

| Change vs R1 | Allow-list | Where |
| --- | --- | --- |
| Production build, same toolchain as `r1-vite` | A1 | `package.json`, `vite.config.ts` |
| Rows virtualized with **TanStack Virtual** (`useVirtualizer`), 50 rows of overscan, **dynamic row measurement** (`measureElement`) so rows that wrap to two lines look as in R1, row window offset with `transform` | A5, A8 | `ResultList` in `src/components/palette.tsx` |
| Virtualizer viewport = the list's `max-height` (`observeElementRect`), and a synchronous scroll-offset report after a scroll reset (`observeElementOffset`), so all list writes for a query land in one commit (see "Marker") | A5 (documented options), A10 | `observeListViewport`, `observeOffset` |
| `aria-setsize` / `aria-posinset` on the rendered options | A6 | `Row` |
| `useDeferredValue(query)`: the input commits immediately, the list renders the new query in an interruptible background render | A4 | `Palette` |
| `React.memo` on the list and rows, `useMemo` for the ranking, `useCallback` for row handlers | A2 | `Palette`, `ResultList`, `Row` |
| Rows keyed by dataset id; the virtualizer's size cache keyed by id (`getItemKey`) | A3 | `ResultList` |
| **Search index** built once when the dataset arrives (in the fetch callback, not in render): each item's `formatInput()` (lower-cased, whitespace-normalized) string and a 64-bit character-bucket mask; exact pruning rules P1 and P2 below | A7 | `src/search/search-index.ts` |
| Memo cache (LRU, 32 queries) of `search(q)`: backspace is a cache hit, forward typing narrows from the cached prefix | A2, A7 | `SearchIndex.search` |
| Selection reset to the first result **during render** when the results change (same commit as the rows) | A9 | `Palette` |
| Controlled input; `scrollToIndex` exposed with `useImperativeHandle`; marker flip and scroll reset in `useLayoutEffect` | A11, A10 | `Palette`, `ResultList` |

Ranking uses **cmdk 1.1.1's own scorer, vendored byte-for-byte** (`src/search/command-score.ts`,
upstream SHA-256 `ccfd0d66…dfc7`, checked by `scripts/verify-search.ts`). R2 calls
`commandScoreInner(item, q, formatInput(item), formatInput(q), 0, 0, {})`, which is literally what
cmdk's `commandScore(item, q, [])` does, with `formatInput(item)` precomputed. `Math.pow` is
Chrome's own, as in cmdk, which is what `parity/tables/pow0999.json` encodes, so scores are
bit-identical to the reference in the pinned Chromium. Nothing from `parity/` is imported. Result:
ids with score > 0, stable-sorted by score descending over candidates in ascending id order (so
ties are id ascending); empty query = every id in dataset order; the query is never trimmed.

### Exactness of the index

A precomputed index may only skip items that provably score 0 (05 §0.5: a subsequence or n-gram
prefilter would drop transposed and doubled-letter matches such as `score("ba","ab") = 0.1`).
Write `P = formatInput(q)`, `m = q.length` (cmdk stops the recursion when `abbreviationIndex ===
abbreviation.length`, the *original* length), `h = formatInput(item)`.

- **P1, character mask.** Used only when `P.length === m`. In `commandScoreInner`, a path to a
  positive score consumes each `P[f]`, `f < m`, either by matching `h[c] === P[f]`, or by skipping
  it through the transposition branch taken at the previous character `f-1` matched at `c`, which
  requires `h[c-1] === P[f]` or `P[f] === P[f-1] (= h[c])`. Either way every code unit of `P`
  occurs in `h`. So an item whose unit set does not cover the query's scores 0. The index stores a
  64-bucket mask per item (a–z, 0–9 and space own a bit; other units share buckets): a collision can
  only let an extra item through to the scorer, never drop one. Order is ignored, so transpositions
  and any-order matches are kept.
- **P2, prefix narrowing.** If `p` is a shorter query with `formatInput(p).length === p.length`,
  `P.length === m` and `formatInput(p)` a prefix of `P`, then `score(item, q) > 0` implies
  `score(item, p) > 0`: truncate a positive path for `q` at `p.length`. Each step before that is
  also a step for `p`; a transposition skip that needed `score < 0.1` for `q` either is allowed for
  `p` too or `p`'s unskipped branch is already positive; a skip that jumps from `p.length-1` past
  `p.length` means `p`'s last character matched, which is terminal (score ≥ 0.99). So `q` is scored
  only over the cached result ids of its longest cached such prefix. The formatted-prefix check is
  required: Final_Sigma lowercases `"ΑΣ"` to `"ας"` but `"ΑΣΑ"` to `"ασα"`.
- **Cache.** `search` is a pure function of `(items, q)`; returning a stored result is exact.

`node scripts/verify-search.ts` checks the vendored file's hash, then compares `SearchIndex` with a
brute-force ranking built on **npm cmdk 1.1.1's published `defaultFilter`** for all 1,000 dev-1
queries at 10k and 50k (fresh index: P1), every prefix of 60 typeable queries typed forward (P2)
and backspaced (cache), and a 2,108-search fuzz over a hostile alphabet (transpositions, doubled
letters, separators, NBSP/tab, Σ/σ/ς, astral halves, CJK, Devanagari, digits). Result: **all 6,032
searches equal** (it caught a sign bug in the first mask test, bit 31). Node's `Math.pow` differs
from Chrome's on some exponents, which is why this compares against cmdk *in the same engine*;
pruning depends only on `score > 0`. The Playwright suite checks bit-exactness in Chromium.

## Latency marker and `results()` (CONTRACT.md)

With `useDeferredValue` the input and the list commit separately, so the flip cannot be keyed off
the query state or the input commit (05 §3.3: that would flip early). It lives in `ResultList`, in
a `useLayoutEffect` keyed on the **results the list renders**, and fires only when the list's query
changes (not on mount or dataset load). In that commit the rows' ref callbacks measure new rows,
and a size change re-renders the list synchronously after the layout effects (React flushes
updates scheduled during commit before returning), so the flip is queued with `queueMicrotask`: it
runs after every list write for that query, in the same task, before any rendering opportunity.
The suite's honesty check (same rAF frame, marker last) passes on every one of the 200 fills and
370 typed keys.

Two things could otherwise write the list a frame late, and are handled:

- **Viewport size.** The list shrinks to fit short result lists; a ResizeObserver would then grow the
  rendered range a frame later. The virtualizer's viewport is the list's `max-height` instead.
- **Scroll position.** A new query starts at the top, like R1 (cmdk scrolls the first item into
  view). If the list was scrolled, the flip effect sets `scrollTop = 0` and reports offset 0 to the
  virtualizer at once (its scroll listener would only see it next frame), so the top rows render in
  the same commit; the later scroll event is a no-op. `scripts/check-keyboard.mjs` checks this.

`window.__ladder.results()` returns the committed list's full ranking, selected id and query, set
in a layout effect of the same commit. Rows carry `data-ladder-item`, `data-id`, `aria-selected`;
the input `data-ladder-input`, the scroller `data-ladder-list`.

## Keyboard

Same visible behaviour as R1 (cmdk 1.1.1 defaults): ↑/↓ (no wrap), Ctrl+N/J/P/K, Meta+↑/↓ (first,
last), Alt+↑/↓ (= ↑/↓, no groups), Home/End (first/last result, and they do not move the caret, as
in R1), Enter (sets `data-last-selected` on `<html>` to the trimmed item, as R1), hover selects,
click selects and runs, IME composition is ignored, Page Up/Down and Escape do nothing (as R1).
The selected row is scrolled into view (`scrollToIndex`, align auto, 4 px scroll padding = R1's
`scroll-py-1`). `scripts/check-keyboard.mjs` drives 70 keys on 7 (dataset, query) pairs in R1-vite
and R2 side by side and compares the selected item, its visibility and Enter's action after every
key, plus row heights (wrapping): all equal.

## Deviations from R1 (visible or behavioural)

- **Correct order after backspace** (R1 bug fixed, required for R2).
- **List always starts at the top after a query change.** R1 can stay scrolled (cmdk's sort moves
  DOM nodes, Chrome's scroll anchoring follows one, and cmdk only scrolls into view when the selected
  value changes); seen with `fill('se')` on 1k. Recorded as an R1 quirk.
- **Rows outside the viewport (+50) are not in the DOM**: browser find-in-page (Ctrl+F) does not
  find them, and `aria-activedescendant` can point at a row that is scrolled out. `aria-setsize`/
  `aria-posinset` tell assistive tech the list size. Inherent to virtualization (feature cost).
- **Skipped intermediate queries.** With deferred rendering, if a key arrives while the list is
  still rendering the previous query, React abandons that render and the list (and the marker) go
  straight to the newer query, so that query change gets no flip of its own. The suite waits for
  each flip, so it never happens there; see "Open questions".

## Harness notes (no harness files were changed)

1. **Fixed in the harness 2026-09-30:** `marker.json`'s `resultSelector` now matches `[data-ladder-item]`, and the correctness test accepts fewer than 50 rendered rows from a virtualized rung. R2 therefore dropped its `cmdk-item` row attribute. It keeps an overscan of 50 for its own reason: with 10, wrapped rows are not measured before they scroll into view, and row heights and keyboard scrolling diverge from R1 (`scripts/check-keyboard.mjs` fails).
2. *(Merged into 1.)*
3. **Scroll mutations count as "stray".** Scrolling (or ↑/↓ that scrolls) changes a virtualized
   list's DOM with no flip, and `honestyReport()` charges those mutations to the next flip. The suite
   never scrolls, so this is only relevant if a harness scenario scrolls between query changes.

## Build, run, test

```sh
npm ci --prefix rungs/r2-diligent
node rungs/scripts/prepare.mjs                  # dataset + probe into public/ (and dist/)
npm --prefix rungs/r2-diligent run build        # tsc -b && vite build
npm --prefix rungs/r2-diligent run serve -- --port 3103
#   http://localhost:3103/?items=/dataset/10k/items.json   (default /dataset/items.json = 50k)
cd rungs/tests
npx playwright test --project r2-diligent --grep-invert @timing        # parity suite
LADDER_QUERIES=10 LADDER_TYPED_10K=2 LADDER_TYPED_50K=0 npx playwright test --project r2-diligent --grep-invert @timing
npx playwright test --project r2-diligent --grep @timing --workers=1   # rough timings
node rungs/r2-diligent/scripts/verify-search.ts [--quick]              # index exactness (Node)
node rungs/r2-diligent/scripts/check-keyboard.mjs                      # R1 vs R2 keys (needs both servers)
```

`vite.config.ts` sends COOP/COEP on `vite preview` (and `vite` dev), as R1-vite does.

## Results

2026-09-30, headless Chromium 141.0.7390.37 (Playwright build), 4 vCPU shared with another agent's
test runs, dataset dev-1.

`npx playwright test --project r2-diligent --grep-invert @timing` (full defaults: 100 queries, 8
typed @10k + 2 @50k): **211 / 211 passed** in 2.0 min (3 workers). R1 took 33.8 min for its two
variants, most of it mounting 50k rows.

| Check | r2-diligent |
| --- | --- |
| (a) COOP/COEP, `crossOriginIsolated`, probe/marker contract | pass |
| (b) **strict**, fresh mount + one fill, 100 queries × {10k, 50k} | 200 / 200 |
| (b) typed, every key, **strict** forward **and backspace** (R2 rule) | 370 / 370 keys (185 forward, 185 backspace) |
| (b) `results()` = rows on screen (start of the ranking, same text, same selected row) | every check |
| (c) one flip per query change, same rAF frame as every list mutation, marker last | 200 fills + 370 keys |
| (c) flip `top50` and `count` = reference | 200 / 200 |
| (d) `aria-selected` = first result = `rank().selected` | always |

Also: `scripts/verify-search.ts` (6,032 searches equal to brute force on cmdk's scorer),
`scripts/check-keyboard.mjs` (R1 = R2 on 70 keys × 7 lists, row heights equal; honest flips and
scroll reset when the list is scrolled), and a pixel diff of R1 vs R2 screenshots (0 pixels differ,
light and dark).

**Rough headless timings** (`--grep @timing --workers=1`; software rendering, CDP input, single
samples: context only, never a result). ms from the `input` event's `timeStamp`, same columns as
rungs/README.md. R1-vite was re-run right after R2 on the same box (a second R2 run differed by at most 12 ms per row,
except 10k `sett`, 86 → 61 ms). "Mount" is `openPalette` wall time: navigation, dataset fetch,
index build (R2) or 10k/50k row mount (R1), and settle.

| Variant | Size | Query (results) | → marker flip | → next rAF | → end of that frame's main-thread work | Mount |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| R1-vite | 10k | `open` (1,322) | 357 | 415 | 449 | 2.0 s |
| **R2** | 10k | `open` (1,322) | **32** | **35** | **36** | **0.42 s** |
| R1-vite | 10k | `a` (7,971) | 478 | 553 | 706 | 2.1 s |
| **R2** | 10k | `a` (7,971) | **23** | **32** | **32** | **0.42 s** |
| R1-vite | 10k | `sett` (4,208) | 384 | 581 | 671 | 2.2 s |
| **R2** | 10k | `sett` (4,208) | **61** | **65** | **66** | **0.41 s** |
| R1-vite | 10k | `git br` (73) | 407 | 414 | 422 | 2.3 s |
| **R2** | 10k | `git br` (73) | **32** | **37** | **39** | **0.42 s** |
| R1-vite | 50k | `open` (6,729) | 2,397 | 2,800 | 3,027 | 19.5 s |
| **R2** | 50k | `open` (6,729) | **99** | **104** | **106** | **0.42 s** |
| R1-vite | 50k | `a` (40,292) | 3,494 | 5,291 | 6,771 | 20.3 s |
| **R2** | 50k | `a` (40,292) | **39** | **46** | **51** | **0.42 s** |
| R1-vite | 50k | `sett` (21,124) | 2,412 | 4,003 | 4,776 | 21.5 s |
| **R2** | 50k | `sett` (21,124) | **235** | **243** | **244** | **0.47 s** |
| R1-vite | 50k | `git br` (440) | 2,292 | 2,316 | 2,356 | 18.5 s |
| **R2** | 50k | `git br` (440) | **44** | **45** | **49** | **0.44 s** |

What is left in R2's time is almost all cmdk's scorer: a fill is a *fresh* query (no cached prefix
to narrow from), so P1 is the only pruning; `sett` (doubled `t` takes cmdk's duplicate-letter
branch on every candidate) is the worst of the four. Typing narrows with P2 and backspace is a
cache hit, so per-key work in the typed spec is smaller than a fresh fill. The list work is ~60
rows whatever the result count, which is why 40,292 results (`a`) cost less than 6,729 (`open`).
Full rows: `rungs/tests/out/timing-r2-diligent.json` (gitignored).

## Open questions

- **Flips for skipped queries** (deferred rendering, above): is "exactly one flip per query change"
  meant per *input* change or per *displayed* query? A rung using `useDeferredValue` legitimately
  skips states under fast typing; Phase A's A.seq trial rows would see keys without a flip.
- **`resultSelector`** and the **≥ 50 rendered rows** requirement (harness notes 1–2) should be
  written into CONTRACT.md / marker.json by the harness owner.

## Pinned versions

From `package-lock.json`: vite **8.3.1**, react / react-dom **19.3.0**, @tanstack/react-virtual
**3.14.13** (virtual-core 3.14.x), tailwindcss 4.3.3, @base-ui/react 1.8.0, lucide-react 1.49.0,
shadcn 4.21.0, TypeScript 6.0.3; cmdk 1.1.1 (dev only, scorer check).
