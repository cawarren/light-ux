# Phase 0 scope: rig firmware

Workstream: **"Write rig firmware: USB keyboard and mouse output, 20 kHz light sampling, timestamped log."**
Assumed MCU: Teensy 4.0/4.1 (i.MX RT1062) or RP2040/RP2350. Hardware part choice and the orchestrator are covered by other scope docs. This one covers only what runs on the MCU, plus the host-side protocol library the orchestrator links against.

---

## 0. TL;DR recommendations

1. **The MUT's USB port carries HID only (keyboard + mouse, no CDC/serial).** The orchestrator link goes over a physically separate path: a UART to a USB-UART bridge plugged into the orchestrator, ideally through a digital isolator. Nothing about control or logging is ever enumerated on the machine under test (MUT).
2. **Measure time from when the host actually collects the report, not from when firmware queues it.** Timestamp the endpoint transfer-complete interrupt (the host has ACKed the IN packet) and log the queue time next to it. Also log every SOF, so each trial's phase against the host's 1 ms frame clock is known.
3. **Use one clock.** All events use a single free-running 64-bit µs counter on the MCU (sub-µs on Teensy). Latency is always a difference between two timestamps from the same crystal, so drift (±50 ppm × 100 ms = 5 µs) doesn't matter. Clock mapping to the orchestrator is only needed for aligning with Perfetto/PresentMon, and is done with ping exchanges.
4. **Sample faster than the spec floor.** Use ≥100 kS/s with DMA, then filter/decimate on-device. 20 kHz is not enough to reject the 14.9 kHz mini-LED PWM on the MacBook Pro in the test matrix (it aliases).
5. **Two sensor modes.** *Event mode* does on-device edge detection (auto-calibrated black/white levels, hysteresis, a confirm window, PWM-aware filter) and runs every trial. *Raw mode* does ring-buffered, trigger-windowed capture that is uploaded after the fact, for validation, debugging and threshold tuning. Every event record carries enough data (levels, threshold, sample indices) for the detection to be recomputed offline from a raw capture.
6. **Protocol:** COBS-framed binary with CRC-16 and a version handshake. The schema is defined once and code-generated or shared for firmware (C/C++) and orchestrator (Python). Trials are scripted as *timelines* (a list of timed HID actions plus sensor arm/disarm), so Scenarios B and C are data, not firmware changes.
7. **Platform:** a **Teensy 4.1 with Teensyduino plus a small patched USB core, built with PlatformIO**. The alternative is an **RP2040/RP2350 + TinyUSB on pico-sdk**, with a second cheap Pico or a USB-UART chip as the control bridge. Both are workable. The biggest differences are in §1.4. Stay on C/C++ for Phase 0; Rust/embassy adds risk without buying accuracy here.
8. **Agent-proofing:** the firmware lives in a human-owned `rig/` tree with CODEOWNERS and branch protection. Signed, versioned release binaries are identified at runtime by build hash. Every trial CSV row carries `fw_version` + `fw_git_sha` + `protocol_version`, and the orchestrator refuses unknown hashes.

Estimated effort: **~12–16 person-days** for a Phase 0 firmware that meets the checklist and is built to extend (§8).

---

## 1. Architecture

### 1.1 Block diagram

```
           ┌──────────────────────── Rig MCU ─────────────────────────┐
 MUT  ◄────┤ USB device port: HID kbd (boot, 8B) + HID mouse (+wheel) │
 (USB)     │   bInterval → 1 ms (see 1.3), SOF + EP-complete IRQs     │
           │                                                           │
           │  µs clock (64-bit) ── timestamps everything              │
           │                                                           │
 Photodiode│  ADC ◄─ timer trigger ─ DMA ring buffer (≥100 kS/s)      │
 + TIA ───►│   └─► filter/decimate ─► edge detector ─► event queue    │
           │                                                           │
 Cal LED ◄─┤  GPIO (LED loopback, also optional scope-trigger pin)    │
           │                                                           │
           │  UART (2–3 Mbaud) ─► [isolator] ─► USB-UART ─► Orchestrator
           └───────────────────────────────────────────────────────────┘
```

### 1.2 Invariants

- **I1.** The MUT only ever sees a standard HID keyboard + mouse. No CDC, no vendor/raw HID, no serial emulation, so nothing on the MUT can talk back to the rig or learn trial timing (and rung code can't detect the rig, per the spec's anti-gaming rule). Use a VID/PID and product string that look like an ordinary keyboard. (The pid.codes open VID is fine. The product string must not say "latency rig", because rungs could detect it; see open decision D6.)
- **I2.** The orchestrator never shares a USB bus with the MUT. The UART is point-to-point.
- **I3.** Firmware owns all timing during a trial. The orchestrator uploads a whole timeline, sends `RUN`, and gets events back asynchronously. No round trips happen during a trial, so orchestrator/OS jitter never enters the measured loop.
- **I4.** The rig does nothing to the MUT between trials: no report floods, and keys are released properly (explicit all-up report at the end of every timeline and on any error or abort).

### 1.3 USB HID details

- **Composite device:** Interface 0 is a boot keyboard (8-byte report, 6KRO is enough; NKRO isn't needed). Interface 1 is a mouse (buttons, 16-bit relative X/Y, wheel, AC Pan for horizontal). Separate interfaces and endpoints stop mouse traffic from delaying keyboard reports during Scenario B/C timelines. Joystick and media interfaces are omitted.
- **Polling at 1 ms.** The spec says 1,000 Hz. How you get it differs by platform:
  - **RP2040/RP2350**: the native USB is full-speed only (12 Mbit), so `bInterval=1` = 1 ms frames. That is exactly the spec's figure and matches ordinary keyboards.
  - **Teensy 4.x** enumerates at **high speed (480 Mbit)**. For HS interrupt endpoints, bInterval is an exponent: the period is 2^(bInterval−1) × 125 µs microframes (USB 2.0 §9.6.6, Table 9-13). Stock Teensyduino uses `KEYBOARD_INTERVAL 1` in all HID configs (`teensy4/usb_desc.h`, with a `// TODO: is this ok for 480 Mbit speed` comment). **So a stock Teensy 4 keyboard polls at 8 kHz, not 1 kHz.** To be *spec-faithful* you either (a) force full speed (the commented-out `USB1_PORTSC1 |= USB_PORTSC1_PFSC` in `teensy4/usb.c` `usb_init`), or (b) stay at HS with `bInterval=4` (8 µframes = 1 ms). Option (a) best mimics a real keyboard's electrical path through the host controller and hub. **Recommend making the polling rate a build-time/boot-time parameter**: 1 kHz for headline numbers, 8 kHz as a variant that shrinks input quantisation from ±0.5 ms to ±62 µs. That's useful for the 240 Hz runs, where 1 ms is ~25% of a frame. Sources: [PJRC USB keyboard docs](https://www.pjrc.com/teensy/td_keyboard.html), [teensy4 high-rate mouse example](https://github.com/Trip93/teensy4_mouse), PaulStoffregen/cores@7f107ee.
  - **Verify on the host**: Windows USBView / `usbview`, Linux `lsusb -v` + `usbmon` timestamps, macOS IORegistry. Check that the effective interval is 1 ms. On Linux, `usbhid.kbpoll/mousepoll` module parameters can override bInterval, so the Linux MUT profile must pin them to defaults.
- **Reset/re-enumeration handling:** if the MUT suspends, resets or re-enumerates, the firmware reports a `USB_STATE` event, and any running trial is aborted and marked invalid.

### 1.4 Platform comparison (firmware-relevant only)

| Concern | Teensy 4.x (Teensyduino + patched core) | RP2040/RP2350 (pico-sdk + TinyUSB) |
|---|---|---|
| USB speed / 1 kHz | HS by default (8 kHz with stock descriptors). Needs a core patch or PFSC force for 1 kHz (§1.3). | FS only, so 1 kHz is the natural rate. |
| "Report actually sent" timestamp | Core supports per-endpoint TX completion callbacks (`usb_config_tx(ep,…,cb)` sets IOC in `schedule_transfer`), but keyboard/mouse pass `NULL`. Needs a ~30-line core fork to register a callback and timestamp inside the USB ISR. | `tud_hid_report_complete_cb` exists but runs in `tud_task()` context (deferred), so its timestamp is jittered by the main loop. Timestamp in the USB IRQ instead: wrap `dcd_int_handler` / read `BUFF_STATUS` + `TIMERAWL` first thing in the ISR. |
| SOF timestamp | Core has `usb_start_sof_interrupts()` (in `usb.c`), hookable. | `tud_sof_cb_enable(true)` + `tud_sof_cb()` (again deferred, so hook the ISR for the time; SOF frame number from `SOF_RD`). hathach/tinyusb@61d9f45 |
| Second link to orchestrator | USB host port on T4.1 is host-only. Use **LPUART (up to ~6 Mbaud)** into an FTDI/CP2102N, or **native Ethernet on T4.1** (QNEthernet). | **Pico-PIO-USB** can add a second USB port, but its device mode is less mature and it costs a core and a 120/240 MHz sysclk ([Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB), [TinyUSB #1673](https://github.com/hathach/tinyusb/issues/1673)). **Recommend UART to a second $4 Pico running a CDC bridge (or a CP2102N).** |
| ADC | 2× 12-bit ADC, ~1 MS/s, PIT/QTimer → XBAR → ADC_ETC → DMA chain ([pedvide/ADC](https://github.com/pedvide/ADC) `adc_timer_dma` example). Chain has known quirks ([NXP thread on PIT→ADC_ETC ratio](https://community.nxp.com/t5/i-MX-RT-Crossover-MCUs/Teensy-4-1-DMA-Hardware-Chain-Issue-4-1-PIT-Trigger-to-ADC-ETC/m-p/2116402)). | 12-bit, 500 kS/s free-running with CLKDIV, FIFO → DMA, simple and deterministic. RP2040 ENOB ≈ 8.7 with DNL spikes at 512/1536/2560/3584 (erratum RP2040-E11). RP2350 fixes the spikes ([Instructables ADC comparison](https://www.instructables.com/Mass-ADC-Testing-DNL-INL-ENOB-Ads1115-Mcp4728-Stab/), [rp2040adc_correction](https://github.com/kitanokitsune/rp2040adc_correction)). Fine for edge timing. Prefer RP2350. |
| RAM for raw capture | 1 MB (T4.1: + optional 8–16 MB PSRAM): ~5 s at 100 kS/s × 16-bit in internal RAM. | 264 KB (RP2040) / 520 KB (RP2350): ~1–2.5 s at 100 kS/s. Enough for trigger windows, not for 10 s Scenario C traces. Decimate or stream. |
| CPU headroom | 600 MHz M7, lots. | Dual M0+/M33, enough. Put USB + timeline on core 0 and ADC/detector on core 1. |
| Replicability ("well under $100") | ~$30–40. | ~$5–8 per Pico. |
| Timer | `ARM_DWT_CYCCNT` (600 MHz, wraps at 7 s, so extend to 64-bit) or GPT1 at 1 MHz+. | 64-bit 1 MHz `timer_hw` (no wrap handling). RP2350 also runs at 150 MHz for SysTick if sub-µs is needed. |

**Recommendation:** either platform is fine. Pick **Teensy 4.1** if the hardware workstream wants the cleanest analog path, the most capture RAM, and Ethernet as a control-link option. Pick **RP2350 (Pico 2) + second Pico/CP2102N bridge** if cost and "anyone can rebuild it" matter more; full-speed-only USB is actually a plus for spec fidelity. The firmware design below is platform-neutral behind a small HAL (`hal_usb`, `hal_adc`, `hal_clock`, `hal_uart`, `hal_gpio`).

### 1.5 Ground/isolation note (coordinate with the hardware workstream)

With the MUT on the device port and the orchestrator on the UART bridge, the two PCs' grounds meet at the rig, which can create a ground loop that shows up as noise on a µA-level photodiode signal. Mitigate with a digital isolator on the UART lines (e.g. ADuM1201 / ISO7721, <$5) and power the rig from one side only. Firmware impact: none, apart from the UART being the only link. Keep the baud rate within the isolator's rating (both parts easily do 3 Mbaud+).

---

## 2. Timing model

### 2.1 One clock

- `t_us`: a 64-bit monotonic counter on the MCU. Teensy: DWT cycle counter extended to 64-bit in a SysTick/GPT overflow handler (resolution 1.67 ns, reported in ns or 0.1 µs units). RP2040/2350: the hardware 64-bit µs timer.
- **Every** logged event (SOF, report queued, report ACKed, ADC sample index, detected edge, LED on/off) is stamped from this clock. ADC samples are stamped implicitly: `t(sample i) = t0 + i × period`, with `t0` captured when the DMA ring is started. Resync every DMA block half-complete IRQ to catch any sampling clock/timer mismatch (on RP2040 the ADC clock and timer share the crystal, so this is only a sanity check).
- **Drift:** the latency is `t_edge − t_ack` on one crystal. Error ≈ ppm × interval ≈ 50e-6 × 0.1 s = 5 µs worst case, which is negligible. Report crystal ppm vs SOF (below) per session as a health metric.
- **Host frame clock as a free reference:** the USB host sends SOF every 1.000 ms (±500 ppm spec; typically much better). Logging SOF timestamps gives (a) MCU-vs-host clock ratio and (b) which host frame each report went out in. Stream SOFs only during trials (1 kHz × ~12 B = 12 kB/s), or log every Nth SOF plus those adjacent to reports.
- **Orchestrator ↔ MCU time mapping:** only needed to line up rig events with Perfetto/PresentMon traces from *attribution sessions* (not headline). Use an NTP-style `PING`: the orchestrator records host send/receive times and the MCU returns `t_us`. Take the minimum-RTT sample of ~50 to estimate offset, and refit a linear model each session. Expect ±50–200 µs with a USB-UART (FTDI latency timer set to 1 ms). An optional GPIO "sync pulse" out to a scope or camera LED gives a hard reference.

### 2.2 What "input time" means

Each report produces three timestamps:

| Field | Meaning | Source |
|---|---|---|
| `t_queue` | Firmware armed the endpoint with this report | Before `usb_transmit` / `tud_hid_report` |
| `t_ack` | Host's IN token collected it (transfer complete) | EP-complete ISR, first instruction |
| `sof_frame`, `t_sof` | Frame in which it went, and that frame's SOF time | SOF ISR |

- With 1 kHz polling, `t_ack − t_queue` should be uniform on (0, 1 ms]. Its distribution is a built-in health check, logged per session.
- **Headline latency = `t_photon − t_ack`**: the moment the host has the data. This excludes the rig's own queueing, which a real keyboard also has (its scan/debounce), and which varies across keyboards. Also export `t_photon − t_queue` so readers can add a "typical keyboard" constant if wanted. **Open decision D1.**
- **Phase randomization:** the orchestrator's 50–250 ms random inter-trial delay already decorrelates from display vsync. The firmware adds nothing but must not re-quantise it. Timelines are scheduled against `t_us`, not against SOF, unless a trial explicitly asks for `align: sof+offset` (useful later for studying host-frame effects).
- **Key-up handling:** Scenario A measures on key-down. Key-up is sent `hold_ms` later (default 30–60 ms, randomized in a range to look human; see D5) and its ack is logged too, but it isn't a measured event.

### 2.3 Jitter budget (what the rig itself contributes)

| Source | Size | Mitigation / reporting |
|---|---|---|
| Report queue → host poll | U(0, 1 ms] at 1 kHz; U(0, 125 µs] at 8 kHz | Eliminated from headline by using `t_ack`; logged. |
| `t_ack` ISR latency | < 1 µs (Teensy), 1–3 µs (RP2040) | Highest IRQ priority for USB; measured in LED loopback. |
| ADC sample period | 10 µs at 100 kS/s (±5 µs quantisation) | Sub-sample interpolation of threshold crossing (linear between two samples). |
| Detector filter group delay | Deterministic, e.g. boxcar N/2 × Ts | Subtract in firmware and log `filter_delay_us`; verified by LED loopback. |
| Photodiode + TIA rise time | Hardware-dependent, ~µs to tens of µs | Measured by LED loopback and reported as rig latency. |
| Clock drift | ≤ 5 µs per 100 ms | Negligible; logged. |
| **Rig total (target)** | **p99 < 50 µs** excluding host poll | Reported per session from the loopback self-test (§6). |

That budget is ~2.5% of the spec's 2 ms day-to-day agreement criterion, so the rig won't be the limiting factor.

---

## 3. Photon detection

### 3.1 Sampling

- **Rate:** default **100 kS/s**, configurable 20–500 kS/s (RP2040 max 500 kS/s). Rationale: the spec floor is 20 kHz, but the MacBook Pro mini-LED backlight PWMs at ~14.9 kHz at all brightness levels ([Notebookcheck via MacRumors](https://forums.macrumors.com/threads/macbook-pro-14-2021-pwm-screen-flickering.2319440/)), so "full brightness" doesn't help there. At 20 kS/s, 14.9 kHz aliases to 5.1 kHz, which you can't filter out digitally. At 100 kS/s it can be boxcar-averaged over an integer number of PWM periods. Coordinate with hardware: an analog anti-alias RC at the TIA output (~20–30 kHz corner) complements this.
- **Mechanics:** timer-triggered (Teensy: PIT/QTimer→XBAR→ADC_ETC→DMA) or free-running (RP2040: `adc_run` + CLKDIV + FIFO DREQ → DMA) into a double-buffered ring (e.g. 2 × 1024 samples). The half/full-complete IRQ hands blocks to the detector. The CPU never touches individual conversions.
- **Channels:** 1 photodiode channel is required. Reserve a 2nd channel for (a) a second photodiode (Scenario B drag: detect the card edge crossing a sensor at a known position, or two sensors to measure speed), or (b) monitoring the LED drive in loopback mode.

### 3.2 Event mode (every trial)

1. **Calibrate levels** (`CAL_LEVELS`): the orchestrator shows black, then white, on the marker (or asks the MUT's floor app to). The firmware records mean and σ over 50–200 ms each and computes `swing = white − black`. These get re-measured every N trials, or cheaply from the pre-trigger baseline each trial.
2. **Filter:** boxcar (moving sum) of length L samples. L defaults to 1 (no filter) and is set from the calibration FFT/period estimate when PWM is detected (e.g. L = round(k × fs / f_pwm)). A cascaded boxcar or single-pole IIR is an option. Group delay is known and subtracted.
3. **Arm:** at `t_ack` of the trigger report (or at a timeline-specified time), take the **baseline** from the last ~2 ms pre-trigger. Direction is inferred (baseline near black means expect rising, and vice versa, because the marker toggles).
4. **Detect:** record two crossings:
   - `t_first`: first departure, i.e. crossing `baseline ± max(k·σ, p_first·swing)` (defaults k=6, p_first=10%). This is the spec's "first photon change".
   - `t_50`: crossing 50% of the swing, a robust metric that's insensitive to panel response time.
   Each requires **M consecutive filtered samples past threshold** (default M = 3). **Hysteresis:** re-arming requires returning within `p_hyst` of the new level. Timestamps are the first sample of the confirmed run, linearly interpolated to sub-sample.
5. **Timeout:** no edge within `timeout_ms` (default 500) gives a `MISS` event. Misses are data, not dropped silently.
6. **Record:** `{trial_id, action_id, t_ack, t_first, t_50, dir, baseline, final_level, swing, thresh_first, thresh_50, L, M, fs, sample_index_first}` plus a **small snippet** (e.g. 256 samples around `t_first`, decimated) so every event can be spot-audited.

Sub-options for later phases: multiple edges per arm (Scenario A's 20-char query at 100 ms/key needs one edge per keystroke, so each key action arms its own window), and "count frames" mode for Scenario C (see §5).

### 3.3 Raw mode (debug/validation)

- `RAW_CAPTURE {pre_ms, post_ms, fs, trigger: action_id|immediate|level}` fills a RAM ring, freezes on trigger, and uploads in chunks with sequence numbers + CRC. Upload bandwidth: 100 kS/s × 12 bit ≈ 150 kB/s. That's too much to stream over a 1 Mbaud UART in real time, but trivially uploads post-hoc at 3 Mbaud (≈300 kB/s) in a few seconds.
- An optional **streaming** raw mode at ≤20 kS/s × 16 bit = 40 kB/s fits 1 Mbaud, which is useful for live scope-like views in the orchestrator.
- **Validation workflow:** run N trials with raw capture enabled for all of them. The orchestrator re-runs the *same detector code* (compiled for host, §7) on the raw data and must reproduce the firmware's `t_first/t_50` exactly (bit-identical given the same parameters). This is a CI-able regression test for the detector and the evidence base for the published rig design.

---

## 4. Command protocol (orchestrator ↔ rig)

### 4.1 Framing

- **COBS-encoded frames delimited by 0x00**, each: `[ver:u8][type:u8][seq:u16][len:u16][payload…][crc16-CCITT]`. Rationale: binary is compact for raw uploads and event streams, COBS resynchronizes after any byte loss, and a CRC catches corruption. Line-JSON is easier to eyeball but ~5–10× larger and awkward for sample data. A `--debug-text` build flag can emit a human-readable mirror for bring-up only.
- **Payload encoding:** fixed little-endian C structs described once in a schema file (`rig/protocol/schema.yaml` or a `.proto` used with nanopb). A generator emits `protocol.h` (firmware) and `protocol.py` (orchestrator). Alternative: CBOR via tinycbor/zcbor. Recommend **fixed structs + generator** for Phase 0: least firmware code, trivially testable.
- **Reliability:** requests carry `seq`, and the firmware answers every request with `ACK{seq, status}` or `NACK{seq, err}`. Asynchronous events (edge, SOF batch, log) carry their own monotonically increasing `evt_seq` so gaps are detectable. The orchestrator marks any trial with an event gap as invalid.
- **Versioning:** `HELLO` → `{protocol_major, protocol_minor, fw_semver, git_sha[20], build_id, board, usb_poll_hz, adc_fs_max, features_bitmap, serial_no}`. Major mismatch means the orchestrator refuses to run. Minor mismatch is allowed if the features bitmap covers the trial's needs.
- **Link:** 3 Mbaud 8N1 (FTDI FT232R/CP2102N both support it; Teensy LPUART and RP2040 UART both reach it at their clocks), with RTS/CTS flow control. **Firmware never blocks on UART TX**: events go into a ring buffer, and overflow sets a flag that invalidates the trial.

### 4.2 Commands

| Command | Payload (summary) | Phase |
|---|---|---|
| `HELLO` | none. Reply: version block above. | 0 |
| `PING` | `host_t`. Reply: `host_t, mcu_t_us` (clock mapping). | 0 |
| `STATUS` | Reply: USB state (configured/suspended), poll interval observed from SOF, ADC fs, buffer overflows, temperature, uptime. | 0 |
| `SET_PARAMS` | Detector params (k, p_first, M, L, hysteresis, timeout), fs, snippet size. | 0 |
| `CAL_LEVELS` | `{which: black|white, duration_ms}`. Reply: mean, σ, min, max, dominant flicker freq (via Goertzel/FFT on-device, or raw upload). | 0 |
| `LOAD_TIMELINE` | List of steps (below), max e.g. 4,096 steps, chunked. | 0 (single step), 1+ (full) |
| `RUN` | `{trial_id, start_at: now|t_us}`. Emits events, then `TRIAL_DONE{trial_id, status}`. | 0 |
| `ABORT` | Release all keys/buttons, stop timeline, disarm. | 0 |
| `LED_LOOPBACK` | `{n, interval_range_ms, pulse_ms}`: self-test (§6). | 0 |
| `RAW_CAPTURE` / `RAW_READ` | Arm raw capture / fetch chunk `{offset, len}`. | 0 |
| `SOF_LOG` | `{mode: off|all|around_actions}` | 0 |
| `SELFTEST` | Internal: ADC loopback to DAC/PWM pin, clock check, USB state. | 0 |
| `REBOOT_BOOTLOADER` | Only when a physical jumper/button is held. Prevents any remote reflash path (§7.4). | 0 |

**Timeline step types** (all times relative to `RUN` start, in µs):

- `KEY {t, usage, mods, down|up}`: HID usage IDs, not characters. The orchestrator maps text to usages per MUT keyboard layout, and each step gets an `action_id`.
- `TYPE {t0, interval_us, jitter_us, hold_us, usages[]}`: convenience that expands on-device into KEY steps (Scenario A query: 20 chars at 100 ms; backspaces are just usage 0x2A).
- `MOUSE_MOVE {t, dx, dy}`, `MOUSE_BTN {t, buttons}`.
- `MOUSE_PATH {t0, points[], speed_px_per_s, counts_per_px}`: the firmware interpolates at 1 report/ms, carries sub-count remainders so the path is exact, and supports acceleration-off assumptions (see D4). Scenario B.
- `WHEEL {t, v, h}` and `WHEEL_SCRIPT {t0, rate_hz, detents[] | pattern}`: Scenario C, 10 s of scripted wheel input.
- `ARM {t|action_id, detector_profile, window_ms}` / `DISARM`: attach photon detection to a specific action (usually implicit: `KEY … measure=true`).
- `MARK {t, gpio}`: toggle the sync GPIO (for the 1,000 fps camera or a scope).
- `WAIT_EDGE {timeout}`: gate the next step on a detected edge (e.g. cold start, Scenario D: launch, wait for first frame, then key).

Only one report per endpoint per frame is ever in flight. If the timeline asks for two keyboard reports in the same ms, the firmware queues them and logs the actual `t_ack` of each (never coalesces silently).

### 4.3 Event/log records (rig → orchestrator)

| Record | Fields |
|---|---|
| `EVT_HID` | `evt_seq, trial_id, action_id, kind(kbd/mouse), t_queue, t_ack, sof_frame, report_bytes[8]` |
| `EVT_EDGE` | `evt_seq, trial_id, action_id, t_first, t_50, dir, baseline, final, swing, thr_first, thr_50, fs, L, M, sample_idx, snippet_len` (+ snippet chunk) |
| `EVT_MISS` | `evt_seq, trial_id, action_id, reason(timeout/ambiguous/overrange)` |
| `EVT_SOF` | `evt_seq, frame_no, t_us` (batched) |
| `EVT_USB` | state changes (reset/suspend/configured), with `t_us` |
| `EVT_LOG` | Level + short text (errors, overflows) |
| `TRIAL_DONE` | `trial_id, status, n_events, overflow_flags, fw_sha` |

The orchestrator (other workstream) flattens these into the CSV. Per trial row: `latency_first_us = t_first − t_ack`, `latency_50_us`, `queue_to_ack_us`, and all firmware/detector parameters, so every number traces back to a firmware build and a detector setting.

---

## 5. Scenario coverage: Phase 0 vs later

| Scenario | Phase 0 need | Firmware feature | Design choice that keeps later phases open |
|---|---|---|---|
| Calibration: LED loopback (rig latency) | **Yes** | `LED_LOOPBACK` | Same detector path as trials. |
| Calibration: display floor, browser floor | **Yes** | Single `KEY` + `ARM` | Same as Scenario A step 1. |
| A: single keystroke | **Yes** | `KEY down → ARM → edge → KEY up` | Timeline engine, not a hard-coded loop. |
| A: 20-char query @100 ms + backspaces | Phase 1 | `TYPE` + per-action ARM windows | Per-action arming and toggling-marker direction inference already in the Phase 0 detector. |
| B: drag | Phase 2 | `MOUSE_PATH`, 2nd ADC channel for card-edge sensor | Mouse interface enumerated from day 1 (so MUT device config never changes between phases); reserve channel 2. |
| C: scroll, dropped frames | Phase 2 | `WHEEL_SCRIPT` + **long raw/frame-count mode** (detect each marker toggle over 10 s → inter-frame intervals) | Raw ring size and "multi-edge" detector mode stubbed in the protocol (`features_bitmap`). Primary metric per spec is from present timestamps and camera; the rig adds an independent photodiode frame-interval trace. |
| D: cold start | Phase 2–3 | `WAIT_EDGE` gating, long timeouts (seconds) | 64-bit timestamps, configurable timeouts. |

**Hard rule for Phase 0:** enumerate the *final* USB descriptor set (kbd + mouse with wheel/pan) from day 1. Changing HID descriptors later changes what the MUT's OS sees and could shift baselines between phases.

---

## 6. Self-test and calibration

### 6.1 LED loopback (rig latency)

- The firmware drives a GPIO-controlled LED placed under the suction mount in place of the screen (or a dedicated fixture that holds LED + photodiode at a fixed distance). The sequence is `t_led_on` from the GPIO write (the same clock and the same "action" abstraction as a HID report ack) → detector → `t_first/t_50`.
- **Reports:** `rig_latency_first` and `rig_latency_50` (p50/p95/p99/max over N=1,000 pulses with random 5–50 ms spacing), plus falling-edge latencies (the TIA may be asymmetric).
- **Expected:** TIA/photodiode rise of a few µs to tens of µs, plus the filter's deterministic delay, plus ≤ 1 sample. **Pass criterion (proposal):** p99 − p50 < 20 µs and p50 < 100 µs at default settings. Beyond that, the analog front end needs attention.
- **Also run with brightness steps** (LED PWM at several duty cycles, or series resistors) to check the auto-threshold works across the 60–1200 nit range the hardware team targets.

### 6.2 USB path self-check

- From the SOF log: the effective poll interval must be 1.000 ms ± tolerance, and `t_ack − t_queue` must be ~U(0,1] ms (KS test in the orchestrator). A skewed distribution means the host is polling at another rate or the report is being delayed. Flag the session.
- **End-to-end USB cross-check (optional, one-off per MUT):** Linux `usbmon` or a Windows ETW USB trace shows the host-side receive time of each report. Comparing with the rig's `t_ack` (via ping clock mapping) validates that transfer-complete ≈ host receipt.

### 6.3 Per-session health record

Emitted at session start/end and stored with results: `fw_sha, protocol_ver, poll_hz_observed, mcu_vs_sof_ppm, loopback p50/p95/p99, black/white levels + σ, detected flicker frequency, ADC overflow count, UART overflow count, temperature`. This record is the evidence for the spec's "two days agree within 2 ms at p50" criterion.

### 6.4 Validation against the 1,000 fps camera

- `MARK` GPIO drives a small indicator LED in the camera's field of view at `t_ack`, so camera frames can be aligned to rig events (the resolution is 1 ms per camera frame, so only a coarse check is possible). This is the photodiode spot-check the spec asks for.

---

## 7. Tooling

### 7.1 Language/framework

| Option | Verdict |
|---|---|
| **Teensyduino (Arduino core) via PlatformIO**, with a vendored fork of `cores/teensy4` for the USB timestamp/interval patches | **Recommended if Teensy.** Mature, fast, and the patches are small. PlatformIO pins the toolchain + core version and builds headless in CI. |
| **pico-sdk + TinyUSB (C)**, CMake | **Recommended if RP2040/RP2350.** First-class DMA/ADC/multicore APIs; TinyUSB is the reference HID stack. Avoid arduino-pico for the rig (extra abstraction around USB/IRQs). |
| Rust: embassy / rp-hal + `usbd-hid` | Attractive (type-safe protocol, `defmt`), but embassy-usb's HID path and ISR-level timestamping need more custom work, and fewer people can rebuild or audit it. Revisit only if the team is Rust-first. |
| MicroPython/CircuitPython | No: GC pauses, no IRQ-level control. |

**Structure:** `rig/firmware/{hal/<board>/, core/ (timeline, detector, protocol), app/}`. The `core/` directory is portable C with no hardware dependencies, so it also compiles for the host.

### 7.2 Host-side tests (run in CI with no hardware)

- **Protocol:** round-trip every message type (encode on firmware-side C compiled for x86, decode in Python and vice versa). COBS fuzzing (libFuzzer or Hypothesis): no crash, and it resyncs after garbage. CRC mismatch is rejected.
- **Detector:** golden tests on recorded raw captures (step, slow LCD transition, PWM-modulated 14.9 kHz synthetic, noise-only, overshoot/ringing, OLED scanning) with expected `t_first/t_50` and misses. Property tests: shifting input by Δ shifts the output by Δ; adding PWM at f doesn't change `t_50` by more than X µs with L set appropriately.
- **Timeline engine:** simulated clock, which asserts step order, mouse-path interpolation totals (sum of dx/dy = path length in counts), that all-up is emitted on abort, and that there's one report per frame.
- **HIL (hardware-in-the-loop) smoke test:** a self-hosted runner with a rig plugged into a Linux box, running `HELLO`, `LED_LOOPBACK` N=200, and checking a keyboard event is received on the Linux side via evdev. This runs nightly or on release, not on every PR.

### 7.3 Build/CI

- GitHub Actions: `pio run` / CMake builds for each board target, host tests, `clang-tidy`/`cppcheck`, and a size report.
- **Reproducible builds:** pinned toolchain (PlatformIO `platform = teensy@x.y.z`, or a pico-sdk tag + arm-none-eabi version in a container), with `SOURCE_DATE_EPOCH`. The binary embeds `git_sha`, `dirty` flag (release builds refuse to build dirty) and `build_id` (hash of the binary's other sections), which are returned in `HELLO`.

### 7.4 Agent-proofing (spec: "Agents cannot modify the harness, rig firmware…")

1. **Repo layout:** the firmware and protocol live under `rig/` (or a separate `latency-rig` repo; see D7). `CODEOWNERS`: `/rig/** @<rig-owner-human>`, with branch protection requiring code-owner review and blocking bot/agent accounts from being approvers.
2. **CI guard:** a required check fails any PR from an agent identity that touches `rig/`, `orchestrator/`, `parity/` or `dataset/`. This is belt-and-braces with CODEOWNERS.
3. **Versioned binaries:** firmware is flashed only from **tagged GitHub Releases** built by CI. The release notes carry the sha256 of `.hex`/`.uf2` (optionally signed with minisign/cosign). An `ALLOWED_FIRMWARE` list (sha + build_id) is committed under the human-owned orchestrator config.
4. **Runtime attestation:** the orchestrator calls `HELLO` at every session start and before every trial batch, refuses unknown `build_id`s, and writes `fw_sha/build_id` into every CSV row. Results from non-allow-listed firmware can't be produced silently.
5. **No remote reflash path:** the bootloader is entered only via a physical button (Teensy program button / RP2040 BOOTSEL) or `REBOOT_BOOTLOADER` gated by a jumper. The MUT side has no CDC/serial interface, so rung code on the MUT can't reach the rig at all (and Teensy's usual "reboot via serial baud 134" trick isn't available because no serial interface is enumerated on the MUT).
6. Optional: RP2350 supports secure boot with signed images. That's overkill for Phase 0, but it's a documented path if the rig is ever unattended with agents.

---

## 8. Task breakdown (Phase 0 firmware)

| # | Task | Est. (person-days) | Notes |
|---|---|---|---|
| F1 | Platform decision + board bring-up, HAL skeleton, 64-bit clock, UART link + isolator test at 3 Mbaud | 1.0 | Depends on hardware workstream's board choice. |
| F2 | USB composite HID (kbd + mouse + wheel/pan) with final descriptors; 1 kHz (and 8 kHz variant on Teensy); enumeration verified on Win/Linux/macOS | 1.5 | Teensy: core fork for interval/PFSC. RP2040: TinyUSB descriptors. |
| F3 | ISR-level `t_queue/t_ack` + SOF timestamping; all-up/abort safety | 1.5 | Core patch (Teensy) or dcd IRQ wrapper (RP2040). |
| F4 | ADC timer+DMA ring at 20–500 kS/s, block timestamps, overflow detection | 1.5 | Teensy ADC_ETC chain is the fiddliest part. |
| F5 | Detector (portable C): levels, baseline, hysteresis, confirm-M, boxcar, sub-sample interpolation, MISS | 2.0 | Includes host golden tests. |
| F6 | Protocol: schema + generator, COBS/CRC, HELLO/PING/STATUS/SET_PARAMS/ACK/NACK, event ring | 2.0 | Includes Python lib + round-trip tests (shared with the orchestrator workstream). |
| F7 | Timeline engine (Phase 0 subset: KEY, TYPE, ARM, MARK; stubs for MOUSE_PATH/WHEEL_SCRIPT behind feature bits) | 1.5 | |
| F8 | Calibration: CAL_LEVELS (+flicker freq estimate), LED_LOOPBACK, RAW_CAPTURE/RAW_READ | 1.5 | |
| F9 | CI, reproducible release builds, allow-list + HELLO attestation, CODEOWNERS | 1.0 | |
| F10 | Integration with orchestrator + first calibration runs on 2 days; fix-ups | 2.0 | Overlaps with Phase 0 checklist item 5. |
| **Total** | | **~15.5** (range 12–18) | One experienced embedded dev. Add ~30% if both platforms are prototyped. |
| Later | MOUSE_PATH + 2nd sensor (B): 2 d · WHEEL_SCRIPT + multi-edge frame-interval mode (C): 2–3 d · WAIT_EDGE/cold start (D): 1 d · HIL runner: 1–2 d | | |

### Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Teensy HS default gives 8 kHz, not 1 kHz, unnoticed | Numbers not comparable to real keyboards; spec mismatch | Explicit poll-rate config, SOF-derived `poll_hz_observed` logged every session, orchestrator asserts it. |
| Callback-context timestamps (TinyUSB deferred callbacks) | Tens–hundreds of µs of jitter from main loop | ISR-level stamping; loopback + `t_ack−t_queue` checks. |
| PWM/backlight flicker (MacBook 14.9 kHz, OLED PWM on some laptops) triggers false edges | Wrong or early `t_first` | ≥100 kS/s, flicker estimation in CAL, boxcar over integer periods, confirm-M, raw audits. |
| Slow LCD transitions + threshold choice shift results | Rung deltas depend on threshold | Report both `t_first` and `t_50`; thresholds fixed per machine per session and logged. |
| Ground loop / noise via two PCs | Noisy baseline, false triggers | Isolator on UART (§1.5); σ tracked in health record. |
| UART/event overflow during Scenario C | Silent data loss | Sequence numbers, overflow flags invalidate trials, post-hoc raw upload. |
| Host OS overrides polling (Linux `usbhid.*poll`, hubs, docks) | Hidden latency shift | Connect directly to a root port (no hub/dock); log `poll_hz_observed`. |
| RP2040 ADC DNL spikes (E11) | Small threshold bias at specific codes | Use RP2350, or keep thresholds off the spike codes / apply the correction LUT. Edge timing is barely affected. |
| Rung code fingerprints the rig (VID/PID/strings) | Gaming | Generic descriptors (D6), and descriptors identical across all phases. |

### Open decisions for a human

- **D1.** Headline input timestamp: `t_ack` (host has the data; recommended) or `t_queue`, or add a nominal "physical keyboard" offset? This affects every published number.
- **D2.** Poll rate for headline runs: 1 kHz only (spec), or also publish 8 kHz variants for 240 Hz runs?
- **D3.** Platform: Teensy 4.1 (headroom, RAM, Ethernet) or RP2350 + bridge Pico (cost, FS-native, easier replication)? Needs to be agreed with the hardware workstream.
- **D4.** Mouse mapping for Scenario B: disable OS pointer acceleration on every MUT (Windows "Enhance pointer precision" off, macOS acceleration curve, libinput flat profile) so counts map to pixels. Who owns the per-OS setup?
- **D5.** Key hold time and whether to randomize it (some apps act on key-up, IME composition may depend on it).
- **D6.** USB identity: a generic VID/PID/product string ("USB Keyboard") to prevent fingerprinting, vs an honest identity for reproducibility. A proposed compromise is to publish the descriptors in the rig docs but keep them generic.
- **D7.** Firmware in the main repo under `rig/` with CODEOWNERS, or a separate repo that agents have no write access to at all (stronger).
- **D8.** Primary edge metric for the headline: `t_first` (spec: "first photon change") vs `t_50`. Recommend `t_first` headline, `t_50` as a robustness check.

---

## Sources

- PJRC, *Using USB Keyboard with Teensy*: https://www.pjrc.com/teensy/td_keyboard.html
- PJRC, *Using USB Mouse with Teensy*: https://www.pjrc.com/teensy/td_mouse.html
- PaulStoffregen/cores (teensy4 `usb_desc.h`, `usb.c`, `usb_keyboard.c`), inspected at commit 7f107ee: https://github.com/PaulStoffregen/cores
- Trip93/teensy4_mouse, 8 kHz HS mouse on Teensy 4: https://github.com/Trip93/teensy4_mouse
- hathach/tinyusb (`tud_sof_cb`, `tud_hid_report_complete_cb`, `dcd_rp2040.c`), inspected at commit 61d9f45: https://github.com/hathach/tinyusb
- TinyUSB issue #1673, two ports (native + PIO) on RP2040: https://github.com/hathach/tinyusb/issues/1673
- sekigon-gonnoc/Pico-PIO-USB: https://github.com/sekigon-gonnoc/Pico-PIO-USB
- pedvide/ADC (Teensy 4 timer+DMA ADC): https://github.com/pedvide/ADC and `examples/adc_timer_dma`
- NXP community, Teensy 4.1 PIT→ADC_ETC DMA chain issue: https://community.nxp.com/t5/i-MX-RT-Crossover-MCUs/Teensy-4-1-DMA-Hardware-Chain-Issue-4-1-PIT-Trigger-to-ADC-ETC/m-p/2116402
- RP2040 ADC ENOB/DNL (erratum RP2040-E11) and RP2350 fix: https://www.instructables.com/Mass-ADC-Testing-DNL-INL-ENOB-Ads1115-Mcp4728-Stab/ , https://github.com/kitanokitsune/rp2040adc_correction , https://en.wikipedia.org/wiki/RP2350
- MacBook Pro mini-LED PWM ~14.9 kHz (Notebookcheck measurement, discussed): https://forums.macrumors.com/threads/macbook-pro-14-2021-pwm-screen-flickering.2319440/
- USB 2.0 Specification §9.6.6 (bInterval semantics for FS vs HS interrupt endpoints): https://www.usb.org/document-library/usb-20-specification
- Prior art for photodiode latency tools: OSLTT https://github.com/OSRTT/OSLTT , OSRTT https://github.com/andymanic/OSRTT ; Typometer https://github.com/pavelfatin/typometer
