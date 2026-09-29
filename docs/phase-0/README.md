# Latency Ladder: Phase 0 scope

Scoped 2026-09-29 against [`docs/spec.md`](../spec.md). This page combines five scoping reports:

| # | Workstream | Detailed report | Estimate |
| --- | --- | --- | --- |
| 01 | Rig hardware and BOM | [01-hardware.md](01-hardware.md) | ~6–6.5 person-days (pd), ~2 calendar weeks with shipping |
| 02 | Rig firmware | [02-firmware.md](02-firmware.md) | ~15.5 pd (12–18) |
| 03 | Orchestrator and results pipeline | [03-orchestrator.md](03-orchestrator.md) | ~28 pd (~12–15 with agents drafting) |
| 04 | Calibration targets and procedure | [04-calibration.md](04-calibration.md) | ~22 pd (~14 dev, 5–8 rig-owner days) |
| 05 | Software foundations for Phase 1 (dataset, reference ranking, R1, cheap parity checks) | [05-software-foundations.md](05-software-foundations.md) | ~56–73 agent-hours + ~2 human-days |

Report 04 was written without web research, so its driver and API claims are tagged **[verify]**. Report 05 is backed by runnable spikes in [`spikes/`](spikes/).

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
| S2 | "Full brightness, so backlight flicker does not trigger the sensor" | Not true on the MacBook. | Rely on filtering plus a camera cross-check; keep full brightness for signal level. | 01 |
| S3 | Marker "in a fixed corner" | Displays scan top to bottom, so a lower marker lights up to a frame later. Browser chrome, the notch and OS scaling also move a "corner". | A fixed **absolute screen rectangle near the top-left** in device pixels, per machine, in a harness-owned `marker.json`, identical for every rung and floor. | 01, 04 |
| S4 | 20-character query "at 100 ms per key" | 100 ms is exactly 6, 12 or 24 frames at 60, 120 or 240 Hz. Every key would land at the same refresh phase, the aliasing the protocol exists to prevent. | Jitter the cadence, e.g. U[90, 117] ms. | 03 |
| S5 | Two days agree "within 2 ms at p50" | Fixed-refresh latency clusters in one-frame steps, so p50 can jump a whole frame for no real reason. | Flag multimodal cases; consider a trimmed mean, and a tighter bound at 240 Hz. | 03, 04 |
| S6 | "1,000 Hz polling" | A stock Teensy 4 runs high-speed USB at **8 kHz**. | Patch the descriptor to 1 kHz for the headline; log the poll rate actually observed every session; optionally publish 8 kHz as a variant. | 01, 02 |
| S7 | Rig parts "well under $100" | About $91 per rig bought new; $55–65 with parts a lab already has. | Reword, or accept "under $100". | 01 |
| S8 | 1,000 fps validation camera | Samsung's 960 fps mode interpolates frames, so it can't be used. | Use a used Sony RX100 V (~$350) or rent a Chronos. | 01 |
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
Week 4   Calibration day 1 (Win 60/240, Linux, Mac, laptop) → day 2 with re-mount → compare
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

## Decisions needed (merged from all five reports)

### D-A: needed this week (block ordering parts or writing core code)

1. **Board and rig count:** Teensy 4.1 lab / 4.0 replication (recommended) vs RP2350. One rig, or two (+~$35) so Mac and PC sessions can run in parallel?
2. **Camera budget:** buy a used RX100 V (~$350), rent a Chronos for tearing and marker-honesty audits, or both.
3. **Tools on hand:** is a 3D printer or an oscilloscope available? Without a scope, bring-up takes about +0.5 day.
4. **Headline input timestamp:** when the host collected the report (recommended) vs when the rig queued it.
5. **Headline poll rate:** 1 kHz (spec) with an optional 8 kHz variant on R1 and R5.
6. **Headline photon edge:** first change (spec, recommended) with the 50% crossing as a robustness check; report black→white and white→black separately.
7. **Marker placement (S3):** fixed near-top-left device-pixel rectangle vs literal corner.
8. **Repo shape:** monorepo with CODEOWNERS and a path guard (proposed) vs firmware and held-out data in a separate repo agents cannot access.

### D-B: needed before calibration (week 3)

9. Windowed (maximized) vs fullscreen for the browser floor and R1–R5. This decides where compositor cost appears on the ladder.
10. Headline floor: *fair* (recommended), with *true* and *composed* as reference lines.
11. Chrome pinning: stable with updates frozen by policy, or Chrome for Testing at the same version.
12. Wayland compositor to pin (KWin / Plasma 6 recommended).
13. Mac modes: ProMotion plus fixed 60 Hz (there is no fixed 120 Hz mode).
14. Laptop panel self-refresh: disabled, as shipped, or both.
15. Two-day agreement statistic and thresholds (S5); whether p95 agreement is also required.
16. Key cadence jitter (S4) and key hold time (fixed or randomized).
17. SUT control over SSH between blocks, or manual launches for headline sessions.
18. How long a calibration stays valid; whether re-mounting the sensor forces recalibration.
19. Results storage (object store plus git index recommended); public from day one or not.
20. USB identity: generic VID/PID/strings (recommended; published in the docs) vs honestly naming the rig.

### D-C: needed before Phase 1 rung work

21. Tie semantics (S9): strict for R2–R6, tie-insensitive for R1 (recommended).
22. Keyboard and editing parity relative to R1, or amend R1 (S10).
23. Headline dataset size: 50k or 10k (S11).
24. Next.js only, or Next plus a Vite variant measured at Gate 1 (recommended).
25. R1 shape: inline `Command` or `CommandDialog`; data loading by client fetch (recommended), bundled import or server rendering.
26. Dataset details: item length counted in code points (recommended); word-list source and licence; excluding İ, capital Σ, NFD text and bidi overrides.
27. Visual-diff metric and threshold; text-shaping sign-off procedure.
28. Whether R2 may build its index at build time; no keywords or groups in v1.
29. Which Scenario A number is the headline: the first keystroke into an empty palette (recommended), all keys pooled, or only the keys in the sequence.
30. Trials for p99: 500, or 1,000 for headline blocks.
31. Pointer acceleration off on every machine for Scenario B: who owns the per-OS setup.

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

## Suggested next steps

1. Settle D-A (1–8) and place the parts order. Part lead time is the longest item on the critical path.
2. Scaffold the monorepo, CODEOWNERS and path guard, and draft `protocol/` v1 jointly for firmware and orchestrator.
3. In parallel, with no hardware: mock rig, orchestrator plan and run loop, dataset generator, reference ranker (TS plus Rust, grown from `spikes/`), `floor-win`, browser floor.
4. Update the spec with S1–S12 once they are signed off.
