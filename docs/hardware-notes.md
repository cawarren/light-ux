# Hardware notes: decisions and findings to carry into Phases B and C

Written 2026-09-30, when rig work was deferred behind the software-only Phase A. This page lists what the hardware scoping established, so Phases B and C start from it rather than repeating the research. The detail and sources are in [`phase-0/`](phase-0/README.md), reports 01–04, 06 and 07. Items marked **[verify]** in those reports still need a bench or on-device check.

## Settled findings (treat as requirements)

| # | Finding | Consequence | Source |
| --- | --- | --- | --- |
| H1 | The MacBook Pro mini-LED backlight flickers at **14.88 kHz at every brightness** | Sample light at **≥100 kS/s**, not the spec's 20 kHz; 20 kHz aliases it and filtering can't remove that. "Full brightness" does not avoid flicker. | 01, 02 |
| H2 | The X1 Carbon OLED flickers at **240 Hz at every brightness** (~33% dip at 100%, true PWM below 50%) | Averaging over one flicker period would add ~2 ms, so it can't be used here. Black→white edges are clean; set the white→black threshold below the dip; run at 100%. Detector settings are **per display**. | 06 |
| H3 | A stock **Teensy 4 polls USB at 8 kHz** (high speed, bInterval=1) | Patch the descriptor for the spec's 1 kHz; log the **observed** poll rate every session and have the orchestrator assert it. RP2040/RP2350 run at 1 kHz natively. | 01, 02 |
| H4 | The MCU's USB port is used by the keyboard/mouse connection to the machine under test | The orchestrator needs a **separate isolated UART** (~3 Mbaud): Teensy UART → ADuM1201BRZ (10 Mbps grade) → CP2102N (not CP2104, which tops out at 2 Mbaud). Isolation also prevents a ground loop between the two PCs that would add noise to the photodiode. | 01, 02, 03 |
| H5 | Displays scan top to bottom | A marker lower on screen lights up to a frame later. The marker must sit at the **same absolute screen position**, near the top-left, in every rung and floor app. Set a minimum physical size: 64 px is 6.7 mm on the X1, so use ~128 px. | 01, 04, 06 |
| H6 | "Input time" is ambiguous | Stamp input when the **USB IN transfer completes** (the host has the report), inside the interrupt; also log queue time and start-of-frame. One 64-bit µs clock for everything. | 02, 04 |
| H7 | LCD black→white and white→black transitions take different times | Report each direction separately; log both first departure and the 50% crossing; fix the thresholds per machine per session. | 01, 02 |
| H8 | The camera must be global-shutter with a hardware trigger | Samsung 960 fps interpolates frames; the RX100 is rolling-shutter and can only be synced by eye. Use a machine-vision module with trigger and exposure-active lines wired to the Teensy through its own isolator. | 07 |
| H9 | A second photodiode on the first list row checks marker honesty on every trial | An honest rung lights the row about 0.65 ms after the marker at 120 Hz (scanout offset); a cheating one at least one refresh later. ~$10. | 07 |
| H10 | Trial scripts run on the device | The orchestrator draws the delays from a recorded seed and uploads a timeline; the firmware runs it and echoes what it actually did. No round trips during trials. | 02, 03 |
| H11 | Fixed 100 ms key cadence locks to refresh (6/12/24 frames at 60/120/240 Hz) | Jitter the cadence, e.g. U[90, 117] ms. Applies in Phase A too. | 03 |
| H12 | Frame-quantized latency is multimodal | "Within 2 ms at p50" across days can fail by a whole frame for no real reason. Flag mode boundaries; consider a trimmed mean. | 03, 04 |

## Recommended parts and setup

- **Board:** Teensy 4.1 (lab) / 4.0 (replication BOM); RP2350 as fallback. Buy two.
- **Sensor head:** BPW34 photodiode with an MCP6002 transimpedance amplifier, gain switchable 22k/47k/100k. Anti-alias filter before the ADC. 3D-printed head, 6 mm aperture, black foam gasket. Suction cups on glossy glass; a strap or clamp arm on matte panels. Not the OPT101 (saturates) and not phototransistors or LDRs (too slow).
- **Calibration LEDs:** one driven by the Teensy to measure rig latency; one visible to the camera when each keypress reaches the machine.
- **BOM:** ~$91 per rig new, $55–65 with lab parts. Lab extras ~$56. See [01 §5](phase-0/01-hardware.md).
- **Camera:** budget pick Daheng MER2-041-528U3M (mono, *not* the "-L" variant, which has no I/O), ~$340–380 all-in. Better pick Basler acA720-520um, ~$800 all-in, or The Imaging Source DMK 37BUX287. Both use the IMX287 sensor, estimated ~2,900 fps at 720×64 (confirm in the vendor calculator). Record bursts to RAM or NVMe, not SD; the Pi 5 needs its 27 W supply to power a Basler. See [07](phase-0/07-camera.md).
- **Orchestrator host:** Raspberry Pi 5.
- **Firmware:** C/C++ (Teensyduino via PlatformIO, or pico-sdk + TinyUSB); COBS + CRC-16 protocol generated from one schema; only keyboard and mouse exposed to the machine under test; generic USB identity. See [02](phase-0/02-firmware.md).
- **Floor apps:** one Rust workspace calling platform graphics APIs directly (DXGI flip model, KMS/Wayland, Metal). Three floors: composed, fair (headline), true (tear-allowed). See [04](phase-0/04-calibration.md).

## Test machine: ThinkPad X1 Carbon Gen 13 (Linux)

- Panel Samsung ATNA40YK20-0: fixed 60/120 Hz, no VRR (a VRR panel option exists, so check the EDID). The 60 Hz mode draws in ~8.2 ms, then blanks. Response ~1.9 ms.
- Kernel ≥ 6.15, Mesa 25.x, `xe` driver. Panel self-refresh off: `xe.enable_psr=0 xe.enable_panel_replay=0`.
- KWin (not GNOME's Mutter, which lacks merged tearing support), whole-number scaling only (fractional scaling silently turns direct scanout off).
- `performance` profile and a cold boot before each session (open bug: CPU sticks at 400 MHz).
- 240 Hz only on an external monitor over Thunderbolt 4 with a direct USB-C to DisplayPort cable, at 1440p or 1080p (not 4K240, where async flips are rejected).
- An internal USB 2 hub exists; plug the rig into a root-hub port (`lsusb -t`).
- Pin kernel, Mesa and Chrome, and record versions per session; the laptop is also a daily machine, so updates can land between sessions.

See [06](phase-0/06-dut-x1-carbon.md).

## Phase B: minimal subset

Phase B needs only enough rig to compare browser and native rungs in photons:

- a Teensy, one BPW34 head, the calibration LED;
- keyboard HID at 1 kHz with ISR timestamps (H3, H6);
- sampling ≥100 kS/s with simple thresholded edge detection, per display (H1, H2);
- the marker placement contract (H5);
- a serial link to the orchestrator; isolation (H4) is recommended even here, since it is cheap;
- the Phase A harness's data model and statistics as the orchestrator.

Deferred to Phase C: the camera, second photodiode, calibration suite, floor apps, two-day reproducibility, other machines, the mock rig and the full protocol.

## Open hardware decisions (from the Phase 0 README)

Board and rig count; camera pick; 3D printer and oscilloscope availability; headline input timestamp (H6 recommends IN completion); 1 kHz vs 8 kHz; first-change threshold; USB identity; whether the firmware lives in a separate repo; Chrome window mode; headline floor; Mac modes; calibration validity window. See the [Phase 0 README decisions](phase-0/README.md#decisions-needed-merged-from-all-reports).
