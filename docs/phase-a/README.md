# Phase A: software-only measurement harness and playground

Scoped 2026-09-29 against [`docs/spec.md`](../spec.md) and the Phase 0 reports in [`docs/phase-0/`](../phase-0/README.md), especially [03 (orchestrator)](../phase-0/03-orchestrator.md), [05 (software foundations)](../phase-0/05-software-foundations.md) and [06 (X1 Carbon)](../phase-0/06-dut-x1-carbon.md).

**The rephase.** The owner will not build the hardware rig first. Phase A builds the web rungs:

- R1: Next.js with shadcn/cmdk.
- R2: diligent React.
- R3: plain DOM plus a worker.
- R4 (optional): WASM with canvas or WebGPU.

Phase A measures these rungs with software instrumentation on the owner's laptop. The laptop is a ThinkPad X1 Carbon Gen 13 (Lunar Lake, 2.8K OLED at 60/120 Hz) running Linux with KDE Plasma (KWin on Wayland). Phase A also builds a playground where the owner can feel the rungs, including in a blind mode.

**Gate A** asks two questions:

1. Is the R1→R3 gap large? For example, at least 3x, or several frames at p95.
2. Can a human tell the rungs apart without knowing which is which?

Later phases add hardware. Phase B is a minimal photodiode, used to cross the browser boundary. Phase C is the full rig from Phase 0.

This document covers only the **harness and playground**. Building the rungs themselves is a dependency (§8.2).

Sources were read live on 2026-09-29. Most Chromium facts come from source files on `main`, read through the GitHub mirror, and are linked where used. Items tagged **[verify]** must be confirmed by the first spike (task A2) on the pinned Chrome build, on the laptop itself.

---

## 0. Findings that change the plan (read first)

1. **Standard web metrics stop at the *next* paint after the event handler, not at the frame that shows the new results.**
   - Event Timing `duration`, INP and Chrome's `EventLatency` trace event all end at the first frame presented after the event is processed ([Event Timing spec](https://w3c.github.io/event-timing/); [web.dev INP](https://web.dev/articles/inp)).
   - R3 filters in a Web Worker and applies the results in a later task. R2 may use `useDeferredValue` or `startTransition`. For both, that first frame often shows only the typed character in the input box, not the new list.
   - These metrics would therefore **flatter exactly the rungs we want to compare against R1**.
   - **The Phase A headline metric is therefore "input → presentation of the frame in which the latency marker flipped".** The marker already has to flip in the same frame as the list update (spec; 05 §3.3). Event Timing and EventLatency become secondary, attribution-only signals.
2. **Chrome on Wayland throws away the compositor's input timestamp.**
   - `WaylandKeyboard::ProcessKey` passes the `wl_keyboard.key` time through `wl::EventMillisecondsToTimeTicks()`, which currently just returns `ui::EventTimeForNow()`. A TODO in the code explains why ([`wayland_keyboard.cc`](https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/host/wayland_keyboard.cc), [`wayland_util.cc`](https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/common/wayland_util.cc)).
   - So on this laptop, `event.timeStamp`, the Event Timing `startTime` and the start of EventLatency all mean **"when Chrome's browser process read the event from the Wayland socket"**. None of them reflects when the key happened.
   - Everything before that point is invisible to Chrome: the kernel, libinput, KWin, and any delay while Chrome's UI thread was busy.
   - Phase A therefore timestamps input **in the injector** (a uinput virtual keyboard, stamped with `CLOCK_MONOTONIC`) and maps that time into the page's clock (§3.4).
3. **Event Timing has no `paintTime` or `presentationTime` in Chrome yet.**
   - Chrome 144/145 shipped the `PaintTimingMixin` (`paintTime` and a nullable `presentationTime`) for Element Timing, LCP, Paint Timing and Long Animation Frames. The shipping notice says it **"omits event timing, which is done separately"** ([Chrome 145 release notes](https://developer.chrome.com/release-notes/145); [blink-dev intent to ship](http://www.mail-archive.com/blink-dev@chromium.org/msg15314.html)).
   - The Blink IDL on `main` confirms this. `performance_event_timing.idl` has no mixin; `performance_long_animation_frame_timing.idl` includes it ([IDL](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/timing/performance_event_timing.idl)).
   - Event Timing `duration` is **rounded to 8 ms** ([spec](https://w3c.github.io/event-timing/)). At 120 Hz that is about one whole frame, too coarse to be a headline metric.
   - LoAF entries exist only for frames longer than **50 ms** ([Chrome docs](https://developer.chrome.com/docs/web-platform/long-animation-frames)). They say nothing about fast rungs.
4. **An in-page presentation timestamp is still possible, through Element Timing.**
   - With every marker flip, the marker inserts a fresh tiny text element carrying an `elementtiming` attribute.
   - Chrome then reports a `PerformanceElementTiming` entry for it, with `paintTime` and `presentationTime` for the frame in which it first painted.
   - That frame is the marker's frame. This gives an in-page, per-trial "marker presented at" time with no tracing.
   - Presentation time is coarsened to 4 ms unless the page is cross-origin isolated ([Paint Timing spec](https://w3c.github.io/paint-timing/)). We serve every rung cross-origin isolated (§1.4).
   - Whether Element Timing reports elements inserted after load and repeatedly is **[verify, spike A2]**. The fallback is the trace-based marker-frame join (§2.4).
5. **Presentation timestamps on this laptop are real.**
   - Chrome's Wayland backend asks for `wp_presentation` feedback on every frame. It turns the compositor's `HW_CLOCK`/`HW_COMPLETION`/`VSYNC` flags into `gfx::PresentationFeedback` ([`wayland_frame_manager.cc`](https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/host/wayland_frame_manager.cc)).
   - Blink's Event Timing uses that feedback timestamp as its end time ([`window_performance.cc`](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/timing/window_performance.cc)).
   - KWin implements presentation-time, protocol version 2 ([wayland.app support table](https://wayland.app/protocols/presentation-time)).
   - Since Chrome 140, `--ozone-platform-hint=auto` is the default, so Chrome runs natively on Wayland ([Phoronix](https://www.phoronix.com/news/Chrome-Auto-Ozone-Platform)).
   - So the "presentation" end point reflects the flip KWin actually completed, **including KWin's compositing**, not a guess.
   - What it still misses is scanout down to the marker row and the panel's pixel response. With the marker near the top-left, that is about 0.5–2 ms (06 §1).
   - The flags actually received must be checked: Chrome logs them as `feedback_flags` in the trace **[verify]**.
6. **Never time a typing sequence through Playwright's keyboard.**
   - CDP `Input.dispatchKeyEvent` injects into `RenderWidgetHost` inside the browser process, skipping the kernel, KWin and Ozone.
   - Its callback resolves only when the **renderer acknowledges the event** (`pending_key_callbacks_` → `OnInputEventAck` in [`input_handler.cc`](https://github.com/chromium/chromium/blob/main/content/browser/devtools/protocol/input_handler.cc)). `await page.keyboard.type()` therefore paces itself to the page. That hides exactly the input queueing R1 suffers at 50k items (05 §0.6).
   - CDP input is fine for correctness and parity scripts, and nowhere else.
7. **Phase A can settle some open Phase 0 decisions cheaply:**
   - 50k vs 10k items (S11 / D-C 24).
   - Windowed vs fullscreen (D-B 10).
   - Next vs Vite for R1 (D-C 25).
   - 60 vs 120 Hz behaviour.

   Each is only an extra block in a software session. §7.4 lists them.

---

## 1. In-page measurement

### 1.1 What each API can and cannot see

| Signal | Start | End | Resolution | Sees | Blind to |
| --- | --- | --- | --- | --- | --- |
| `event.timeStamp` / Event Timing `startTime` | Chrome's own time for the event. On Wayland, when the browser process read it (§0.2) | — | 5 µs when cross-origin isolated, 100 µs otherwise ([Chrome blog](https://developer.chrome.com/blog/cross-origin-isolated-hr-timers)) | — | Kernel, libinput, KWin, time in the Wayland socket, browser UI thread busy before the read |
| Event Timing `processingStart` / `processingEnd` | Dispatch start/end on the main thread | — | Same | Input delay (queueing on main thread), handler cost | Anything asynchronous after the handler |
| Event Timing `duration` | `startTime` | Presentation of the **next** frame after processing. Chrome uses the viz presentation feedback, or a fallback time in some cases (`UpdateFallbackTime`, `window_performance.cc`) | **8 ms** rounding | Handler plus the first frame | Worker or deferred results that paint in later frames; scanout and pixel response |
| `interactionId` | — | — | — | Groups keydown/keypress/keyup (and composition) into one interaction ([spec](https://w3c.github.io/event-timing/); [INP changelog](https://github.com/chromium/chromium/blob/main/docs/speed/metrics_changelog/inp.md)) | — |
| `PerformanceObserver({type:'event', durationThreshold:16})` | — | — | 16 ms is the minimum threshold, so shorter events are **not reported** unless they are first-input | — | Fast interactions. Pair every keystroke with our own records instead |
| LoAF (`long-animation-frame`) | Frame start | `renderStart`, `styleAndLayoutStart`, plus `paintTime`/`presentationTime` (Chrome 145+) | ms | Script attribution for frames **> 50 ms** (R1 at 50k) | Every frame under 50 ms. Its `duration` excludes presentation ([Chrome docs](https://developer.chrome.com/docs/web-platform/long-animation-frames)) |
| Element Timing (`elementtiming` probe in the marker) | — | `paintTime` (end of rendering update), `presentationTime` (frame presented) | 5 µs when cross-origin isolated; 4 ms coarsening otherwise | **The marker's frame**, including worker and deferred cases | Scanout and panel response. **[verify]** re-reporting on inserted elements |
| rAF timestamp | Frame's begin time (vsync-aligned) | — | 5 µs | Which frame an update went into (frame counter) | Whether that frame was actually presented, or when |
| rAF + `MessageChannel` post ("post-animation-frame" trick) | — | Runs just after the frame's rendering update on the main thread | 5 µs | Main-thread end of the frame: a lower bound on paint | Raster, GPU, viz, KWin, display. `requestPostAnimationFrame` itself is still behind experimental flags, so use the polyfill |

**Conclusion.** Inside the page, the best per-trial number is `presentationTime(marker probe) − t_input`, where `t_input` comes from the injector (§3.4). Event Timing is kept for decomposition:

- input delay = `processingStart − startTime`
- processing = `processingEnd − processingStart`

LoAF is kept for attributing R1's long frames to scripts.

### 1.2 The probe (`probe.js`, harness-owned)

The harness server injects `probe.js` into every rung's HTML (§4.3), so rung code, including frozen R1, stays unmodified. It:

1. Registers observers, all with `buffered:true`:
   - `event` with `durationThreshold:16`
   - `first-input`
   - `long-animation-frame`
   - `element`
2. Captures `keydown`, `beforeinput` and `input` at `window` in the **capture phase**, passive, and records `(event.timeStamp, key, inputType)`.
   - The probe does no work in these listeners beyond pushing onto a preallocated ring buffer. It costs well under 10 µs, and every rung pays the same.
3. Runs a rAF loop that keeps a frame counter and the rAF timestamp. It also posts a `MessageChannel` message from each rAF to get the post-frame time. The loop runs in **every** rung, so the cost is equal across rungs.
4. Uses a `MutationObserver` on the marker node and the list container to record, for each change:
   - the frame counter
   - a task sequence number (incremented by a `setTimeout(0)` chain and by each `MessageChannel` task)

   This is 05's F8 marker-honesty proxy, merged in here.
5. Reads the marker's own record of each flip, which the harness-owned marker component writes (§4.4):
   - flip sequence number
   - query string
   - a digest of the top 50 result ids
6. Posts nothing during a timed window. It buffers everything and flushes at block end, with `navigator.sendBeacon` or a WebSocket message, after the injector signals "block done".

### 1.3 Timer resolution

`performance.now()` resolution is 100 µs in Chrome unless the page is cross-origin isolated, when it is 5 µs ([Chrome blog](https://developer.chrome.com/blog/cross-origin-isolated-hr-timers)). The `presentationTime` coarsening also depends on isolation. Serve every rung with:

- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Embedder-Policy: require-corp`

Check `crossOriginIsolated === true` in the probe, and refuse the block otherwise.

This is a response-header change made by the harness proxy, not a change to rung code. It also allows `SharedArrayBuffer`, which an agent-optimised R3/R4 could exploit. **Decided 2026-09-30: every rung, R1 included, is served cross-origin isolated.** Still open: whether rungs may use `SharedArrayBuffer` (proposed: only if it is on the R2/R3 allow-list).

### 1.4 Cross-origin isolation and "typical" R1

A typical Next.js app is *not* cross-origin isolated. Isolation changes nothing in R1's code path except timer precision, but we should say so in the report. We should also run one A/A pair of R1 blocks with and without isolation, using trace-only numbers, to show there is no effect.

---

## 2. Chrome tracing (attribution sessions only)

### 2.1 `EventLatency`: what it is

For each input event, the compositor records one `EventLatency` slice, with nested stage slices. It is emitted by [`cc/metrics/event_latency_tracing_recorder.cc`](https://github.com/chromium/chromium/blob/main/cc/metrics/event_latency_tracing_recorder.cc) under the category group **`cc,benchmark,input,input.scrolling`**; enabling any one of those categories turns it on. Key presses appear as `event_type = KEY_PRESSED` or `KEY_RELEASED`.

The stages in order, with names taken from the source:

| Stage (slice name) | Meaning | Note for this laptop |
| --- | --- | --- |
| `GenerationToBrowserMain` / `GenerationToRendererCompositor` | Event timestamp → browser UI thread, or → renderer compositor | ≈ 0 on Wayland, because the timestamp *is* the read time (§0.2) |
| `BrowserMainToRendererCompositor` | Browser → renderer IPC | |
| `RendererCompositorQueueingDelay`, `RendererCompositorProcessing`, `RendererCompositorToMain` | Compositor-thread input handling and hand-off | |
| `RendererMainProcessing` | Main-thread dispatch (JS handlers) | |
| `RendererMainFinishedTo{BeginImplFrame, SendBeginMainFrame, Commit, EndCommit, Activation, EndActivate, SubmitCompositorFrame}` | Waiting for and running the frame pipeline | Frame-alignment wait shows up here |
| `SubmitCompositorFrameToPresentationCompositorFrame` with viz substages (`SubmitToReceiveCompositorFrame`, `ReceiveCompositorFrameToStartDraw`, `StartDrawToSwapStart`, `Swap`, `SwapEndToPresentationCompositorFrame`, `SwapStartToBufferAvailable`, `BufferAvailableToBufferReady`, `BufferReadyToLatch`, `LatchToSwapEnd`) | GPU process draw, swap, and wait for presentation feedback | **KWin compositing plus the wait for the flip lands in `SwapEndToPresentationCompositorFrame`** |

Stage names come from [`compositor_frame_reporter.cc`](https://github.com/chromium/chromium/blob/main/cc/metrics/compositor_frame_reporter.cc).

**Limitation, repeated from §0.1:** EventLatency ends at the frame that included the event's own effects, which is the first frame after the handler. For R3's worker path, the list appears in a later frame. That later frame has *no* EventLatency, because no input event is attached to it. So EventLatency attributes the "handler and first frame" part. The worker round trip and the later frame come from the marker-frame join (§2.4).

Which of RawKeyDown or Char carries the EventLatency that holds the `input` event's frame is **[verify]**.

### 2.2 Categories and config

Use a Perfetto `TraceConfig` passed to CDP `Tracing.start` in `perfettoConfig`, as a base64 proto. The same CDP call also takes `transferMode:"ReturnAsStream"` and `streamFormat:"proto"` ([CDP Tracing.pdl](https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Tracing.pdl)). Keep the category list short:

```
buffers { size_kb: 262144 fill_policy: DISCARD }
data_sources { config { name: "track_event"
  track_event_config {
    disabled_categories: "*"
    enabled_categories: "input"            # EventLatency (via cc,benchmark,input group), input routing
    enabled_categories: "cc"               # PipelineReporter / frame stages
    enabled_categories: "benchmark"
    enabled_categories: "viz"              # display compositor, presentation feedback
    enabled_categories: "gpu"
    enabled_categories: "blink.user_timing"# performance.mark() from the marker
    enabled_categories: "toplevel"
  } } }
data_sources { config { name: "org.chromium.trace_metadata" } }
```

Field names and data-source names should be checked against the pinned Chrome and the `perfetto` Python package. **[verify]**

Do **not** enable `disabled-by-default-devtools.timeline*`, screenshots or `v8.cpu_profiler` in these sessions; they are heavy.

**Collection options:**

- **Automated (recommended).** Playwright for Python attaches over CDP to a Chrome the harness launched itself (§4.2): `connect_over_cdp` → `browser.new_browser_cdp_session()`. Then:
  1. `Tracing.start{perfettoConfig, transferMode:"ReturnAsStream", streamFormat:"proto"}`
  2. run the block
  3. `Tracing.end`
  4. wait for `tracingComplete{stream}`
  5. `IO.read` until EOF, then write `trace.pftrace`
- **Manual.** Use [ui.perfetto.dev](https://ui.perfetto.dev) "Record new trace" with the Chrome target, or `chrome://tracing` (legacy). This is for one-off looks only; manual traces never feed the report.

### 2.3 Parsing with trace_processor

Use the `perfetto` Python package (`from perfetto.trace_processor import TraceProcessor`), pinned.

- `INCLUDE PERFETTO MODULE chrome.event_latency;` provides the table **`chrome_event_latencies`**, with one row per EventLatency:
  - `ts`, `dur`, `event_type`, `is_presented`, `vsync_interval_ms`
  - `presentation_timestamp`, computed from the end of `SwapEndToPresentationCompositorFrame`, with fallbacks
  - `latch_timestamp`, `swap_end_timestamp`, `buffer_ready_timestamp`

  Source: [stdlib `chrome/event_latency.sql`](https://github.com/google/perfetto/blob/main/src/trace_processor/perfetto_sql/stdlib/chrome/event_latency.sql).
- To break down the stages, join `slice` with `descendant_slice(id)` from each EventLatency row and pivot by `name`. That yields one row per key event with a column per stage, which goes into `stages.parquet`.
- The marker writes `performance.mark('ladder:flip', {detail:{seq, trial}})`. These appear as `blink.user_timing` slices.

### 2.4 Marker-frame join (the fallback for §0.4, and a cross-check)

For each `ladder:flip` mark:

1. Find the frame whose main-thread `BeginMainFrame` / commit contains the mark. The mark's task must end before that frame's `Commit`.
2. Take that frame's presentation time from the `PipelineReporter` / graphics-pipeline slices for the same frame sequence number.

The exact join key (`frame_sequence` / `surface_frame_trace_id` args), and whether stdlib modules `chrome.graphics_pipeline` or `chrome.frames` cover this, are **[verify, spike A2]**.

The result is `t_present_marker` in trace time. Cross-check it against the Element Timing `presentationTime` from the same trial. Across ≥ 200 trials they should agree within 1 ms.

### 2.5 Clock domains

- Chrome's `base::TimeTicks` on Linux is `CLOCK_MONOTONIC`. `performance.now()` is a constant offset from it within one page lifetime.
- Perfetto may rebase Chrome's timestamps onto `CLOCK_BOOTTIME` using clock snapshots. Read the `clock_snapshot` table and convert. **[verify]**
- The laptop is never suspended during a session (06 §2.4), so the MONOTONIC→BOOTTIME offset is constant. The injector also logs both clocks at block start and end.

### 2.6 Tracing overhead and session separation

This follows the spec rule: tracing runs in separate sessions.

- **M sessions (measure).** Used for all headline numbers:
  - uinput input only
  - no tracing
  - **no CDP client attached** during blocks: Chrome is launched with the debugging port but no client connects, or it is launched without it
  - data comes from the probe only
- **T sessions (trace).** Same schedule, with tracing on and CDP attached. Their numbers are used **only for stage breakdowns**. They never enter Gate A percentiles.
- **P sessions (parity).** Playwright/CDP input, for correctness and the marker-honesty stress tests (§4.5). No timing is reported from them.
- **Overhead check.** For each rung, compare the M and T distributions of the probe's own metric (marker presentation − injector time) with a two-sample bootstrap on p50 and p95. Report the difference as "trace overhead". If it is more than about 1 ms at p50, shrink the category list.

---

## 3. Input injection

### 3.1 Options compared

| Injector | Path exercised | Timestamp we can trust | Verdict |
| --- | --- | --- | --- |
| CDP `Input.dispatchKeyEvent` (Playwright `keyboard.*`) | Browser process → renderer only. Skips kernel, libinput, KWin, Wayland, Ozone and the browser UI thread's platform event path. Default timestamp is "now" in the browser (`GetEventTimeTicks`) | Browser-side only | **Parity and correctness only.** Ack-paced (§0.6) |
| `xdotool` / XTEST | X11/XWayland only; Chrome is native Wayland | — | No |
| `ydotool` (daemon `ydotoold` writes to `/dev/uinput`) | Kernel → libinput → KWin → Chrome | Only what we log ourselves; one extra IPC hop; its own inter-event delays | Usable for manual poking; not for timed runs |
| **python-evdev `UInput`** (direct `/dev/uinput` writes) | **Kernel → libinput → KWin → `wl_keyboard.key` → Chrome** | `CLOCK_MONOTONIC` read immediately after `write()` + `SYN_REPORT` | **Recommended** |
| Small C/Rust uinput injector | Same | Same, with lower jitter | Fallback if python-evdev jitter p99 > 0.2 ms (§3.3) |
| Real keyboard by hand | Adds the keyboard's own scan and debounce (internal i8042 keyboard via the EC, **[verify]** latency) | None | Playground and iPhone check only |

What uinput still skips, compared with the eventual rig: USB polling (0–1 ms at 1 kHz) and the keyboard's own firmware. The rig will add those back in Phase B/C. Both are the same for every rung, so they do not bias the rung-to-rung gap.

### 3.2 How the timestamps relate

- The kernel stamps each `input_event` when uinput delivers it to evdev (`CLOCK_MONOTONIC` by default for clients that request it).
- libinput passes it on in µs.
- KWin sends `wl_keyboard.key` with a **ms** timestamp.
- Chrome **ignores** that value and stamps with `EventTimeForNow()` when its UI thread handles the Wayland event (§0.2).

So:

- `t_chrome − t_inject` is the **OS delivery segment**: kernel, libinput, KWin input handling, socket, and Chrome UI-thread wakeup.
- Phase A measures it per trial (§3.4). It should be identical across rungs. If R1's main-thread load slows Chrome's *browser* UI thread, it will show up here, and that is a real, attributable cost.

### 3.3 Injector design

- One Python process, `ladder soft inject`, running on the laptop.
  - It needs `/dev/uinput` access through a udev rule and group membership, **not root**. Chrome must never run as root, and never with `--no-sandbox`.
  - It creates one virtual keyboard: `UInput({EV_KEY: [...ascii keys, KEY_BACKSPACE, KEY_ESC]}, name="ladder-kbd")`.
  - Before a block starts, it waits for KWin to register the device and for Chrome to have focus. Focus is asserted by the probe reporting `document.hasFocus()` over the control channel between blocks.
- **Scheduling:**
  - `SCHED_FIFO` priority 50 (via `chrt`, or `os.sched_setscheduler` with `CAP_SYS_NICE`), pinned to one P-core with `os.sched_setaffinity`.
  - `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)` to an absolute deadline, then spin for the last ~200 µs.
  - Python's `time.clock_nanosleep` does not exist, so use `ctypes` or `time.sleep` plus a spin.
  - GC is disabled during a block.
- For each key: write `EV_KEY 1` then `SYN`, read `CLOCK_MONOTONIC` → `t_inject_down`. After a hold of U[30, 60] ms, write `EV_KEY 0` then `SYN` → `t_inject_up`.
  - Hold time is randomized (D-B 17), and always shorter than the auto-repeat delay.
- **The schedule comes from a seed, before the block starts** (03 §2.1):
  - Inter-trial gap for isolated trials: U[50, 250] ms (spec), counted from the **page-reported settle** of the previous trial (§4.4).
  - Typing cadence inside a query: **U[90, 117] ms** (README S4), *not* waiting for settle, so R1's queueing is measured the way users feel it.
- **Jitter self-test.** This runs at the start of every session and is logged:
  - 2,000 scheduled writes to a *second*, unfocused uinput device, recording `actual − planned`.
  - Separately, `evtest`-style readback of the kernel's event timestamps from the ladder device, recording `kernel_ts − t_inject`.
  - Target: p99 of |actual − planned| < 0.2 ms. The error only jitters the schedule; `t_inject` is measured, not planned.

### 3.4 Mapping injector time to page time

The probe runs an NTP-style exchange with the harness server over a local WebSocket, 20 round trips, **before and after** every block and never inside one:

- The server stamps `time.monotonic_ns()` (which is `CLOCK_MONOTONIC` on Linux).
- The page stamps `performance.now()`.
- Take the offset from the minimum-RTT sample. On localhost the RTT is around 0.1–0.3 ms, so the offset error is under 0.15 ms.
- If the before and after offsets differ by more than 0.2 ms, the block is flagged.

With the offset:

- `t_input_page = t_inject_down + offset`, compared with `event.timeStamp`, gives the per-trial OS delivery segment (§3.2).
- Latency = `presentationTime(marker probe) − t_input_page`.

---

## 4. Harness design

### 4.1 Language and shape

The harness is **Python 3.12 with `uv`**, added as `ladder soft …` subcommands to the Phase 0 orchestrator package (03 §1). It reuses:

- `pydantic` for the manifest and plan
- `polars` and Parquet
- the statistics module (03 §5)
- `typer`
- Jinja and matplotlib for reports

Additions:

- `python-evdev`
- `playwright` for Python, used only for CDP in T and P sessions
- `perfetto` (trace_processor)
- `starlette` + `uvicorn`, or `aiohttp`, for the harness server

The **in-page code is TypeScript**: `probe.ts`, the marker component, and the playground UI. It lives in the harness paths and is built with esbuild. The parity and correctness scripts stay in TS/Node as report 05 plans (F6, F9, F10).

Rationale:

- The data model, stats and reports must match the Phase B/C rig output, so the software and photodiode numbers can sit in one table later.
- The only Python-hostile piece is in-page JS, which is TS either way.

```
 laptop (DUT, also runs the harness in Phase A — acknowledged perturbation, §7.2)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ ladder soft run ──plan/seed──► injector (SCHED_FIFO) ──/dev/uinput──► kernel │
 │      │                                                  │                    │
 │      │                                   libinput → KWin → wl_keyboard.key   │
 │      ▼                                                  ▼                    │
 │ harness server :8700 ◄─ proxy ─ rung servers      Chrome (pinned, clean      │
 │  - injects probe.js, COOP/COEP                    profile, own launch)       │
 │  - clock-sync WS, beacon sink ◄──── end-of-block flush ── probe.js           │
 │  - playground + blind routes                                                 │
 │      │                                                                       │
 │      └─ T sessions only: CDP → Tracing.start(perfettoConfig) → trace.pftrace │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Chrome launch

- **Binary:** Chrome for Testing at a pinned version, or policy-frozen Chrome stable (D-B 12). Record the version, the full command line (read from `chrome://version` in a P session) and `chrome://gpu` in the manifest.
- **Launch Chrome ourselves**, never with Playwright's `launch()`, which adds automation flags and on root adds `--no-sandbox` (05 §4). Allowed flags:
  - `--user-data-dir=<fresh tmp>`, a new profile per block
  - `--no-first-run`, `--no-default-browser-check`
  - `--ozone-platform=wayland`, explicit even though it is the default
  - `--start-fullscreen`, or none for the maximized-window variant
  - `--remote-debugging-port=0` in T and P sessions only
  - the URL
- **A flag lint** rejects anything on a denylist: `--no-sandbox`, `--disable-gpu-vsync`, `--disable-frame-rate-limit`, `--disable-web-security`, `--enable-automation`, `--disable-renderer-backgrounding`, `--disable-features=…Throttling…`, `--enable-unsafe-*`, and similar.
- **One Chrome process per block.** Kill it, wait for the process tree to exit, then idle for 10 s before the next block.

### 4.3 Harness server

- Reverse-proxies each rung:
  - R1 runs `next start` on its own port.
  - R1-vite, R2, R3 and R4 are static builds served directly.
  - All are exposed under one origin: `/r/<opaque-token>/`.
- **Tokens are random per session**, so URLs and titles do not reveal the rung (needed for blind mode, §5). The rung's `<title>` is overwritten to "Palette".
- Injects `<script src="/__ladder/probe.js">` as the first child of `<head>`, and adds COOP/COEP headers.
- Serves `marker.json`, the dataset files (`/dataset/items.json`, identical bytes for all rungs), the playground, and `/__ladder/sync`, a WebSocket used for clock sync, settle signals and end-of-block flush.

### 4.4 Latency marker (rig-ready now)

- The **harness-owned marker component** comes in variants: React (for R1/R2, reading cmdk's store as in 05 §3.3), vanilla (R3), and a canvas draw call (R4).
- It uses a **fixed device-pixel rectangle** from `marker.json`: near the top-left, **128 device px** on the X1 (README S3; 06 §1). It is pure `#000`/`#fff`, with no transition.
- It must be written **in the same JS task as, and after, the last list DOM mutation** for that query (05 §3.3 rule). For canvas it goes in the same draw submission.
- On every flip it also:
  1. calls `performance.mark('ladder:flip', {detail:{seq}})`;
  2. replaces a child `<span elementtiming="ladder-flip-<seq>">▮</span>` inside the marker, in the marker's current colour. This is the presentation probe (§0.4). For R4, the span sits in a DOM overlay over the canvas marker, written in the same task. **[verify]** that it paints in the same frame and that a same-colour glyph counts as contentful;
  3. appends `{seq, query, top50_digest, n_results}` to the probe buffer.
- **The software same-frame check** runs on every trial in M and T sessions, as a health flag. It asserts:
  - the marker's and the list's last mutations happened in the same task, with the marker mutation last;
  - both were recorded under the same rAF frame counter;
  - in T sessions, both fall before the same `Commit`.

  Any violation marks the trial `marker_dishonest` and the block unhealthy.

### 4.5 Scripted trials

**Scenario A (keyboard)**, per block:

- **A.first_key.** An isolated keystroke into an empty palette. The trial is key-down, then wait for settle, then Backspace (itself recorded as trial `A.clear`), then settle, then the U[50, 250] ms gap. About 60% of trials. This is the headline candidate (D-C 30).
- **A.seq.** A 20-character query from report 05's **rig-typeable subset** (lower-case ASCII, digits, space; US layout), typed at U[90, 117] ms, then 20 backspaces at the same cadence. Every key is a trial row, with `seq_pos` 0..39. Queued input is kept.
- **Optional C.scroll.** 10 s of `REL_WHEEL` / `REL_WHEEL_HI_RES` from a second uinput device, a pointer. Dropped or late frames are counted from the T-session trace (`chrome_event_latencies` for `GESTURE_SCROLL_UPDATE` plus the stdlib scroll-jank modules) and from probe rAF gaps. Informative only in Phase A.
- **Warm-up and counts:**
  - 50 warm-up trials, then 500 measured trials per rung × scenario × refresh rate (spec). 1,000 if p99 is quoted (D-C 31).
  - **Rung order is randomized** per session with ABBA-style interleaving: each rung appears in ≥ 2 blocks per session.
  - Repeat on a second day if possible (optional in Phase A).
- **Settle detection.** The probe declares "settled" when:
  - the marker's Element Timing presentation entry for the last flip has arrived, **and**
  - there has been no DOM mutation for 2 frames.

  It sends one byte on the WebSocket. This happens after the measured interval has closed, so it does not perturb the trial. A trial with no settle within 2 s gets status `timeout`.
- **Correctness per trial.** After the block, compare each flip's `top50_digest` against `ranker-ts` (05 F3) for that query:
  - strict for R2/R3/R4;
  - tie-insensitive for R1 (05 §0.1 / S9).

  Mismatches get status `wrong_result`. A rung with more than 0 wrong results is non-parity for Phase A.

  Full `ladder conform` runs (05 F6) happen in P sessions.

### 4.6 Data model (a Phase A variant of 03 §4.3)

The session layout and manifest are the same as 03 §4.2/4.4, with `rig: "none"`, `input_source: "uinput"`, and `session_kind: M|T|P|playground`.

`trials.parquet` has one row per injected key-down:

| column | notes |
| --- | --- |
| session_id, block_id, rung, rung_variant, rung_build_id, dataset_size, hz, display_mode | `display_mode` is `fullscreen` or `maximized` |
| scenario, action_kind (`A.first_key`/`A.clear`/`A.seq`), seq_pos, key, query_prefix | |
| t_inject_down_ns, t_inject_up_ns, planned_pre_delay_us, actual_pre_delay_us | Injector, `CLOCK_MONOTONIC` |
| clock_offset_ns, clock_offset_err_ns | Page ↔ monotonic (§3.4) |
| t_event_ms | `event.timeStamp` of the keydown (page clock) |
| et_start, et_processing_start, et_processing_end, et_duration, interaction_id | Event Timing. Null if below the 16 ms threshold |
| flip_seq, t_flip_task_ms, t_marker_paint_ms, t_marker_present_ms | Marker probe (Element Timing) |
| frame_idx_event, frame_idx_flip, frames_between | rAF counter |
| **lat_present_ms** | **Headline:** `t_marker_present − (t_inject_down + offset)` |
| lat_os_delivery_ms | `t_event − (t_inject_down + offset)` |
| lat_input_delay_ms, lat_processing_ms, lat_to_flip_ms, lat_flip_to_present_ms | Decomposition |
| status | `ok`, `timeout`, `wrong_result`, `marker_dishonest`, `no_probe_entry`, `clock_flag` |
| trigger_phase | 0..1 within the frame, from the rAF-timestamp grid, for aliasing checks (03 §5.5) |

T sessions also write `stages.parquet`: one row per EventLatency, with a stage column each, `t_present_marker_trace`, and the join to `trials` by key order and timestamp.

### 4.7 Statistics and report

These reuse 03 §5.

- **Percentiles:** Hyndman-Fan type 7 for p50/p95/p99. **Bootstrap CIs:** 10,000 resamples, BCa for p50/p95, percentile for p99. Use a moving-block bootstrap if the lag-1..10 ACF is significant; that is likely for `A.seq`, where queueing couples neighbouring keys.
- **Ratios and differences**, R1 vs R3 (and R2 vs R3, R1 vs R2):
  - p95 ratio and p95 difference in ms and in frames, `Δ / (1000/hz)`
  - CIs from independent bootstrap of both arms. A difference of quantiles is not a quantile of differences (03 §5.6).
- **Frame-quantization view:**
  - latency histogram with frame gridlines
  - latency vs trigger phase (the sawtooth)
  - mode weights (03 §5.4 caveat)
- **Report** (`ladder soft report`, a single static HTML page):
  1. Gate A table: per rung × Hz × dataset size, p50/p95/p99 with CIs, with the ratio and frames vs R1.
  2. Per-rung ECDFs on one axis (log x), plus a strip plot of all trials.
  3. `A.seq` latency vs `seq_pos` (shows R1 queue growth).
  4. Stacked stage breakdown per rung from T sessions:
     - OS delivery → input delay → processing → to flip (worker or async) → flip to present;
     - within "to present", the EventLatency stages (§2.1), including the KWin/flip wait.
  5. Health: counts by status, same-frame check failures, clock-sync error, injector jitter, trace-overhead estimate (§2.6), CPU frequency summary (06 §6).
  6. Environment manifest diff between sessions.

---

## 5. Playground and blind mode

### 5.1 Open mode

`/play` shows the palette with a rung picker and a dataset-size picker. It has a live HUD: the last 20 `lat_present_ms` values from the probe, plus Event Timing input delay. The owner types freely, and the page shows the latencies of their own keystrokes.

For this mode `t_input` is `event.timeStamp`, because the injector is not in the loop. That leaves out OS delivery, so the HUD says so.

### 5.2 Blind pairwise mode (primary)

This is a two-interval forced choice (2AFC), "which felt faster":

1. The server draws a pair (X, Y) of conditions and a random order. Assignments come from a seeded, **server-side** table that the page never sees; the page only sees opaque tokens (§4.3).
2. **Interval 1.** The page loads condition A behind a neutral "get ready" screen held for a **fixed 3 s** after load completes, so that load time does not reveal the rung. The owner types a shown prompt word, then a short query (5–8 characters, drawn from the typeable query set). They may backspace freely. The interval ends with Enter.
3. **Interval 2.** Same, with condition B.
4. The owner answers "1 or 2 felt faster", with confidence 1–3, and optionally "no difference" (logged, but forced-choice trials are analysed separately).
5. Log per trial:
   - the pair, order, answer, confidence and response time
   - all probe latencies measured during both intervals, so the answer can be related to the *actual* latency difference in that trial

**Visual leakage checks.** Before blind trials:

- rungs must pass the visual-fidelity diff against R1 (05 F11);
- identical fonts and theme;
- no rung-specific loading skeleton;
- identical dataset bytes;
- the palette is scrolled to the top after load.

The 50k-item R1 may show janky scrolling or typing that no other rung shows. That *is* the latency difference, so it is allowed.

**Trials and statistics:**

- Per pair, the null is p = 0.5, and the test is a one-sided exact binomial.
- Power at α = 0.05 and 80%: detecting a true 75% correct rate needs about 23 trials; a true 65% rate needs about 67.
- **Plan 40 trials per pair per session**, about 15–20 min per pair. Pairs: R1–R3, R1–R2, R2–R3, plus R3–R3 as a catch/placebo pair. Interleave the pairs randomly within a session.
- Report per pair:
  - percent correct with a Clopper–Pearson CI
  - d′ for 2AFC
  - a logistic fit of P(correct) against the *measured* Δp50 per trial
- Across sessions on different days, pool with a mixed model, or report each day separately.

**ABX** (X is A or B; which is it?) is offered as an option but not recommended as the default. It loads memory more heavily, and 2AFC with a known "faster" answer answers Gate A directly.

### 5.3 Artificial-latency calibration (the owner's own threshold)

This measures how big a difference the owner can perceive. It uses **one rung, R3**, with an added delay of *N* ms. With a single rung there are no visual or behavioural cues other than latency.

- **Two modes for adding delay**, each implemented by the harness-owned marker/probe hook, not by R3's own code:
  1. **defer:** hold the finished list and marker update, and apply both at the first rAF where `now ≥ event.timeStamp + N`. The result is frame-quantized, like real latency.
  2. **block:** busy-wait *N* ms in the input handler, which mimics R1-style main-thread cost and also delays subsequent input.

  Report which mode was used. Default to *defer*.
- **Procedure.** The reference is N = 0 and the comparison is N ∈ {8, 17, 25, 33, 50, 67, 100} ms, as 2AFC "which felt faster". Use either:
  - the method of constant stimuli, 20 trials per level (160 trials, about 45 min, split over two sittings), or
  - an adaptive 1-up-3-down staircase converging on 79.4%, about 50–60 trials.
- **Output.** A logistic or Weibull psychometric fit, and the threshold at 75% correct (the owner's JND for added keystroke latency) with a bootstrap CI.
- **Check the added delay** with the probe (`lat_present_ms` distributions per N). At 60 Hz a requested 8 ms adds 0 or 1 frames.
- Published thresholds for indirect input (keyboard or mouse) vary widely, from about tens of ms up to around 100 ms depending on task (e.g. [Deber et al., CHI 2015](https://dgp.toronto.edu/?p=697); [mouse-interaction thresholds, Univ. Lübeck](https://imis.uni-luebeck.de/en/node/7124)). The owner's own number is what matters for interpreting Gate A.
- Run this at **both 60 and 120 Hz**.

### 5.4 Data

Playground and blind sessions write `judgements.parquet`:

- session, trial, condition A and B (rung, N, size, Hz), order
- answer, confidence, rt_ms
- per-interval latency summaries (p50/p95 of `lat_present_ms`, and the count of keys)

Analysis lives in `ladder soft report blind`.

---

## 6. Cheap physical sanity check (iPhone 240 fps slow motion)

**Purpose:** confirm on a few dozen presses that (a) the software numbers track photons, and (b) the R1–R3 gap seen in software is the gap seen in light. This is not a precision measurement.

- **Setup:**
  - iPhone slow motion at 240 fps (4.17 ms per frame), on a tripod.
  - The frame shows the laptop keyboard (the finger and one key) *and* the marker area. Use 60 Hz first, where one display frame is four camera frames, then 120 Hz.
  - Brightness at 100% (06 §1). Mind the 240 Hz OLED PWM, which will show as brightness banding; use the marker's black→white edge.
  - Lock exposure and focus on the marker.
- **Procedure:**
  1. With the probe HUD running in open mode, the owner presses one key about every 2 s, 30 times per rung, for R1 and R3 (and R2 if time allows).
  2. In the video, for each press, mark the frame where the key visibly bottoms out and the first frame where the marker changes.
  3. Latency_cam = Δframes × 4.17 ms, ± 4.17 ms quantization. The iPhone's rolling shutter adds a few ms of skew between the key and the marker (top of the laptop screen vs the keyboard); it is the same for all rungs.
  4. A small script (`ladder soft slomo`, ffmpeg to frames plus a manual-marking CLI, or a simple mean-intensity detector on the marker ROI) speeds this up.
- **Pass criteria:**
  - The median of (latency_cam − probe latency for the same press) is similar across rungs, within ±1 camera frame. That offset is the keyboard, OS and camera lag the probe cannot see.
  - The R1−R3 difference in medians from the camera agrees with the software difference within about 1 display frame.
- Record the key-to-`event.timeStamp` offset as a rough "the parts before Chrome" number to compare later with the Phase B photodiode.

---

## 7. Limitations, biases, and what triggers Phase B

### 7.1 What Phase A cannot compare

- **Browser vs native (R5/R6).** A native app has no Event Timing, and its presentation feedback (`wp_presentation`, or KMS flip events in bare KMS) is a different clock path, with different semantics for async flips (06 §3.1: async flip timestamps are fake). Comparing a browser number against a native number needs a common end point: photons. **This is the main reason for Phase B.**
- **Display floor, compositor bypass, tearing.** These are invisible or unreliable to software on this panel.
- **Keyboard, USB polling, keyboard firmware.** Excluded by uinput (§3.1). They are the same across rungs, so the gaps are unaffected; the absolute numbers are too low.

### 7.2 Where it may be biased

| Bias | Direction | Mitigation |
| --- | --- | --- |
| Harness running on the DUT (injector, server, Python) | Adds CPU and scheduler noise to all rungs; could widen tails | Injector on one pinned core, GC off; the server idle during blocks; A/A blocks to size the noise; Phase B moves input off-box |
| Trace overhead | Slows T sessions | T numbers are never headline numbers; overhead measured (§2.6) |
| Synthetic input (uinput) | May skip libinput filtering specific to real keyboards; identical for all rungs | iPhone check (§6); Phase B uses real USB HID |
| `wp_presentation` = flip completion, not photons | Misses scanout to the marker row (~0.5–2 ms at the top-left) and OLED response (~1–2 ms); a constant | Marker at the top-left; Phase B measures the constant |
| KWin feedback flags not HW-backed | Presentation time is a software guess | Log `feedback_flags`; require `HW_CLOCK` and `HW_COMPLETION` **[verify]** |
| Chrome stamps input at UI-thread read (§0.2) | Hides OS delivery and browser-UI-thread delays inside Event Timing | Headline uses injector time; OS delivery reported separately |
| Event Timing / EventLatency end at the first frame (§0.1) | Flatters worker and deferred rungs | Headline uses the marker frame |
| Element Timing probe cost or semantics | One extra tiny text node per flip; if it does not paint in the marker frame, the timing is wrong | Same in every rung; cross-checked against the trace join (§2.4) before use |
| Cross-origin isolation | Changes the R1 environment | A/A with and without isolation using the trace metric (§1.4) |
| Laptop platform quirks (400 MHz stuck CPU, PSR) | Bimodal sessions | 06 §6 controls: cold boot, `performance` profile, `xe.enable_psr=0 xe.enable_panel_replay=0`, per-trial frequency log |
| Integer vs fractional scaling, fullscreen vs windowed | Changes KWin's path (direct scanout or composited) | Pin integer scaling; measure both display modes (§7.4) |

### 7.3 Interpreting Gate A

**Pass rule.** Both conditions are required: **perceptibility is a hard requirement (decided 2026-09-30)**. The gap thresholds in condition 1 are still proposals. Proceed to build R4/Phase B and invest in the rig only if both hold:

1. **The gap is large.** At the headline settings (60 Hz, headline dataset size, `A.first_key`, M sessions), either:
   - p95(R1) / p95(R3) ≥ 3 with the lower CI bound ≥ 2, **or**
   - p95(R1) − p95(R3) ≥ 2 frames, with the lower CI bound ≥ 1 frame. That is 33 ms at 60 Hz or 17 ms at 120 Hz.

   Report `A.seq` alongside; it will probably show a larger gap, because R1 queues.
2. **It is perceptible.** The R1–R3 blind 2AFC is significantly above chance (one-sided binomial p < 0.05, ≥ 40 trials) and the R3–R3 placebo pair is at chance. The measured Δ should also sit above the owner's JND from §5.3.

**Reading the outcomes:**

| Result | Meaning | Next |
| --- | --- | --- |
| Large gap and perceptible | Lazy tax exists and matters | Phase B photodiode, to check the gap survives to photons and to open the browser-vs-native comparison |
| Large gap, not perceptible | **Gate A fails.** The owner's threshold is above the gap, or the scenario is not what users feel | Before stopping: check the JND, and try 10k vs 50k and 60 Hz to see whether some realistic setting is perceptible |
| Small gap | The browser pipeline dominates, or R1 is fast at this size | The browser tax (R5) becomes the interesting question, and that needs Phase B |
| Gap only at 50k | Size-dependent lazy tax | Informs D-C 24 |

### 7.4 Cheap extra blocks worth running in Phase A

- 50k vs 10k (S11)
- 60 vs 120 Hz
- fullscreen vs maximized window (D-B 10)
- R1 (Next) vs R1-vite (D-C 25)
- PSR on vs off (optional)

Each costs about 15 minutes of machine time per rung.

### 7.5 Triggers for Phase B (minimal photodiode)

Any one of these:

- Gate A passes. Photons are needed to publish, and to compare the browser with native.
- The iPhone check disagrees with software by more than one display frame, in the gap or in the offset between rungs.
- KWin presentation feedback lacks the `HW_CLOCK` or `HW_COMPLETION` flags, or the Element Timing probe cannot be validated.
- The owner wants R5/R6 numbers at all.

---

## 8. Task breakdown, dependencies, risks, decisions

### 8.1 Tasks

AH = agent-hours (code drafted by agents, reviewed by a human); HD = human-days (owner time on the laptop, decisions, blind trials).

| # | Task | Est. | Depends on |
| --- | --- | --- | --- |
| A1 | **Spike on the laptop.** Verify: Element Timing re-reports inserted probe spans with `presentationTime`; KEY_PRESSED EventLatency stages exist; `feedback_flags` from KWin; the §2.4 marker-frame join key; the trace clock domain. Measure uinput→`event.timeStamp` delivery and injector jitter | 5–7 AH + 0.5 HD | A13 |
| A2 | `probe.ts`: observers, capture listeners, rAF/MessageChannel frame counter, MutationObserver same-frame check, buffer, clock sync, flush | 6–8 AH | A1 (shape) |
| A3 | Marker components (React via `useCommandState`, vanilla, canvas), with `performance.mark` + Element Timing probe span + digest; `marker.json` | 4–5 AH | 05 F7 marker design; A1 |
| A4 | Harness server: proxy + probe injection + COOP/COEP + opaque tokens + dataset serving + WebSocket control/sync + beacon sink | 5–6 AH | — |
| A5 | uinput injector: seeded schedule, SCHED_FIFO, absolute sleeps, logging both clocks, jitter self-test, udev rule | 4–5 AH | — |
| A6 | Chrome launcher: pinned CfT, fresh profile, flag lint, fullscreen/maximized, focus check, process lifecycle | 3–4 AH | — |
| A7 | Session runner `ladder soft run`: plan (rung order, blocks, warm-up), settle handshake, M/T/P kinds, manifest and env capture (reusing 03 §3.2 and the 06 §6 fields) | 6–8 AH | A2, A4–A6 |
| A8 | Tracing: CDP `Tracing.start(perfettoConfig)` streaming, trace_processor SQL (`chrome_event_latencies`, stage pivot, marker-frame join), `stages.parquet` | 6–8 AH | A1, A7 |
| A9 | Correctness per trial against `ranker-ts` (strict or tie-insensitive) | 2–3 AH | 05 F3, F6 |
| A10 | Analysis: `trials.parquet` build, stats (reusing or building 03 §5.1/5.4/5.5/5.6), frame-phase diagnostics, M-vs-T overhead | 5–7 AH (+4 AH if 03's stats are not built yet) | A7, A8 |
| A11 | Report HTML: Gate A table, ECDFs, `seq_pos` plot, stage stacks, health | 5–6 AH | A10 |
| A12 | Playground: open mode + HUD; blind 2AFC (server-side assignment, fixed 3 s ready screen, logging); placebo pairs; `judgements.parquet`; binomial/d′/logistic analysis | 8–10 AH | A2–A4 |
| A13 | Laptop setup: 06 §6 subset (performance profile, PSR off, integer scaling, Night Light off, notifications off), pinned Chrome, udev rule, KWin focus behaviour | 0.5 HD | — |
| A14 | Latency-injection calibration (defer and block modes, constant stimuli or staircase, psychometric fit) | 4–5 AH + 0.5 HD owner | A12 |
| A15 | iPhone 240 fps procedure + frame-marking helper | 2 AH + 0.5 HD | A2, rungs |
| A16 | Measurement runs: A/A, M sessions for all rungs × {60, 120 Hz} × {50k, 10k} × display mode; T sessions; optional day 2 | 1.5 HD (mostly unattended machine time) | A7–A11, rungs |
| A17 | Blind sessions: about 4 pairs × 40 trials, over at least 2 sittings | 1 HD owner | A12, rungs |
| A18 | Gate A write-up | 0.5 HD | all |

**Total for harness and playground: about 70–90 AH, plus about 5 HD of owner time.** With agents drafting in parallel, this is about 1.5–2 calendar weeks, and A1 is the first gate.

The critical path is: A1 spike → A2/A3 probe + marker → A7 runner → A16 runs, then report.

A12 (playground) runs in parallel with A7–A11.

### 8.2 Dependencies (outside this scope)

- **05 F1/F2:** dataset and query generator, including the rig-typeable subset.
- **05 F3:** `ranker-ts`.
- **05 F6:** `ladder conform`.
- **05 F7:** R1 (Next) and R1-vite, frozen, with the marker from A3.
- **05 F11:** visual diff, required before blind mode.
- **Rungs** (rough, for planning only): R2 about 8–12 AH; R3 about 12–20 AH (worker, recycled row pool, marker rule); R4 about 40–60 AH if attempted, much of it for the accessibility mirror and IME, which Phase A need not complete (report R4 as non-parity).
- **03:** manifest schema, stats module, report scaffolding. If 03 is not yet built, A10 builds the stats pieces in 03's module layout so Phase C inherits them.

### 8.3 Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| The Element Timing probe does not fire repeatedly, or not in the marker frame | No in-page presentation time | Use the trace-based marker-frame join (§2.4) for all sessions; the headline then comes from T-style sessions with minimal categories, and overhead is reported |
| KWin feedback is not HW-timed | Presentation times are guesses | Check flags; fall back to "frame count × period + rAF grid"; bring Phase B forward |
| R1 at 50k queues so badly that settle rarely happens within 2 s | Timeouts, `A.first_key` unmeasurable | Longer settle timeout for R1; report timeouts; this is itself a result (S11) |
| Rung identity leaks in blind mode (load time, visual differences, jank that is not latency) | Blind test measures something other than latency | Fixed ready screen, fidelity diff, placebo pair, latency-injection mode on a single rung |
| Owner fatigue or learning across blind trials | Drifting accuracy | Short sittings, randomized pairs, report by sitting |
| Harness on the DUT perturbs results | Inflated tails, equal across rungs | A/A noise band; Phase B moves input off-box |
| Chrome update or KWin update between sessions | Non-comparable sessions | Pin, record, diff the manifests (06 §6) |
| Sep 2026 Chrome semantics shift (Event Timing gains `presentationTime`) | Changes to secondary metrics | Log `PerformanceEventTiming.prototype` keys in the manifest; use `presentationTime` when present |

### 8.4 Open decisions for the owner

1. **Headline metric for Gate A.** `lat_present_ms` on `A.first_key` at 60 Hz (recommended), with `A.seq` reported alongside.
2. **Gate A thresholds.** ≥ 3x p95 **or** ≥ 2 frames (§7.3). *Decided: blind 2AFC above chance is a hard requirement.*
3. **Dataset size for Phase A headline.** Run both 50k and 10k (recommended); pick one for the gate.
4. *Decided: cross-origin isolation for all rungs.* Still open: whether rungs may use `SharedArrayBuffer`.
5. **Display mode:** fullscreen, maximized, or both (both recommended in Phase A).
6. **Blind protocol:** 2AFC (recommended) or ABX; trials per pair (40 recommended); whether "no difference" is allowed.
7. **Latency-injection mode:** defer (recommended) or block, or both.
8. **Is R4 in Phase A?** Recommended only if Gate A's R1→R3 result is ambiguous. R4 without its accessibility mirror is non-parity anyway.
9. **Second day for Phase A sessions:** recommended but optional; the spec's 2 ms criterion is not applied until Phase C.
10. **Where `ladder soft` lives:** in `orchestrator/` as a subpackage (recommended), so Phase C reuses the data model.

---

## Sources

- W3C Event Timing: https://w3c.github.io/event-timing/
- W3C Paint Timing (PaintTimingMixin, presentationTime coarsening): https://w3c.github.io/paint-timing/
- W3C Element Timing: https://w3c.github.io/element-timing/
- Chrome 145 release notes (paintTime/presentationTime; Event Timing omitted): https://developer.chrome.com/release-notes/145
- blink-dev Intent to Ship, presentationTime/paintTime (M144): http://www.mail-archive.com/blink-dev@chromium.org/msg15314.html and discussion http://www.mail-archive.com/blink-dev@chromium.org/msg15368.html
- Chromium IDL `performance_event_timing.idl`, `paint_timing_mixin.idl`, `performance_long_animation_frame_timing.idl`: https://github.com/chromium/chromium/tree/main/third_party/blink/renderer/core/timing
- Chromium `window_performance.cc` (presentation promise, fallbacks): https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/timing/window_performance.cc
- Chromium INP/Event Timing changelog: https://github.com/chromium/chromium/blob/main/docs/speed/metrics_changelog/inp.md and the Mac presentation-feedback change https://github.com/chromium/chromium/blob/main/docs/speed/metrics_changelog/2024_06_inp_lcp_fcp.md
- Long Animation Frames API: https://developer.chrome.com/docs/web-platform/long-animation-frames
- web.dev INP: https://web.dev/articles/inp
- Timer resolution and cross-origin isolation: https://developer.chrome.com/blog/cross-origin-isolated-hr-timers
- Chromium `cc/metrics/event_latency_tracing_recorder.cc`: https://github.com/chromium/chromium/blob/main/cc/metrics/event_latency_tracing_recorder.cc
- Chromium `cc/metrics/compositor_frame_reporter.cc`: https://github.com/chromium/chromium/blob/main/cc/metrics/compositor_frame_reporter.cc
- Perfetto stdlib `chrome/event_latency.sql`: https://github.com/google/perfetto/blob/main/src/trace_processor/perfetto_sql/stdlib/chrome/event_latency.sql
- Perfetto trace processor (Python): https://perfetto.dev/docs/analysis/trace-processor-python ; stdlib docs: https://perfetto.dev/docs/analysis/stdlib-docs
- CDP `Tracing` domain (perfettoConfig, ReturnAsStream, proto): https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Tracing.pdl
- CDP `Input` domain: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Input.pdl ; DevTools input handler: https://github.com/chromium/chromium/blob/main/content/browser/devtools/protocol/input_handler.cc
- Chromium Ozone Wayland keyboard and time conversion: https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/host/wayland_keyboard.cc , https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/common/wayland_util.cc
- Chromium Ozone Wayland frame manager (wp_presentation feedback): https://github.com/chromium/chromium/blob/main/ui/ozone/platform/wayland/host/wayland_frame_manager.cc
- presentation-time protocol and compositor support: https://wayland.app/protocols/presentation-time
- Chrome 140 Wayland auto-detection: https://www.phoronix.com/news/Chrome-Auto-Ozone-Platform
- Deber, Jota, Forlines, Wigdor, CHI 2015, "How Much Faster is Fast Enough?": https://dgp.toronto.edu/?p=697
- Latency perception thresholds in mouse interaction (Univ. Lübeck): https://imis.uni-luebeck.de/en/node/7124
