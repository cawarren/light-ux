# R1 "Typical" (Next.js)

The Latency Ladder baseline (docs/spec.md, "The ladder"): a stock shadcn/ui Command palette in a
default Next.js app, built the way a typical team would ship it. **R1 is never optimized by agents**
(spec rules). Its sibling `rungs/r1-vite/` renders the same `Palette` with Vite for the
Next-vs-Vite comparison (05 §3.2).

## Build, run, test

```sh
# from the repo root, once
npm install                                           # root workspaces (tests, ranker-ts, dataset)
npm run -w @latency-ladder/rung-tests install:rungs   # npm ci in r1-typical and r1-vite
npm run -w @latency-ladder/rung-tests build:rungs     # dataset dev-1 + probe -> public/, then next build + vite build

# run the production build (what every measurement uses)
cd rungs/r1-typical && npx next start -p 3101
#   http://localhost:3101/                              50k items (public/dataset/items.json)
#   http://localhost:3101/?items=/dataset/10k/items.json  other sizes: 1k, 10k, 50k
#   &ladderMarker=x,y,size                              marker rect in viewport device px

# parity tests (both R1 variants; Playwright starts the servers if they are not running)
npm run -w @latency-ladder/rung-tests test            # full: ~1 h headless (100 fresh 50k mounts per variant)
npm run -w @latency-ladder/rung-tests test:quick      # 10 queries, 2 typed sequences
npm run -w @latency-ladder/rung-tests test:timing     # rough headless fill timings -> rungs/tests/out/
```

## What is "typical" here

Created with the stock tools, then only the palette page was written:

1. `npx create-next-app@16.3.7 r1-typical --ts --tailwind --eslint --app --import-alias "@/*" --use-npm --disable-git --yes`
   (all defaults: TypeScript, Tailwind v4, ESLint, App Router, Turbopack, no `src/`, `AGENTS.md`).
2. `npx shadcn@4.21.0 init --defaults` (style `base-nova`, the CLI's current default; Base UI,
   neutral, CSS variables, lucide) and `npx shadcn@4.21.0 add command`. `components/ui/*` are the
   CLI's output, **unmodified** (the current registry imports `cn` from shadcn's `cn` package).
3. `cmdk` pinned to **1.1.1** (`--save-exact`), the version the reference ranker vendors.
4. Dark mode the way shadcn's Next docs do it: `next-themes` `ThemeProvider`
   (`attribute="class" defaultTheme="system" enableSystem`), so light/dark follows the OS.

`components/palette.tsx` is the whole feature:

- inline `<Command>` (not `CommandDialog`), `CommandInput autoFocus`, **uncontrolled** input,
  `CommandList` + `CommandEmpty` + one `CommandItem` per item (`key={i} value={item}`);
- default cmdk filtering and sorting (no `shouldFilter`, no custom `filter`), no groups, no icons
  (the `CheckIcon` inside every shadcn item is part of the stock component and is kept);
- **no virtualization, no memoization, no deferred rendering**, every item rendered;
- data: a client `fetch('/dataset/items.json')` in `useEffect` at runtime, overridable with
  `?items=<url>`; the JSON is served from `public/dataset/` and is **not bundled**;
- `next build && next start`, default config apart from the headers below.

## What is not typical (and why)

| Deviation | Why |
| --- | --- |
| COOP `same-origin` + COEP `require-corp` headers (`next.config.ts` `headers()`) | Project-wide decision (phase-a §1.3–1.4, decided 2026-09-30): every web rung is cross-origin isolated so all rungs get 5 µs timers and uncoarsened presentation times. **A typical production Next.js app is not isolated.** Isolation changes nothing in R1's code path except timer precision; phase-a plans one A/A pair with and without it. |
| `/ladder/probe.js` loaded with `next/script` `beforeInteractive` | Harness-owned marker + probe (`rungs/shared/`). Not rung code. |
| `components/latency-marker.tsx` rendered inside `<Command>` | Harness-owned test hook: calls `window.__ladder.markerFlip(search, {count})`. Renders nothing. |
| `datasetLoaded` call and `onSelect` writing `data-last-selected` on `<html>` | Test hooks (05 §3.1): "list rendered" signal and an Enter action the keyboard script can assert. |
| Geist bound to `--font-sans` in `layout.tsx` | `shadcn init` rewrote `globals.css` to use `--font-sans` but left create-next-app's `--font-geist-sans` in the layout, so the page fell back to the browser font. Renaming the variable makes shadcn's intended font apply (the Vite variant gets the same font via `@fontsource-variable/geist`). |
| Page content (`app/page.tsx`) | The create-next-app landing page replaced by the centred palette; its SVGs removed. |

## The marker: same code path, same frame

The marker must flip in the same frame as the list, drawn by the same code path (spec,
"Latency marker"). With an uncontrolled `CommandInput` the list updates like this (05 §3.3):

1. The input's `onChange` calls cmdk's `store.setState('search')`: `filterItems()` and `sort()` run
   synchronously (sort moves DOM nodes), then `emit()`.
2. Every `useSyncExternalStore` subscriber (items, `CommandEmpty`, our marker) re-renders in the
   **same** sync-lane React commit, where non-matching items unmount.
3. cmdk's own layout effects then re-render synchronously (sorting newly mounted items,
   selecting the first item), still before the browser can paint.

`LatencyMarker` reads `useCommandState(s => s.search)` (the same store as the items), so it
re-renders in the commit of step 2. Its `useLayoutEffect([search])` queues a **microtask** that
calls `markerFlip`: the microtask runs after the whole synchronous cascade of step 3 and before
any rendering opportunity. The tests confirm, per query change, one flip, in the same rAF frame
as every list mutation, and after the last one (`markerLast`). Without the microtask the flip
lands in the same frame but *before* step 3's selection change (three list mutations after it: the selection moving to the new first item).

**Why not from the parent's controlled state.** The obvious "typical" way to know the query is
`<CommandInput value={q} onValueChange={setQ}/>` with `q` in the page component, and flipping on
`q`. With a controlled input cmdk copies `value` into its store in a **passive `useEffect`** after
the parent's commit (cmdk index.tsx L795-799), so the parent's commit (and a marker keyed on `q`)
happens one commit **before** the list is filtered: an early, dishonest marker. It would also
re-render the page component and its 50k `CommandItem` elements on every key, which is a
different (and slower) R1. Keeping the input uncontrolled and reading cmdk's own store avoids
both.

## Known R1 behaviour found by the tests (cmdk 1.1.1, not fixed)

- **Fresh mount + one fill: strict-equal** to `rank()` (score desc, id asc).
- **Typing forward: tie-insensitive-equal** (tie order depends on history, 05 §0.1).
- **Backspace: order is wrong, not only ties.** cmdk sorts inside `setState('search')` over the
  items currently in the DOM; items that re-appear on a wider query are inserted later by React
  at their original position and are never sorted (their registration effect runs once, at mount).
  Example (10k, typing `tab size in internal` then backspacing to `tab size in in`): the item
  scored 0.151 is shown above the one scored 0.891. The result *set* is right. After clearing the
  query the list is not in dataset order either. See `rungs/tests/specs/typed.spec.ts`.

## Pinned versions

From `package-lock.json`: next **16.3.7** (App Router renders with its vendored React
**19.3.0-canary-cbb046ab-20260731**; `react`/`react-dom` packages 19.2.8), cmdk **1.1.1**,
tailwindcss 4.3.3, @base-ui/react 1.8.0, lucide-react 1.49.0, cn 0.4.0, next-themes 0.4.6,
shadcn 4.21.0, TypeScript 5.9.3. Node 22.22.2. Record these in each session manifest.

`AGENTS.md`/`CLAUDE.md` are create-next-app's defaults (a pointer to the bundled Next docs).
