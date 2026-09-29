# Latency Ladder: Phase 0 scope (rig work now deferred to Phase C)

> **Rephased 2026-09-29.** The plan now starts with a software-only web ladder (Phase A, scoped in [`docs/phase-a/`](../phase-a/README.md)). A minimal photodiode rig comes in Phase B, and the full rig described here in Phase C. See the spec's [Phased plan](../spec.md#phased-plan). This page is kept as the Phase C scope. What it contains now belongs to these phases:
>
> | Content here | New phase |
> | --- | --- |
> | Report 05: dataset, reference ranking, R1, cheap parity checks | **Phase A**, unchanged |
> | Report 06: X1 Carbon setup (xe settings, KWin, whole-number scaling, cold boot, pinned versions) | **Phase A** (the laptop is the Phase A test machine) |
> | Report 03: data model, statistics, reports | **Phase A**, with software measurements as the source; device layer and mock rig in Phase B/C |
> | Corrections S3 (marker), S4 (key cadence), S9–S12 (ranking, keyboard parity, dataset size, marker timing in React) | **Phase A** |
> | Reports 01–02: a minimal subset (Teensy, one BPW34 sensor, HID output, simple edge detection) | **Phase B** |
> | Reports 01–04 and 07 in full: rig, isolation, calibration, floors, camera, second photodiode | **Phase C** |
>
> Of the decisions below, these are needed before Phase A work: 7 (marker placement), 8 (test machine), 10 (windowed vs fullscreen), 12 (Chrome pinning), 13 (Wayland compositor), 15 (panel self-refresh), 17 (key cadence), and all of D-C (22–32). The rest wait for Phase B or C.

Scoped 2026-09-29 against [`docs/spec.md`](../spec.md). This page combines five scoping reports:

| # | Workstream | Detailed report | Estimate |
| --- | --- | --- | --- |
| 01 | Rig hardware and BOM | [01-hardware.md](01-hardware.md) | ~6–6.5 person-days (pd), ~2 calendar weeks with shipping |
| 02 | Rig firmware | [02-firmware.md](02-firmware.md) | ~15.5 pd (12–18) |
| 03 | Orchestrator and results pipeline | [03-orchestrator.md](03-orchestrator.md) | ~28 pd (~12–15 with agents drafting) |
| 04 | Calibration targets and procedure | [04-calibration.md](04-calibration.md) | ~22 pd (~14 dev, 5–8 rig-owner days) |
| 05 | Software foundations for Phase 1 (dataset, reference ranking, R1, cheap parity checks) | [05-software-foundations.md](05-software-foundations.md) | ~56–73 agent-hours + ~2 human-days |
| 06 | Device under test: ThinkPad X1 Carbon Gen 13, 2.8K OLED, Linux | [06-dut-x1-carbon.md](06-dut-x1-carbon.md) | ~0.5 pd bring-up checklist |
| 07 | Validation camera (machine-vision modules) and a second photodiode | [07-camera.md](07-camera.md) | Budget ~$340–380; better ~$800 |

Report 04 was written without web research, so its driver and API claims are tagged **[verify]**. Reports 06 and 07 were added after the owner proposed their laptop as the test machine and asked about camera modules; items in them that lack a primary source are also tagged **[verify]**. Report 05 is backed by runnable spikes in [`spikes/`](spikes/).

## What Phase 0 is

This follows the spec's Phase 0 checklist. The goal is a working, calibrated rig on the reference machine, with day-1 and day-2 calibration results agreeing within tolerance:

1. Order the rig parts (microcontroller, photodiode, amplifier, mount).
2. Rig firmware: USB keyboard and mouse output, light sampling, timestamped log.
3. Orchestrator: N trials, randomized delays, CSV/Parquet output.
4. Display-floor app and browser-floor page.
5. Calibration on two separate days, compared.

Workstream 05 is not on the Phase 0 checklist. It has no hardware dependency, and Phase 1 (R1 and R5, then Gate 1) needs its outputs, so it runs in parallel. That way R1 and R5 can be measured as soon as calibration passes.

## Recommended architecture (all five reports agree)

```
 orchestrator PC ──USB── CP2102N ──isolator── UART 3 Mbaud ──┐
  (Python `ladder` CLI;                                      │
   never on the machine under test)                   Teensy 4.x rig ──USB HID (kbd+mouse only)──► machine under test
                                                             │                                          │
                                             BPW34 + op-amp sensor head ◄──── light from marker ────────┘
                                             (≥100 kS/s, ISR-timestamped, on-device edge detection)
```

- **Rig:** Teensy 4.x. Report 01 specifies the 4.0 for the replication BOM and the 4.1 as the lab unit; they use the same chip. The RP2350 is the fallback. The sensor is a BPW34 photodiode with an MCP6002 transimpedance amplifier (switchable gain). A calibration LED measures rig latency and a second LED is visible to the camera. BOM is about $91 per rig.
- **Machine under test sees only a keyboard and mouse.** There is no serial or vendor interface, so rungs cannot detect or talk to the rig. The orchestrator link is a separate isolated UART.
- **Whole trial scripts are uploaded before `RUN`.** The orchestrator draws the randomized delays from a recorded seed. The firmware runs them on its own clock and echoes back what it actually did. No round trips happen during trials.
- **Timestamps:** "input time" is when the host actually collected the report (the USB IN transfer completed), stamped in the interrupt. "Photon time" is the first change and the 50% crossing, both logged, on one 64-bit µs clock.
- **Protocol:** COBS framing with CRC-16, generated from one schema into C and Python, with a version and firmware-hash handshake.
- **Orchestrator:** Python 3.12, uv, pydantic, polars/Parquet, and a typer CLI (`ladder plan|run|eval|aa|analyze|report|verify`). Raw device bytes are kept immutable and hashed; tables and statistics are rebuilt from them. A **mock rig** speaks the real protocol, so everything is CI-testable before hardware exists.
- **Floor apps:** one Rust workspace that calls each platform's graphics API directly: Windows DXGI flip model, Linux KMS and Wayland, macOS Metal. There are three floors per machine: *composed*, *fair* (compositor bypassed, tear-free; the headline floor) and *true* (tearing allowed). The browser floor is a single inline page, with variants for an editable input and for canvas.
- **Hardware in use (reports 06 and 07):**
  - **Test machine:** the owner's ThinkPad X1 Carbon Gen 13 under Linux is the Phase 0 reference.
  - **Orchestrator:** a Raspberry Pi 5.
  - **Validation camera:** a global-shutter machine-vision camera (Sony IMX287 sensor), cropped to a narrow strip for about 2,000–3,000 fps. Its trigger and exposure-active lines connect to the Teensy through an isolator, so each frame is timestamped on the rig clock.
  - **Second photodiode:** placed on the first list row, it checks marker honesty on **every trial**, not only in camera spot checks.
- **Agent-proofing:** CODEOWNERS plus a CI path guard. Agents may only write `rungs/r2..r6`. They reach the rig only through a `ladder eval` queue, never by running the orchestrator from their own checkout. Firmware is flashed from tagged releases, and the orchestrator checks it against an allow-list.

### Proposed repo layout

```
docs/          spec, protocol spec, rig build guide, methodology   (humans)
protocol/      wire schema + test vectors      (harness; firmware + orchestrator co-own)
firmware/      rig MCU firmware                (harness)
hardware/      schematics, BOM, mount CAD      (harness)
orchestrator/  `ladder` package, mock rig, stats, reports (harness)
calibration/   display-floor/ (Rust), browser-floor/      (harness)
dataset/       seeded generator; held-out seeds live only on orchestrator host (harness)
parity/        reference ranker (TS + Rust), conformance runner, scripts (harness)
rungs/         r1-typical (frozen, humans) · r2..r6 (agent-writable) · shared-marker (harness)
```

## Corrections the spec needs

The scoping turned up places where the spec is wrong or will cause trouble as written. Each needs an owner's sign-off.

| # | Spec says | Finding | Proposed change | Source |
| --- | --- | --- | --- | --- |
| S1 | Sample light at "20 kHz or faster" | The MacBook Pro mini-LED backlight flickers at **14.88 kHz at every brightness**. At 20 kHz this aliases and cannot be filtered out. | ≥100 kS/s, with a flicker estimate during calibration and filtering on the device. | 01, 02 |
| S2 | "Full brightness, so backlight flicker does not trigger the sensor" | Not true on the MacBook (14.88 kHz), nor on the X1 OLED, which flickers at **240 Hz at every brightness** (~33% dip at 100%, true PWM below 50%). One 240 Hz period (4.17 ms) is longer than the pixel transition, so the MacBook-style fix (averaging over one flicker period) would add ~2 ms. | Per-display detector settings. On OLED: black→white edges are clean; set the white→black threshold below the flicker dip; run at 100%. Keep a camera cross-check. | 01, 06 |
| S3 | Marker "in a fixed corner" | Displays scan top to bottom, so a lower marker lights up to a frame later. Browser chrome, the notch and OS scaling also move a "corner". | A fixed **absolute screen rectangle near the top-left** in device pixels, per machine, in a harness-owned `marker.json`, identical for every rung and floor. Set a **minimum physical size**: 64 px is only 6.7 mm on the X1, so about 128 px is safer. | 01, 04, 06 |
| S4 | 20-character query "at 100 ms per key" | 100 ms is exactly 6, 12 or 24 frames at 60, 120 or 240 Hz. Every key would land at the same refresh phase, the aliasing the protocol exists to prevent. | Jitter the cadence, e.g. U[90, 117] ms. | 03 |
| S5 | Two days agree "within 2 ms at p50" | Fixed-refresh latency clusters in one-frame steps, so p50 can jump a whole frame for no real reason. | Flag multimodal cases; consider a trimmed mean, and a tighter bound at 240 Hz. | 03, 04 |
| S6 | "1,000 Hz polling" | A stock Teensy 4 runs high-speed USB at **8 kHz**. | Patch the descriptor to 1 kHz for the headline; log the poll rate actually observed every session; optionally publish 8 kHz as a variant. | 01, 02 |
| S7 | Rig parts "well under $100" | About $91 per rig bought new; $55–65 with parts a lab already has. | Reword, or accept "under $100". | 01 |
| S8 | 1,000 fps validation camera | Samsung's 960 fps mode interpolates frames, so it can't be used. The RX100 is rolling-shutter and can only be synced by eye. | A **global-shutter machine-vision module** with hardware trigger (Daheng MER2-041-528U3M ~$340–380 all-in, or Basler acA720-520um ~$800), plus a second photodiode for per-trial marker honesty. | 07 |
| S9 | Every rung returns "identical ranked results using cmdk's scoring" | cmdk's order **depends on typing history**: ties keep the previous query's order, and ties are the norm (39,965 of the matches for `a` share one score). Scores also differ from Rust `powf` in the last bit (596 mismatches in 500k pairs). | Normative rule: score descending, then dataset id. Use a Chrome-generated `pow(0.999,k)` table. Strict ordering for R2–R6; R1 matches up to order within ties. | 05 |
| S10 | R1 = stock shadcn, which must pass the keyboard parity check | Stock cmdk has **no Page Up/Page Down or Escape handling**, and Home/End move the selection rather than the text cursor. | Define the keyboard checks as "behaves like R1", or amend R1 by an explicit rule. | 05 |
| S11 | 50k items is the default dataset | Scoring alone costs 55–285 ms per keystroke at 50k, which is slower than the 100 ms cadence, so input queues in R1. | Decide 50k vs 10k for the headline (already an open question in the spec). | 05 |
| S12 | The marker is drawn "in the same frame" as the list | The common controlled-input pattern in React makes the marker flip **one render early**. | R1 uses an uncontrolled input; the marker reads cmdk's own search state. | 05 |

## Plan and critical path

```
Week 1   Decisions D-A below · order parts (and camera) · repo scaffold + CODEOWNERS/path guard
         protocol schema v1 (firmware+orch) · mock rig · dataset generator · reference ranker
Week 2   Parts arrive → sensor head + board assembly · firmware bring-up (HID, ISR stamps, ADC DMA)
         orchestrator run loop against mock · floor apps (Win first) · browser floor · goldens
Week 3   Bench bring-up: gain/flicker per display, LED loopback, measured poll rate per OS
         firmware ↔ orchestrator integration on real rig · R1 skeleton + Playwright checks
Week 4   Calibration day 1 (X1 internal 60/120 + external 240 on Linux; others after Gate 1) → day 2 with re-mount → compare
         → Phase 0 exit: calibration report + noise band
```

**Critical path:** parts lead time (about 1 week) → board bring-up → firmware ISR timestamps and ADC → integration with the orchestrator → calibration on day 1 and day 2. Everything else runs beside it. The software critical path (dataset → queries → golden files → conformance runner, about 20 agent-hours) finishes well before the hardware does.

**Combined effort:** the raw sum is about 72 pd plus about 60–70 agent-hours. Report 02 (F10), report 03 (tasks 14–15) and report 04 (tasks 11–13) each count integration and the calibration runs, which is about 8–10 pd of double counting. The calibration analysis in 04 (task 11) should reuse the orchestrator's stats and reporting (03, tasks 10 and 12). With those removed, Phase 0 is **about 55–60 pd of human-owned harness work**, and much of the code can be drafted by agents under review. With one rig owner plus agent-drafted software, **four weeks is tight but plausible**. The spec's "learn within four weeks" also includes building R1 and R5, which pushes that toward five or six weeks unless the rig owner has help.

## Cross-workstream points to settle

- **Board:** 01 prefers the Teensy 4.0 plus isolated UART for replication. 02 prefers the Teensy 4.1 or an RP2350. *Proposal:* Teensy 4.1 in the lab and 4.0 in the replication BOM, same firmware. RP2350 only as a fallback.
- **Input timestamp:** 02 and 04 both recommend the moment the USB IN transfer completes (the host has collected the report). The protocol should define this, and the calibration and orchestrator code should use it.
- **Calibration analysis:** owned by the orchestrator (`ladder report calibration`), not a separate script in 04.
- **Protocol ownership:** `protocol/` is co-owned by the firmware and orchestrator owners, with shared test vectors in both CIs.
- **Marker contract:** one harness-owned spec (`rungs/shared-marker/` + `marker.json`) used by the floors, R1, and the sensor mount procedure.

## Test machine: the owner's X1 Carbon (report 06)

- **Panel:** Samsung ATNA40YK20-0 2.8K OLED. Fixed 60 and 120 Hz, **no VRR**; ships at 60 Hz. Lenovo also sells a 120 Hz VRR OLED for this model, so confirm the EDID. The 60 Hz mode draws the screen in about 8.2 ms and then blanks, so it doesn't behave like 60 Hz on a desktop monitor. Black↔white response is about 1.9 ms.
- **Linux:** the `xe` driver; use kernel ≥ 6.15 and Mesa 25.x. Turn panel self-refresh off with `xe.enable_psr=0 xe.enable_panel_replay=0`. Pin **KWin** (tearing control and direct scanout; GNOME's Mutter has no merged tearing support). Use **whole-number scaling only**, because fractional scaling silently turns direct scanout off.
- **Known bug:** the CPU can stick at 400 MHz. Use the `performance` profile and cold-boot before every session.
- **240 Hz:** external monitor over Thunderbolt 4 with a direct USB-C to DisplayPort cable, at 1440p240 or 1080p240. At 4K240, xe cannot do async flips.
- **USB:** there is an internal USB 2 hub. Use `lsusb -t` to find a port directly on a root hub for the rig.
- **Coverage:** the X1 covers the Linux and compositor-bypass (R6) role and is enough for Phase 0 and Gate 1. It ships with Windows, so dual-booting it would give Windows vs Linux on identical hardware. The Mac and budget laptop join after Gate 1.

## Decisions needed (merged from all reports)

### D-A: needed this week (block ordering parts or writing core code)

1. **Board and rig count:** Teensy 4.1 lab / 4.0 replication (recommended) vs RP2350. One rig, or two (+~$35) so Mac and PC sessions can run in parallel?
2. **Camera:** the budget machine-vision pick (Daheng, ~$340–380 total) or the better pick (Basler, ~$800 total); see report 07. Confirm frame rates at a 720×64 crop in the vendor's calculator before ordering. Add the second photodiode (~$10) either way.
3. **Tools on hand:** is a 3D printer or an oscilloscope available? Without a scope, bring-up takes about +0.5 day.
4. **Headline input timestamp:** when the host collected the report (recommended) vs when the rig queued it.
5. **Headline poll rate:** 1 kHz (spec) with an optional 8 kHz variant on R1 and R5.
6. **Headline photon edge:** first change (spec, recommended) with the 50% crossing as a robustness check; report black→white and white→black separately.
7. **Marker placement (S3):** fixed near-top-left device-pixel rectangle vs literal corner.
8. **Test machine and orchestrator:** the X1 (Linux) as the Phase 0 reference and a Pi 5 as the orchestrator (recommended). Whether to dual-boot the X1 with Windows, and whether to keep the desktop in the plan.
9. **Repo shape:** monorepo with CODEOWNERS and a path guard (proposed) vs firmware and held-out data in a separate repo agents cannot access.

### D-B: needed before calibration (week 3)

10. Windowed (maximized) vs fullscreen for the browser floor and R1–R5. This decides where compositor cost appears on the ladder.
11. Headline floor: *fair* (recommended), with *true* and *composed* as reference lines.
12. Chrome pinning: stable with updates frozen by policy, or Chrome for Testing at the same version.
13. Wayland compositor to pin. KWin / Plasma 6 is recommended; report 06 shows GNOME cannot give the true (tear-allowed) floor.
14. Mac modes: ProMotion plus fixed 60 Hz (there is no fixed 120 Hz mode).
15. Laptop panel self-refresh: disabled (the default on the X1, via `xe.enable_psr=0 xe.enable_panel_replay=0`), as shipped, or both.
16. Two-day agreement statistic and thresholds (S5); whether p95 agreement is also required.
17. Key cadence jitter (S4) and key hold time (fixed or randomized).
18. SUT control over SSH between blocks, or manual launches for headline sessions.
19. How long a calibration stays valid; whether re-mounting the sensor forces recalibration.
20. Results storage (object store plus git index recommended); public from day one or not.
21. USB identity: generic VID/PID/strings (recommended; published in the docs) vs honestly naming the rig.

### D-C: needed before Phase 1 rung work

22. Tie semantics (S9): strict for R2–R6, tie-insensitive for R1 (recommended).
23. Keyboard and editing parity relative to R1, or amend R1 (S10).
24. Headline dataset size: 50k or 10k (S11).
25. Next.js only, or Next plus a Vite variant measured at Gate 1 (recommended).
26. R1 shape: inline `Command` or `CommandDialog`; data loading by client fetch (recommended), bundled import or server rendering.
27. Dataset details: item length counted in code points (recommended); word-list source and licence; excluding İ, capital Σ, NFD text and bidi overrides.
28. Visual-diff metric and threshold; text-shaping sign-off procedure.
29. Whether R2 may build its index at build time; no keywords or groups in v1.
30. Which Scenario A number is the headline: the first keystroke into an empty palette (recommended), all keys pooled, or only the keys in the sequence.
31. Trials for p99: 500, or 1,000 for headline blocks.
32. Pointer acceleration off on every machine for Scenario B: who owns the per-OS setup.

## Top risks

| Risk | Mitigation |
| --- | --- |
| Compositor bypass (iFlip / direct scanout) silently not engaging, so the "fair" floor quietly equals "composed" | PresentMon, Instruments or `wp_presentation` in every attribution session; automated ordering check (composed ≥ fair ≥ true) |
| Flicker or slow LCD transitions giving false or threshold-dependent edges | ≥100 kS/s, per-display flicker characterization, both edge metrics, report each direction, camera cross-check |
| Ground loop between the two PCs through the rig | Galvanic isolation on the orchestrator UART |
| Teensy running at 8 kHz without anyone noticing | Observed poll rate logged every session; orchestrator asserts it |
| Rungs fingerprinting the rig | Only a keyboard and mouse exposed; generic descriptors; held-out seeds only on the orchestrator host |
| SSH or network activity disturbing the machine under test | Disconnect before measuring, then a quiet period; A/A calibration test with the SSH server on and off |
| Floor apps turning into an "R6 lite" that agents copy | They live in harness paths that agents cannot edit |
| X1: the CPU sticks at 400 MHz, or firmware/driver updates arrive through the owner's daily OS between calibration days | `performance` profile, cold boot per session, pinned kernel/Mesa/Chrome, versions recorded in each session manifest and diffed |
| OLED 240 Hz flicker, or burn-in protection shifting or dimming the static marker | Per-display detector settings; check the marker position and level at the start of each session |

## Suggested next steps

1. Settle D-A (1–9) and place the parts order. Part lead time is the longest item on the critical path.
2. Scaffold the monorepo, CODEOWNERS and path guard, and draft `protocol/` v1 jointly for firmware and orchestrator.
3. In parallel, with no hardware: mock rig, orchestrator plan and run loop, dataset generator, reference ranker (TS plus Rust, grown from `spikes/`), `floor-win`, browser floor.
4. Update the spec with S1–S12 once they are signed off.
