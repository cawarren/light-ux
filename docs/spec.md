# Latency Ladder: Project Spec

Sep 29, 2026 · @Andrew Warren

## Summary and hypothesis

We will build one UI interaction six times, each one layer lower in the stack, from a stock shadcn app down to a native app that bypasses the OS compositor. We measure keystroke-to-photon latency on every rung. The difference between adjacent rungs is what that layer costs the user.

The hypothesis (Wirth's law): decades of hardware gains have been absorbed by software layers, so modern UIs feel no more responsive than older ones. The test has to split that absorbed cost into two parts:

- **Lazy tax:** latency that disappears with more engineering effort, with no loss of capability.
- **Feature tax:** latency that pays for something users need, such as tear-free compositing, Unicode shaping, accessibility, IME input, sandboxing, or high-DPI rendering.

**Confirms the hypothesis:** the fastest rung that passes full parity is at least 3x faster than rung 1 at p95, and most of the gap is lazy tax.

**Refutes it:** the parity-passing floor is within 20 ms of rung 1 at p95, or most of the gap turns out to be feature tax.

## Goals and non-goals

Version 1 answers one question: how much latency does each layer of a typical web UI stack add, and how much of it can be removed without losing features?

**Goals**

1. A per-layer latency breakdown for one realistic interaction, reported at p50, p95 and p99.
2. The lowest latency achievable while passing the full parity checklist.
3. A reusable, automated measurement harness that agents can optimize against.
4. Reproducible, publishable results: code for every rung, the rig design, and raw data.
5. A record of what each rung cost to build, in agent-hours and lines of code.

**Non-goals for version 1**

- A production UI library. Rungs are test fixtures, not products. Extracting a library is Phase D.
- Network and data-layer latency, such as spinners and round trips. A local-first rung is Phase D.
- Browsers other than Chrome. Safari and Firefox come after the first results.
- Visual design beyond matching the default shadcn look.

## Test interactions

The primary scenario is a command palette filtering 50,000 items, because it exercises input, state, list diffing, text layout, paint and scroll in one small component. shadcn's Command component (built on cmdk) is the baseline.

| Scenario | Stresses | Trigger (sent by the rig) | Measured end point |
| --- | --- | --- | --- |
| A. Palette filter (primary) | Input handling, filtering, list diff, text layout, paint | One keystroke into a focused, empty palette; then a 20-character query at 100 ms per key; then backspaces | First photon change in the marker region after each key |
| B. Drag | Pointer path, hit testing, frame pacing | Mouse moves a 96 px card along a fixed path at constant speed | Card edge crossing the photodiode position |
| C. Scroll | Frame pacing, virtualization, raster | 10 s of scripted wheel input through the full result list | Dropped or late frames, from present timestamps and camera |
| D. Cold start | Bundle size, hydration, process launch | Launch or navigate from a blank tab, then one keystroke | Time until that keystroke is visible |

**Dataset.** 50,000 items from a fixed seed, 8 to 80 characters each. About 5% contain CJK, Arabic, Hebrew, Devanagari or emoji sequences, so text shaping is exercised. A size sweep (1k, 10k, 100k) runs once the main results are in.

**Fixed semantics, free implementation.** Every rung must return identical ranked results for the same query, using the reference ranking from cmdk's scoring. Rungs may implement the search however they like, including indexes or worker threads.

**Latency marker.** Each frame draws a 64 px square in a fixed corner, that flips between black and white each time the query changes. It must be drawn by the same code path, in the same frame, as the list update. The parity suite verifies this with a high-speed camera, so a rung cannot update the marker early.

## The ladder

Each rung removes one layer, so the latency difference between rung N and rung N+1 is that layer's cost. Rungs 1 to 5 must pass the full parity checklist; rung 6 is a physical-floor reference.

| Rung | Stack | Layer removed | What the delta isolates |
| --- | --- | --- | --- |
| R1 Typical | Next.js, React, shadcn Command (cmdk), Tailwind; default config, production build, no virtualization | None (baseline) | What users get today |
| R2 Diligent | R1 plus list virtualization, memoization, deferred rendering, a precomputed search index | None; same stack, more care | The "nobody bothered" share of the lag |
| R3 No framework | Hand-written TypeScript on the DOM; a fixed pool of recycled row nodes, CSS containment, no layout reads in the write path, filtering in a Web Worker | Framework and virtual DOM | Framework tax |
| R4 No DOM | Rust compiled to WASM, rendering to WebGPU (canvas 2D fallback); own glyph atlas, layout and hit testing; low-latency canvas hints; hidden DOM mirror for accessibility and IME | DOM, CSS style and layout engine | DOM tax |
| R5 Native | Rust with wgpu or GPUI; platform text shaping; AccessKit for accessibility; low-latency present mode with one frame in flight | Browser | Browser tax |
| R6 Floor | R5 plus compositor bypass (independent flip on Windows, direct scanout on Linux), just-in-time rendering before vblank, 240 Hz display | OS compositor and frame queuing | The physical floor for this hardware |

**Rules that apply to every rung**

- Same dataset, same ranked results, same visual design within the fidelity tolerance.
- R1 is built the way a typical team would ship it and is never optimized by agents.
- R2 may only use techniques from an allow-list of documented, idiomatic React practices.
- R4 and R5 must implement accessibility and IME themselves; skipping them fails parity.

## Parity checklist

A rung that fails any check is still measured but reported as non-parity, so it cannot count toward confirming the hypothesis. Timing each check's cost is itself a finding: it measures the feature tax directly.

| Check | Pass criterion | Verified by |
| --- | --- | --- |
| Screen reader | VoiceOver and NVDA announce the input, the result count and the active item | Scripted manual run plus accessibility-tree dump |
| Keyboard | Arrows, Home, End, Page Up, Page Down, Enter and Escape work; focus is visible | Automated key script |
| IME | Japanese and Chinese composition work; candidate window sits at the caret; no dropped input | Manual run on each OS |
| Text shaping | Correct rendering of Arabic, Hebrew, Devanagari, CJK and emoji sequences | Screenshot diff against R1 |
| Text editing | Select, copy, paste and undo work in the input | Automated script |
| Scaling | Crisp at 100%, 150% and 200% OS scaling; browser zoom works on web rungs | Screenshot inspection |
| Theming | Light and dark modes; respects reduced motion and increased contrast | Screenshot diff |
| Visual fidelity | Perceptual diff against R1 below an agreed threshold | Automated diff |
| Correct results | Identical ranked results to the reference on 1,000 held-out queries | Automated comparison |
| Tear-free | No tearing visible in high-speed capture (R6 may test tearing as a variant) | 1,000 fps camera |
| Marker honesty | Marker and list change in the same frame | 1,000 fps camera |
| Sandbox | Web rungs run with default browser security; no flags that weaken it | Launch config review |

## Measurement harness

*Phase C (see Phased plan). Phase A uses software instrumentation instead; Phase B uses a minimal version of this rig.*

The harness is the real steel thread: a microcontroller posing as a USB keyboard and mouse sends input, and a photodiode on the screen times the result. It captures the whole pipeline, including the OS compositor and the display, which in-app timers miss.

&#91;embedded content: Measurement rig · 6 parts, one measured loop\]

The measured loop never passes through the orchestrator, so nothing on the measuring side can slow the machine under test.

**Components**

- **Microcontroller:** a Teensy 4.x or RP2040 that enumerates as a USB keyboard and mouse at 1,000 Hz polling. It timestamps every report it sends and every light change it sees.
- **Light sensor:** a photodiode with an amplifier, sampled at 20 kHz or faster, held over the latency marker by a suction mount.
- **Orchestrator:** a separate computer that runs trial scripts, switches rungs and stores results. It never runs on the machine under test.
- **Attribution traces:** Perfetto traces for Chrome, PresentMon on Windows and Instruments on macOS. These run in separate sessions so tracing overhead never touches the headline numbers.
- **Validation camera:** a 1,000 fps camera to spot-check the photodiode and verify marker honesty and tearing.

**Protocol**

- At least 500 trials per rung, scenario and machine, after 50 warm-up trials.
- A random 50 to 250 ms delay between trials, so input never locks to the display's refresh phase.
- Rung order randomized per session, and each session repeated on a second day.
- Display at full brightness, so backlight flicker does not trigger the sensor.

**Calibration**

- **Rig latency:** an LED driven directly by the microcontroller measures the sensor path itself.
- **Display floor:** a minimal fullscreen native app that inverts the screen on keypress. No rung can beat this number on that machine.
- **Browser floor:** a blank web page that flips the marker on keydown, before any UI code runs.

## Metrics and success criteria

The headline metric is keystroke-to-photon latency for Scenario A at p95, on the reference machine at 60 Hz. Tails matter more than averages, because jank is what people notice.

**Primary:** keystroke-to-photon latency at p50, p95 and p99, per rung, scenario and machine.

**Secondary:** drag latency at p50 and p95; dropped frames per 10 s of scrolling; cold start to first visible keystroke; peak memory; bundle or binary size; build effort in agent-hours and lines of code.

**For scale:** one frame is 16.7 ms at 60 Hz, 8.3 ms at 120 Hz and 4.2 ms at 240 Hz. Every frame a pipeline queues adds one of these to the total.

**Scorecard template** (filled per machine and refresh rate)

| Rung | A p50 (ms) | A p95 (ms) | A p99 (ms) | Drag p95 (ms) | Dropped frames / 10 s | Cold start (ms) | Peak memory (MB) | Parity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 Typical |  |  |  |  |  |  |  |  |
| R2 Diligent |  |  |  |  |  |  |  |  |
| R3 No framework |  |  |  |  |  |  |  |  |
| R4 No DOM |  |  |  |  |  |  |  |  |
| R5 Native |  |  |  |  |  |  |  |  |
| R6 Floor |  |  |  |  |  |  |  |  |

**Reporting.** Each machine gets a waterfall chart from R1 down to the display floor, one bar per layer's delta. Raw trial data, rig design and all rung code are published with the results.

**Project success criteria** (separate from whether the hypothesis holds)

- The same rung measured on two different days agrees within 2 ms at p50.
- Every rung either passes parity or is explicitly marked non-parity with the failing checks listed.
- Someone outside the project can rebuild the rig and reproduce R1 and R5 within 10%.

## Test environment

Four machines cover the questions that matter: a fast desktop, a second OS, the only OS with direct scanout for R6, and a slow laptop where the lazy tax should hurt most.

| Machine | OS | Display | Role |
| --- | --- | --- | --- |
| Reference desktop (recent mid-range CPU and GPU) | Windows 11 | External monitor, tested at 60 Hz and 240 Hz | Primary; all rungs |
| Same desktop, dual boot | Linux (Wayland and bare KMS) | Same monitor | R6 direct scanout; compositor comparison |
| MacBook Pro (Apple Silicon) | macOS | Built-in 120 Hz variable refresh | Second OS; laptop panel |
| Budget laptop, about 4 years old | Windows 11 | Built-in 60 Hz | Low-end stress; where users feel lag most |

**Controls for every session**

- Plugged in, high-performance power mode, background apps closed, notifications off.
- Fixed resolution, scaling and brightness; variable refresh off except in dedicated tests.
- Chrome stable, version pinned for the whole project; extensions off; clean profile.
- Rig parts cost well under $100 excluding the camera, so anyone can replicate it.

## Phased plan

*Rephased 2026-09-29.* The original plan built the hardware rig first. The revised plan starts with the cheapest experiment that could show the idea is wrong, and adds measurement rigor only after each gate shows there is enough to justify it. The first gaps are likely large: cmdk's scoring alone costs 55–285 ms per keystroke at 50k items (docs/phase-0/05-software-foundations.md). Software instrumentation resolves gaps of that size well. The photodiode rig earns its cost when differences shrink to about a frame, or when a comparison crosses the browser boundary, where in-app timers stop being comparable.

| Phase | Builds | Measures with | Hardware | Gate at the end |
| --- | --- | --- | --- | --- |
| **A. Web ladder, software only** (~2 weeks) | Dataset and reference ranking; R1, R2, R3 (R4 optional) in Chrome; a playground with blind comparison Real OS-level keystrokes (`uinput`) timestamped by the harness, to the on-screen time of the frame where the marker flipped (Element Timing presentation time on the marker; trace fallback). Chrome traces only for the per-stage breakdown. Blind comparison trials; 240 fps phone video for spot checks | None | **Gate A:** is the R1→R3 gap large (≥3× or several frames at p95), **and** can a person tell rungs apart blind more often than chance? Both are required. |
| **B. Cross the browser boundary** (~2–3 weeks) | R5 native (and R4 if not built in A) | A minimal photodiode rig: Teensy and one sensor, simple edge detection, no calibration suite, camera or isolation | ~$40 | **Gate B:** is the browser tax (R4→R5) large enough to warrant frame-level attribution? |
| **C. Rigorous and publishable** (~4–6 weeks) | R6; full parity suite; agent optimization loop | The full rig, calibration and validation camera scoped in docs/phase-0 (reports 01–04, 06, 07); several machines; two-day reproducibility | Full rig, camera | Publish results, rig design and raw data |
| **D. Extensions** | Library extraction, local-first rung, sample app, other browsers, dataset size sweep | As in C | — | — |

**What each gate can and cannot conclude.** Phase A measures only the framework and DOM layers. A small gap at Gate A refutes the lazy-tax part of the hypothesis for those layers. It says nothing about the browser, compositor or display costs, which only Phase B and C can see. Deciding whether to continue past Gate A is therefore two decisions: whether to pursue the web rungs further, and separately whether to pursue the browser boundary.

**Web rungs are served cross-origin isolated** (COOP `same-origin`, COEP `require-corp`), R1 included, so every rung gets the same 5 µs timers. The report notes that a typical production app would not be isolated.

**Kept from the original plan.** Every rung draws the latency marker in the same frame as its list update from day one, so Phase A rungs work unchanged under the rig later. Phase A's data model and statistics are the orchestrator's (docs/phase-0/03-orchestrator.md), with software measurements as the source.

**Performance Olympics after each milestone.** Before each gate review, a two-wave agent competition looks for the largest remaining improvement: first proposals with no limit on implementation complexity, scored by measured rather than predicted gain; then one builder agent per selected proposal, however long the build. See docs/olympics.md.

**Hardware decisions carried forward.** The rig research done before the rephasing is summarized in docs/hardware-notes.md, so Phases B and C start from it.

**Phase A checklist** (detail in docs/phase-a/README.md)

- [ ] Dataset generator and reference ranker (TS and Rust), with golden files
- [ ] R1 (stock shadcn Command), R2 and R3, each drawing the marker in the same frame as the list
- [ ] Software harness: pinned Chrome, harness-timestamped `uinput` keystrokes, on-screen time of each marker flip, correctness checks, p50/p95/p99 report, and a per-stage breakdown from separate traced sessions
- [ ] Half-day check on the laptop of the harness's unconfirmed assumptions (task A1 in docs/phase-a)
- [ ] Playground with blind ABX mode and an adjustable added-latency control, to calibrate what differences are perceptible
- [ ] Phone slow-motion spot check against the software numbers
- [ ] Performance Olympics on R2 and R3 (docs/olympics.md)
- [ ] Gate A review

**Deferred to Phase C** (the original Phase 0 checklist, scoped in docs/phase-0/)

- [ ] Order the microcontroller, photodiode, amplifier and screen mount
- [ ] Write rig firmware: USB keyboard and mouse output, light sampling at ≥100 kS/s (not 20 kHz; see docs/phase-0 S1), timestamped log
- [ ] Write the orchestrator: N trials, randomized delays, Parquet output
- [ ] Build the display-floor app and the browser-floor page
- [ ] Run calibration on two separate days and compare

## How agents are used

With coding capacity effectively unlimited, the bottleneck becomes measurement and verification, not code. Agents therefore optimize against the harness, and humans own the harness, the parity suite and the rules.

**Optimization loop, per rung**

1. An agent proposes a change in that rung's repository.
2. The build runs, then the full parity suite. Any failure rejects the change.
3. The harness runs 200 trials on the reference machine (the software harness in Phase A; the rig from Phase C).
4. The change is accepted only if p95 improves by more than the measured noise band.
5. Accepted changes are re-measured on a second machine before they count.

**Guardrails against gaming the benchmark**

- Agents cannot modify the harness, rig firmware, parity suite or dataset generator.
- Final measurements use held-out datasets and queries the agents never saw.
- Detecting the test environment, dataset or seed is forbidden and checked in code review.
- The marker-honesty check runs on every accepted change (a software same-frame check in Phase A; the second photodiode and camera from Phase C).
- A human reviews any change that touches input handling, frame timing or present modes.

**Performance Olympics.** At each milestone the optimization loop above is run as a competition; see docs/olympics.md.

**Fairness between rungs.** Each of R2 to R5 gets the same agent budget, and actual effort is recorded as a result. The cost of optimization is part of the answer, because it speaks to the business argument for why teams skip it.

## Risks and open questions

The biggest risk is a result that looks dramatic but only reflects a skipped feature or a flattering machine. Most mitigations below exist to prevent that.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Feature-tax cheating | A fast rung wins by dropping accessibility, IME or shaping | Parity suite gates every result |
| Display and OS floor dominates | Software gains look small in absolute terms | Report the software-controllable share separately from the display floor |
| Scenario too easy on fast hardware | R1 looks fine on the desktop, hiding the effect | Budget laptop in the matrix; dataset size sweep |
| Refresh-phase aliasing | Results cluster around frame boundaries and mislead | Randomized trial timing; 500+ trials |
| R4 accessibility mirror is costly | The DOM mirror eats the gain from bypassing the DOM | Measure R4 with and without the mirror; report both |
| One component is not an app | Results may not generalize | Phase D builds a sample app from the fastest rung |
| Agents overfit the benchmark | Gains vanish on real data | Held-out datasets; second-machine confirmation |

**Open questions**

- [ ] Is Windows the right primary OS, or should macOS lead?
- [ ] Should R1 use Next.js, or plain Vite and React? Next adds hydration, which mostly affects cold start.
- [ ] Is 50,000 items the right default, or should the headline use 10,000?
- [ ] Should results be published openly from the start, or after Phase B?
- [ ] Is a 20 ms p95 gap the right bar for "refutes", or should it scale with refresh rate?

## Prior art and references

None of these runs a layer-by-layer ladder under a parity gate, which is this project's contribution. Each supplies a method, a component or a comparison point.

| Work | What it shows | Use in this project |
| --- | --- | --- |
| [Computer latency: 1977-2017](https://danluu.com/input-lag/) (Dan Luu) | High-speed camera measurements found a 1983 Apple IIe beat every modern machine tested on keypress-to-screen latency | Motivation; camera method for validation |
| [Typometer](https://github.com/pavelfatin/typometer) (Pavel Fatin) | Software-only typing latency tool using synthetic keypresses and screen capture; notes that compositing window managers add at least a frame | Cross-check for the rig; shows what software-only timing misses |
| [refterm](https://github.com/cmuratori) (Casey Muratori) | A reference terminal renderer showing how much faster a straightforward design can be than a shipping product | Evidence for the lazy tax; design reference for R4 and R5 |
| [GPUI and Zed](https://zed.dev/blog/videogame) | A Rust UI framework that rasterizes the whole window on the GPU, built after the Atom team could not hold frame rate in Electron | Candidate framework for R5 |
| [AccessKit](https://github.com/AccessKit/accesskit) | Cross-platform accessibility layer for toolkits that draw their own UI | Required for R4 and R5 to pass parity |
| [cmdk](https://github.com/pacocoursey/cmdk) | The command menu behind shadcn's Command component | R1 baseline and reference ranking |
