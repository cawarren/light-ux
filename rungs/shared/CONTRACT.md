# Rung contract (R2 and above)

What every web rung from R2 up must provide, so the shared parity suite (`rungs/tests`) and the Phase A harness can drive and check it without knowing its internals. R1 predates this and uses cmdk's own attributes; the tests accept both.

## Serving

- `rungs/<rung>/package.json` with `npm run build` (production build) and `npm run serve -- --port N` (serves that build).
- Every response carries `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` (decided 2026-09-30; `crossOriginIsolated` must be true).
- `rungs/scripts/prepare.mjs` copies the dataset to `public/dataset/{1k,10k,50k}/items.json` (and `dataset/items.json` = 50k) and the probe to `public/ladder/{probe.js,marker.json}`; if the build output is `dist/`, it also refreshes `dist/`. Both are gitignored. Nothing from the dataset is bundled.

## Page

- Loads `/ladder/probe.js` as a classic script before the app, and the dataset at runtime from the `items` URL parameter (default `/dataset/items.json`), then calls `window.__ladder.datasetLoaded({ url, count })`.
- Input: `[data-ladder-input]` (the text input). List container: `[data-ladder-list]`. Rendered result rows: `[data-ladder-item]` with `data-id` = the item's dataset index, and the row's `textContent` exactly the item string. The selected row has `aria-selected="true"`.
- Behaviour matches R1's visible behaviour: the first result is selected after each query change; the empty query shows every item in dataset order.

## Correctness

- Results must equal `rank(items, q)` from `parity/ranker-ts` **strictly** (score descending, then id ascending) at every step, including after backspace. Rungs may implement search however they like.
- A virtualized rung (one that does not put every result in the DOM) must also define `window.__ladder.results()` returning `{ ids: number[], selected: number | null, query: string }` for the current state. The tests check that the rows on screen are exactly the start of `ids`, with matching text, and that the selected row agrees, so the reported and displayed rankings cannot differ.

## Marker

- Call `window.__ladder.markerFlip(query, { count })` exactly once per query change, **in the same code path and the same frame as the list's DOM update**, after the list's last write for that update. Pass the list container to `window.__ladder.watchList` only via the tests (they call it with `[data-ladder-list]`).
- Scrolling a virtualized list changes the DOM without a query change; the tests read results at scrollTop 0 and do not scroll.

## Rules

- Change only files inside your own rung directory. The probe, marker, dataset, ranker and tests are harness-owned.
- Do not detect the test environment, dataset or seed.
