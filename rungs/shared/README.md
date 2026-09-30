# rungs/shared: latency marker + in-page probe (harness-owned)

Framework-free, harness-owned code that every web rung loads. **Rung code never edits these
files and agents optimizing a rung never touch them** (spec, "How agents are used").

| File | What |
| --- | --- |
| `ladder-probe.js` | Classic script (no build, no imports). Installs `window.__ladder`: the marker, the hook point, observers, capture listeners, rAF frame counter, opt-in marker-honesty MutationObserver. Idempotent. |
| `ladder-probe.d.ts` | Types for `window.__ladder`. Rungs keep a byte-checked copy (`components/ladder-types.ts`). |
| `marker.json` | Default marker geometry. The harness owns it and writes a per-machine one (04-calibration §4.2). |

`rungs/scripts/prepare.mjs` copies `ladder-probe.js` and `marker.json` to each rung's
`public/ladder/{probe.js,marker.json}` (gitignored). Each rung loads `/ladder/probe.js` before its
own code (Next: `next/script` `beforeInteractive`; Vite: a plain `<script>` in `index.html`). The
harness server (phase-a A4) may instead inject it into the HTML; a second copy is a no-op.

## Marker contract (phase-a §4.4, 04 §4.2, 05 §3.3)

- A `position:fixed` square appended to `<html>` (outside `<body>`, so no framework tree or
  hydration sees it), `#000` at start, pure `#000`/`#fff`, no transition, top z-index, ignores the
  theme.
- Geometry in **viewport device pixels**: default `x=16, y=16, size=128`. CSS px = device px /
  `devicePixelRatio`, re-applied when the DPR changes (zoom), so the square stays fixed in
  device pixels. Sources, in priority order: URL `?ladderMarker=x,y,size`, then `marker.json` next
  to the script (fetched once; `__ladder.ready` resolves after), then the defaults.
- **Hook point**: `window.__ladder.markerFlip(query, { count })`. The rung calls it exactly once
  per query change, **in the same JS task as, and after, the last list DOM mutation** for that
  query, with no rendering opportunity in between. It flips the colour synchronously, replaces the
  child `<span elementtiming="ladder-flip-<seq>">▮</span>` (Element Timing presentation probe,
  phase-a §0.4, same colour as the marker), sets `data-ladder-seq` (the last write), calls
  `performance.mark('ladder:flip', {detail:{seq}})`, and appends
  `{seq, query, count, t, frame, color, top50, digest}` to `__ladder.flips` (`top50` is read from
  the first `resultSelector` match and its siblings only; `digest` is FNV-1a over them).
- React rungs flip from a component **inside** the list's own store subscription (for cmdk:
  `useCommandState(s => s.search)` + `useLayoutEffect` + `queueMicrotask`); see
  `rungs/r1-typical/README.md` for why.

## Probe (phase-a §1.2)

- `crossOriginIsolated` → `__ladder.crossOriginIsolated` (console warning when false; the harness
  refuses such a block).
- PerformanceObservers (buffered): `event` (durationThreshold 16), `first-input`,
  `long-animation-frame`, `element` → `__ladder.entries.*` (plain objects).
- Capture-phase passive `keydown`, `beforeinput`, `input` at `window` → `__ladder.inputs`
  (`timeStamp`, key, inputType, frame).
- rAF loop: `__ladder.frame` counter, `__ladder.frames` (`raf` timestamp, `post` = MessageChannel
  task right after that frame's rendering update).
- Marker honesty (opt-in, it costs time on big lists): `__ladder.watchList(selectorOrEl)` starts a
  MutationObserver on the list and on the marker's `data-ladder-seq`; `__ladder.honestyReport()`
  returns per flip `{listMutations, late, stray, sameFrame, markerLast}` where "same frame" means
  no rAF callback ran in between. cmdk's ResizeObserver write of `--cmdk-list-height` on the list
  element's own `style` is ignored (layout bookkeeping, not content).
- Buffers are rings of 8,192 records. `__ladder.snapshot()` returns a JSON copy; `__ladder.reset()`
  clears buffers. Nothing is sent anywhere.
- `__ladder.datasetLoaded({url, count})` is the rung's "rendered" signal (test hook).

## Not done here (harness agent, phase-a A2/A4)

Clock sync with the injector, settle handshake, WebSocket/beacon flush, per-trial
`top50_digest` comparison against `ranker-ts`, vanilla/canvas marker variants for R3/R4, and the
A1 spike on the laptop. Headless Chromium 141 (the Playwright build) does report an `element`
entry for every inserted `ladder-flip-<seq>` span, with `renderTime` set, but `paintTime` and
`presentationTime` are absent there (they shipped in Chrome 144/145), so the pinned Chrome for
Phase A must be ≥ 145 or the trace fallback (phase-a §2.4) is needed.
