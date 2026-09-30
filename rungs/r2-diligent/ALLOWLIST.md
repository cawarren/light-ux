# R2 allow-list: documented, idiomatic React practices

The spec's rule for R2: *"R2 may only use techniques from an allow-list of documented, idiomatic
React practices"* (docs/spec.md, "The ladder"). R2 is R1 plus **list virtualization, memoization,
deferred rendering and a precomputed search index**, on the same stack. This list was written
before the code. An entry qualifies only if the React docs (react.dev, or the official legacy
reactjs.org docs) or the documentation of a widely used library in R1's ecosystem describe it as
a normal way to write React. "Used" says where R2 uses it (README.md has the full map).

## Allowed

| ID | Technique | Official documentation | Used |
| --- | --- | --- | --- |
| A1 | **Production build** of the same toolchain (Vite `vite build`, served by `vite preview`) | [Vite: Building for production](https://vite.dev/guide/build); [React: use the production build](https://legacy.reactjs.org/docs/optimizing-performance.html#use-the-production-build) | yes (same as R1) |
| A2 | **Memoization**: `React.memo` on list and row components, `useMemo` for derived data, `useCallback` for props passed to memoized children; memoizing a pure function's results | [memo](https://react.dev/reference/react/memo), [useMemo](https://react.dev/reference/react/useMemo) ("Skipping expensive recalculations"), [useCallback](https://react.dev/reference/react/useCallback) ("Skipping re-rendering of components") | yes |
| A3 | **Stable keys** from the data (the item id), never the array index, so rows keep their DOM node across queries | [Rendering lists: keys](https://react.dev/learn/rendering-lists#keeping-list-items-in-order-with-key) | yes |
| A4 | **Deferred rendering**: `useDeferredValue` (or `startTransition`) so the input commits at once and the list re-renders in an interruptible background render; the list must be memoized for this to help | [useDeferredValue](https://react.dev/reference/react/useDeferredValue) ("Deferring re-rendering for a part of the UI"), [startTransition](https://react.dev/reference/react/startTransition) | `useDeferredValue` |
| A5 | **List virtualization with a mainstream library** (TanStack Virtual or react-window), including its documented options: overscan, dynamic row measurement (`measureElement`), custom `getItemKey`, `observeElementRect`/`observeElementOffset`, `scrollToIndex` | React: [Virtualize long lists](https://legacy.reactjs.org/docs/optimizing-performance.html#virtualize-long-lists); [TanStack Virtual](https://tanstack.com/virtual/latest/docs/introduction), [Virtualizer API](https://tanstack.com/virtual/latest/docs/api/virtualizer), [dynamic example](https://tanstack.com/virtual/latest/docs/framework/react/examples/dynamic); [react-window](https://github.com/bvaughn/react-window) | TanStack Virtual 3.14.13 |
| A6 | **Accessible virtualized listbox**: `aria-setsize`/`aria-posinset` on options when not every option is in the DOM; `aria-activedescendant` combobox pattern | [MDN aria-setsize](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-setsize), [WAI-ARIA APG combobox](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) | yes |
| A7 | **Move expensive work out of render; precompute derived data once when the data arrives** (here: a search index built in the fetch callback after the dataset loads, at runtime, never bundled). An index may skip candidates **only if provably exact** (never drops an item with score > 0), with the proof written down and tested | [You might not need an effect: caching expensive calculations](https://react.dev/learn/you-might-not-need-an-effect#caching-expensive-calculations); [useMemo: how to tell if a calculation is expensive](https://react.dev/reference/react/useMemo#how-to-tell-if-a-calculation-is-expensive) | yes |
| A8 | **Avoid layout thrash**: no interleaved DOM reads and writes in the update path; read layout once per commit (the virtualizer's measurement), use `transform` for the row window offset | [web.dev: avoid layout thrashing](https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing) | yes |
| A9 | **Adjust state when a prop changes during render** instead of in an effect (reset the selection to the first result in the same render as the new list) | [Adjusting some state when a prop changes](https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes), [useState: storing information from previous renders](https://react.dev/reference/react/useState#storing-information-from-previous-renders) | yes |
| A10 | **Refs for imperative DOM work** that React does not model: scrolling a list, exposing `scrollToIndex` with `useImperativeHandle`; `useLayoutEffect` for DOM work that must land before paint | [Manipulating the DOM with refs](https://react.dev/learn/manipulating-the-dom-with-refs) (scrolling example), [useImperativeHandle](https://react.dev/reference/react/useImperativeHandle), [useLayoutEffect](https://react.dev/reference/react/useLayoutEffect) | yes |
| A11 | **Controlled input** holding the query in state | [Controlling an input with a state variable](https://react.dev/reference/react-dom/components/input#controlling-an-input-with-a-state-variable) | yes |
| A12 | **Documented library escape hatches** instead of fighting a library: e.g. cmdk's `shouldFilter={false}` with your own filtering, or rendering shadcn's styles on plain elements | [cmdk README](https://github.com/pacocoursey/cmdk#command-cmdk-root) (`shouldFilter`), [shadcn/ui Command](https://ui.shadcn.com/docs/components/command) | shadcn styles on plain elements (README explains) |
| A13 | **React Compiler** (automatic memoization) | [React Compiler](https://react.dev/learn/react-compiler) | no: manual A2 memoization covers the same ground and keeps the build identical to R1's |

## Not allowed in R2

These belong to later rungs (R3 and below) or change what is being measured:

- Removing or bypassing the framework for the list: writing row DOM by hand, `innerHTML`, a
  hand-written pool of recycled row nodes, or any list DOM update outside React's commit. (The
  harness's own marker, and scrolling through a ref (A10), are the only imperative DOM writes.)
- Custom renderers or reconcilers, patching React, cmdk or TanStack Virtual internals, canary or
  experimental React APIs.
- **Web Workers** / `OffscreenCanvas`: react.dev gives no guidance on moving work to workers, so
  they are not a "documented, idiomatic React practice"; filtering in a worker is R3's layer.
- **WebAssembly**, SIMD, or a rewritten scorer. R2 runs cmdk 1.1.1's scorer verbatim; only where
  and how often it is called changes.
- **SharedArrayBuffer / Atomics**. The page is cross-origin isolated only because the harness
  requires it for timer resolution (CONTRACT.md); R2 does not use the capability.
- CSS containment / `content-visibility` tuning, canvas or WebGPU rendering, compositor hints
  (R3/R4 techniques).
- A lossy or approximate index, a result limit (top-K), trimming or normalizing the query, or any
  change to the ranking semantics.
- Detecting the test environment, dataset or seed; bundling the dataset.
