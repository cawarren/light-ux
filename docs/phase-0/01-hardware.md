# Phase 0 scope: rig hardware and bill of materials

Workstream: rig **hardware** (the microcontroller board, light sensor, calibration LED, mount, cabling, camera).
Firmware and orchestrator software are out of scope here. Where hardware choices constrain them, this document says so.

Spec requirements this document answers (from "Measurement harness", "Calibration", "Test environment"):

- The MCU shows up as a USB keyboard and mouse polled at 1,000 Hz, and timestamps every report it sends and every light change it sees.
- The photodiode and amplifier are sampled at 20 kHz or faster and held over a 64 px marker. **Revised to ≥100 kS/s**: 20 kHz aliases the MacBook's 14.9 kHz PWM.
- The measured loop never passes through the orchestrator.
- An LED driven directly by the MCU measures the rig's own latency.
- Parts cost well under $100, excluding a 1,000 fps validation camera.
- The rig works on 4 machines: a Windows/Linux desktop with an external monitor at 60 Hz and 240 Hz, a MacBook Pro (120 Hz mini-LED), and a budget Windows laptop.

---

## 1. Microcontroller

### Comparison

| Criterion | **Teensy 4.0 / 4.1** (NXP i.MX RT1062) | **RP2040** (Pico) | **RP2350** (Pico 2) |
|---|---|---|---|
| USB device speed | **High-speed, 480 Mbit/s** ([PJRC](https://www.pjrc.com/store/teensy40.html)) | Full-speed, 12 Mbit/s | Full-speed USB 1.1, 12 Mbit/s ([RP2350 datasheet](https://pip.raspberrypi.com/documents/RP-008373-DS-rp2350-datasheet.pdf)) |
| Max HID poll rate | 8 kHz (125 µs microframes). Setting bInterval=1 gives 8 kHz with no host driver ([PJRC forum](https://forum.pjrc.com/index.php?threads/usb-2-0-teensy-port-at-8000-hz-poll-rate.63948/), [teensy4_mouse](https://github.com/Trip93/teensy4_mouse)) | 1 kHz (1 ms frames) | 1 kHz |
| Polling-phase quantization | 0–125 µs at 8 kHz, 0–1 ms at 1 kHz | 0–1 ms | 0–1 ms |
| ADC | 2× 12-bit SAR. About 1 MS/s sustained with DMA, using the ADC library ([forum](https://forum.pjrc.com/index.php?threads/adc-library-with-support-for-teensy-4-3-x-and-lc.25532/page-10)) | 12-bit, 500 kS/s, with known DNL spikes | 12-bit, 500 kS/s |
| Timers | ARM DWT CYCCNT at 600 MHz (1.67 ns), GPT/QTimer input capture, on-chip analog comparators | 1 µs timer, PIO for cycle-exact capture | Same as RP2040, plus Cortex-M33 DWT |
| Second channel to orchestrator | 7 hardware UARTs (multi-Mbaud). The 4.1 also has 100 Mbit Ethernet (needs a ~$10 MagJack kit, and is galvanically isolated by its magnetics). The 4.1's second USB port is **host-only**. | UART, or a second USB *device* via PIO-USB (full-speed only, fiddly) | Same as RP2040 |
| Price | 4.0: ~$24. 4.1: ~$31.50 ([Micro Center](https://www.microcenter.com/brand/4294818527/pjrccom)) | ~$4–5 | ~$5–13 ([Adafruit](https://www.adafruit.com/product/6006)) |
| Prior art | Open-Source-LDAT (Teensy 4.1, 8 kHz) | click2photon / FrameProbe (QT Py RP2040) | — |

### Recommendation: **Teensy 4.0** as the reference rig; buy two.

- The spec asks for 1,000 Hz. Both boards can do it, but only the Teensy can also run at **8 kHz**, which cuts input-side quantization noise by 8×. That matters for the "two days agree within 2 ms at p50" criterion and for the "p95 improves by more than the noise band" acceptance loop. The same board supports both rates; the choice is a descriptor constant (open decision D2).
- The ADC has ≥50× headroom over 20 kHz. We can sample at 100–200 kS/s, keep the raw traces, and filter out PWM digitally (section 2).
- It has a 600 MHz cycle counter and plenty of RAM (1 MB) to keep a ring buffer holding about 1 s of samples.
- **Channel to the orchestrator:** a hardware UART at about 3 Mbaud (firmware's target) into an **isolated** USB-UART bridge (ADuM1201**BRZ**, 10 Mbps grade, then a CP2102N at 3 Mbaud max, or an FT232H for headroom), which plugs into the orchestrator. Set the Teensy's own USB to a "Keyboard + Mouse" type with no Serial interface, so the machine under test only ever sees a plain keyboard and mouse. It also never runs a driver or host process for the rig.
- **Polling-rate note (from the firmware scope):** Teensy 4.x HID endpoints default to bInterval=1 at high speed, which is **8 kHz**. Getting the spec's 1 kHz means setting bInterval=4 (8 microframes) in the descriptor or patching the core. The RP2040/RP2350 are full-speed, so 1 kHz is native and 8 kHz is impossible.
- **Firmware lean:** the firmware workstream leans towards the Teensy 4.1 (patched descriptor), with RP2350 plus a UART bridge as the fallback. This is compatible with the hardware plan: the 4.0 and 4.1 share the MCU, ADC and UART pins used here. The **lab builds on one 4.1** (see Lab extras), and the **replication BOM lists the 4.0**. The 4.1 was chosen for development because of firmware's preference, its Ethernet/SD options and its extra pins.
- The Teensy 4.1 is only worth the extra $8 to a replicator if they want Ethernet (link isolation for free) or SD-card logging. Firmware is identical, so a replicator can use either. The **RP2350 is the named fallback** for a cheaper rig at 1 kHz. Its trade-offs are 1 ms quantization and more firmware work to get a second channel.

**Hardware constraints to hand to the firmware workstream:**

1. Timestamp each keypress on the **USB IN transfer-complete** event (when the host ACKs the report), not when it is queued. Log both. The gap between them is the polling-phase delay, and it should be measured, not assumed.
2. Run the ADC as a continuous DMA stream at a fixed rate from a hardware timer. Timestamp buffers with CYCCNT. Detect in firmware, but also stream or keep raw windows around each event for offline re-analysis.
3. Teensy analog inputs take **3.3 V max and are not 5 V tolerant**. Power the amplifier from the Teensy's 3.3 V rail.
4. When the USB type has no Serial, flashing firmware means pressing the button in Teensy Loader. Do it from the orchestrator machine, never the machine under test.

---

## 2. Light sensor and front end

### Why a discrete photodiode and TIA, and not the common hobby parts

| Option | Verdict |
|---|---|
| LDR (photoresistor), used by [sqwk/arduino-latency-test](https://github.com/sqwk/arduino-latency-test) | **Reject.** Response time is tens of ms. |
| Phototransistor / ambient-light sensor (TEMT6000 in [Open-Source-LDAT](https://github.com/S4N-T0S/Open-Source-LDAT), ALS-PT19 in [OpenLDAT](https://github.com/adolfintel/OpenLDAT)) | **Avoid.** Rise time depends on light level and load (tens of µs to ms), and it saturates easily. Open-Source-LDAT notes its thresholds were "calibrated on an OLED display" and had to be retuned for LCDs. |
| **OPT101** (photodiode and TIA on one chip): 14 kHz bandwidth, ~28 µs rise with the internal 1 MΩ, 0.45 A/W ([TI datasheet](https://www.ti.com/lit/ds/symlink/opt101.pdf)). Cheap CJMCU-101 modules exist ([Amazon](https://www.amazon.com/OPT101-Intensity-Monolithic-Photodiode-CJMCU101/dp/B0B2CWRG98)). | **Good fallback / comparison sensor.** The problem is that the fixed 1 MΩ gain **saturates** on a screen at full brightness (estimate below). Lowering the gain means rewiring pins 2, 4 and 5 with an external resistor, which the modules don't make easy. Buy one for cross-checking. |
| **Vishay BPW34** (7.5 mm² PIN photodiode, ~100 ns rise) with a **rail-to-rail op-amp TIA** with selectable gain | **Recommended.** This is the topology FrameProbe/click2photon converged on (VBPW34S + TLV9061, [repo](https://github.com/marconett/click2photon)). Their first prototype "had less sensitivity", which the TIA gain fixes. |

### Sizing (to be checked on the bench)

- Illuminance on a detector pressed against a Lambertian screen is E ≈ π·L. At 250 nits that is about 800 lux. At 500 nits (MacBook SDR, bright monitors) it is about 1,600 lux.
- BPW34: ~50 µA at 1 klx under illuminant A. Under display white (little IR), assume **~20–30 µA/klx**. That gives **~15–50 µA** across our displays at full brightness.
- The ADC has a 3.3 V full scale, so aim for ~2.5 V at white: **Rf ≈ 47 kΩ nominal**. Provide 22k / 47k / 100k options (jumper or DIP switch) or a 100 kΩ trimmer in series with 10 kΩ. Add Cf ≈ 10–22 pF (C0G) across Rf for stability.
- Bandwidth: MCP6002 (1 MHz GBW), BPW34 Cd ≈ 70 pF at 0 V bias, Rf = 47 k. The TIA can reach roughly 100–200 kHz. Cf sets it lower, to about 150–340 kHz (1/2πRfCf). The front end rises in **a few µs**, which is negligible against display transitions (OLED ~0.1 ms; LCD 1–30 ms) and is measured by the calibration LED anyway.
- An optional small reverse bias (e.g. 1.65 V from a 3.3 V divider on the non-inverting input) cuts Cd to ~25 pF. It isn't needed at these speeds.

### Front-end circuit (one per sensor head; build two heads)

```
            Cf 10-22pF
          +----||-----+
          |   Rf 47k  |          (Rf selectable 22k/47k/100k)
          +---/\/\----+
          |           |
 BPW34    |  |\       |
 K ------ +--|-\      |
             |  >-----+----[100R]---+---> to Teensy A0 (via shielded cable)
 A ---+------|+/ MCP6002           [33nF @100kS/s, 10nF @>=200kS/s: anti-alias]
      |      |/  (3.3V single supply)   |
     GND    GND                        GND
 Decouple: 100nF + 10uF at op-amp supply, in the sensor head.
```

The op-amp lives **in the sensor head**. The cable then carries a low-impedance voltage, not a nano-amp current, which avoids noise pickup. A zero-light offset near 0 V is fine on a rail-to-rail part. If black levels (OLED true black) need resolving, use the reverse-bias trick or a small positive offset on the non-inverting input.

### PWM backlight and flicker (the main source of false triggers)

Measured facts:

- **The MacBook Pro mini-LED flickers at a constant ~14.88 kHz at every brightness** ([Notebookcheck MBP 14 M4 review](https://www.notebookcheck.net/Apple-MacBook-Pro-14-2024-review-The-M4-Pro-and-matte-display-are-massive-upgrades.914597.0.html), [LEDStrain](https://ledstrain.org/d/3412-macbook-pro-m4-pwm)). "Full brightness" does **not** remove it. Sampled at exactly 20 kHz, it aliases to ~5 kHz ripple.
- Many OLEDs dim with PWM at 240–480 Hz ([Blur Busters](https://forums.blurbusters.com/viewtopic.php?t=12196)). OLEDs also dip in brightness once per refresh. Budget laptop LCDs often use PWM below 100% brightness.

Hardware mitigations (analysis rules go to the firmware and orchestrator workstreams):

1. **Oversample at ≥100 kS/s** and low-pass in firmware with a boxcar or FIR spanning whole PWM periods (e.g. 67 µs for 14.88 kHz). **Run the ADC at ≥100 kS/s, not the spec's 20 kHz** (agreed across workstreams). With 100 kS/s the Nyquist limit is 50 kHz, so fit an anti-alias RC of 100 Ω and 33 nF (fc ≈ 48 kHz, ~3 µs group delay, measured by the calibration LED). At ≥200 kS/s, use 100 Ω and 10 nF (fc ≈ 160 kHz). The TIA bandwidth of ~150–300 kHz and the BPW34's ~100 ns rise both support ≥100 kS/s, and the Teensy ADC can do about 1 MS/s with DMA. **Do not** put a heavy analog low-pass in the loop: a 2 kHz pole would add ~80 µs of fixed delay and blur the edge. (The calibration LED would still measure that delay, but raw data is better.)
2. Before Phase 1, record a **flicker characterization** of every display at the chosen brightness: a static white and a static black trace, 1 s each. That gives the ripple amplitude and frequency, and sets the detection noise band per machine.
3. Trigger on the **large black↔white step** of the marker, with the threshold set as a fraction of the (white − black) span measured **per trial** from the pre-trigger baseline. Don't use a fixed ADC count. NVIDIA LDAT fires at a ≥6% rise over the initial luminance ([HotHardware](https://hothardware.com/reviews/nvidia-reviewer-toolkit-explained), [igor'sLAB](https://www.igorslab.de/en/nvidia-ldat-latency-display-analysis-tool-presented-and-tested/)). OSRTT looks for departure from a stable baseline ([OSRTT docs](https://andymanic.github.io/OSRTTDocs/docs/input-lag/measurements/)).
4. The marker **alternates** black→white and white→black, and LCD rise and fall times differ by milliseconds. **Tag and report each direction separately** (decision D3).

### Analog stream or comparator?

**Analog stream is primary.** It adapts to each display's levels, keeps raw traces for re-analysis and auditing, and handles PWM. A comparator (the i.MX RT's on-chip ACMP, or an external TLV3201 into a GPT input-capture pin) gives ns-level edge timestamps but needs a fixed threshold that PWM ripple can falsely trip. Leave a spare pad and pin for an optional comparator. Don't depend on it.

### Calibration LED (rig latency)

- One **white 5 mm LED** (a bright green one also works; BPW34 response is fine) on a Teensy GPIO through ~150–220 Ω, mounted in a **calibration puck**. The puck is a small printed disc that the sensor head presses onto exactly as it would onto a screen, with the LED behind a diffuser (a strip of white paper or PTFE tape) so its brightness roughly matches a screen at full brightness.
- The measured value is GPIO edge → detection in firmware. It includes LED turn-on (~tens of ns), TIA (µs), ADC conversion, DMA block latency and the detection filter. Expect about **5–100 µs**, depending mostly on the DMA block size and filter length. Anything above ~0.1 ms points to a firmware or filter problem.
- A **second LED, the sync LED**, is lit on the USB transfer-complete event and sits **in the camera's field of view** next to the marker. The 1,000 fps camera can then measure (sync LED on → marker change) directly and compare it with the photodiode for the same trial. This is the cheapest way to spot-check the photodiode against the camera trial by trial.

---

## 3. Mount, shielding, cabling

**Sensor head.** A 3D-printed black PETG or PLA cylinder, ~20 mm OD. The BPW34 sits behind a **~6 mm aperture**, 2–4 mm back from the screen, and the op-amp board sits behind it. The face gets a **black EVA or neoprene foam gasket ring** that blocks ambient light and protects the panel. The marker's physical size bounds the aperture:

| Display | Marker size (64 CSS px) |
|---|---|
| 27" 1440p, 100% (109 ppi) | ~15 mm |
| 27" 4K, 150% | ~15 mm |
| MacBook Pro, 2× (254 ppi) | ~12.8 mm |
| Budget 15.6" 1080p, 125% | ~14 mm |

A 6 mm aperture leaves a ≥3 mm margin all round for placement error.

**Holding it on the screen.** Different displays need different methods. Build the head with a centre M4 insert so any of these attach:

| Display | Method | Notes |
|---|---|---|
| Glossy glass (MacBook Pro, some laptops) | 40–45 mm suction cup with M4 stud | Fine on glass. Check it after every session. |
| **Matte anti-glare desktop monitor** | **Elastic strap or cord over the top of the monitor** with a counterweight or clip at the back. This is what LDAT and OSRTT do. | Suction cups hold badly on matte coatings and can mark them. |
| Laptop lid (budget laptop) | Strap wrapped around the lid | Keep the lid angle fixed with a wedge. |
| Any | Optional small articulating arm (camera "magic arm", ~$10) clamped to the desk | Most repeatable. Touches no display. |

Use **light pressure only**. Pressing on an LCD causes pooling and changes local response. For **repeatable placement across days**, have the floor apps show a crosshair or targeting pattern, position the head for peak reading, and photograph the placement. A few mm of drift changes how much of the marker the sensor sees, but not the timing, provided it stays inside the marker.

**Ambient light.**

- Run sessions in a darkened room, or at least with no mains-flicker lighting on the screen. Mains LED and fluorescent light ripples at 100/120 Hz.
- Log a "dark baseline" at the start of each session.
- Keep the room constant between day 1 and day 2, as the success criterion requires.

**Cabling.**

- **Sensor head → rig:** 1 m of 4-conductor **shielded** cable (3.3 V, GND, OUT, and shield grounded at the rig end only), with TRRS 3.5 mm plug and jack or JST-XH connectors. LDAT also uses a 3.5 mm jack. Budget for two heads.
- **Teensy USB → machine under test:** a short (≤1 m), known-good micro-B data cable, plugged into a **rear-panel or chipset port directly**. No hub, no monitor-hub or dock ports: hubs add polling jitter and transaction-translator effects. The MacBook needs a USB-C to micro-B cable. **Strain-relieve the micro-B connector** (hot glue or a zip-tie to the enclosure), because Teensy micro-USB jacks are known to be fragile.
- **Rig → orchestrator:** Teensy Serial1 TX/RX at about 3 Mbaud → **ADuM1201BRZ digital isolator** → CP2102N USB-UART → orchestrator. This avoids a **ground loop between two PCs** through the rig, which would inject noise into the analog signal. The rig side of the isolator is powered from the machine under test via the Teensy, and the orchestrator side from the bridge's 3.3 V. The isolator adds a fixed delay of about 100 ns to UART traffic, which is irrelevant because nothing on that link is timed.
- **Enclosure:** a small printed box or project box holding the Teensy, isolator and CP2102N on perfboard, with jacks for sensor heads 1 and 2, the calibration LED and the sync LED.

```
 [Sensor head 1] --shielded TRRS--+
 [Sensor head 2] --shielded TRRS--+     +------------------------------+
 [Cal LED puck]  -----------------+---> | Teensy 4.0                   |--USB (HID kbd+mouse only)--> Machine under test
 [Sync LED, in camera view] ------+     |   Serial1 (3M) -> ADuM1201BRZ|
                                        |   -> CP2102N USB-UART        |--USB (isolated)--> Orchestrator
                                        +------------------------------+
```

---

## 4. Prior art: design choices and pitfalls

| Rig | Design | What to crib | Pitfalls it hit |
|---|---|---|---|
| **NVIDIA LDAT** ([NVIDIA](https://www.nvidia.com/en-us/geforce/news/nvidia-reviewer-toolkit/), [HotHardware](https://hothardware.com/reviews/nvidia-reviewer-toolkit-explained), [igor'sLAB](https://www.igorslab.de/en/nvidia-ldat-latency-display-analysis-tool-presented-and-tested/)) | Photodiode luminance sensor in a puck on a **cord/strap**. A modified mouse is wired to the LDAT so the true switch closure is timestamped. Trigger at ≥6% luminance rise over the initial value. | Strap mount. A relative threshold ("first photon change"). Timestamping the input at its true source. | Closed hardware, reviewer-only. A small relative threshold is sensitive to flicker ripple. |
| **OSRTT / OSRTT Pro** ([repo](https://github.com/andymanic/OSRTT), [docs](https://andymanic.github.io/OSRTTDocs/docs/input-lag/measurements/)) | Several photodiodes in parallel (5 in Pro, 6 in Pro CS) and a **digital-pot adjustable gain**. ADC at ~55 kS/s over a 200 ms window streamed to a PC app over USB serial. Strap mount. | Adjustable gain is **necessary**, because display brightness varies a lot. A windowed capture around each event. | Admits that inputs aren't aligned to the refresh window, so there is up to one refresh of uncertainty. We fix this with random 50–250 ms delays and many trials. |
| **Open-Source-LDAT** ([repo](https://github.com/S4N-T0S/Open-Source-LDAT)) | Teensy 4.1, TEMT6000, true 8 kHz by patching Teensy core files at build time. Can also drive a real mouse's switch through a BC547 transistor. | Evidence that **8 kHz HID on a Teensy 4 works**. The patch is needed only for the stock mouse descriptor, and our firmware can own its descriptor. | Thresholds tuned on an OLED (black = 0) broke on LCDs. Ambient-light sensors are slow. |
| **click2photon / FrameProbe** ([repo](https://github.com/marconett/click2photon)) | QT Py RP2040, **VBPW34S + TLV9061 TIA**, 12k samples per run, 3D-printed head on a custom PCB. Reports mean±CI, median, p5/p95. | **The same sensor topology we recommend**, independently validated. | Prototype v1 lacked sensitivity, so provide adequate gain. Limited to 1 kHz (full-speed USB). |
| **OpenLDAT** ([repo](https://github.com/adolfintel/OpenLDAT), [Dossena 2022, JSID](https://sid.onlinelibrary.wiley.com/doi/10.1002/jsid.1104)) | ALS-PT19 sensor, 10-bit, up to 30 kHz, **LED validation mode**, click generation. Peer-reviewed. | The LED-validation idea, i.e. our calibration LED. Cite it as the academic precedent. | 30 kHz and 10 bits is marginal for PWM displays. |
| **Dan Luu, "Computer latency: 1977–2017"** ([post](https://danluu.com/input-lag/)) | iPhone SE at 240 fps. Anything under 40 ms was re-shot at 1,000 fps on a **Sony RX100 V**. Timed from key-start-moving to screen finished. | The RX100 V as a validation camera. Report precision honestly: he **rounded to 10 ms**. | Manual frame counting doesn't scale to 500 trials. That's why the photodiode is primary and the camera only spot-checks. |
| **Typometer** ([repo](https://github.com/pavelfatin/typometer)) | Software only: synthetic OS input events plus screen capture. | A cross-check. Its docs note that compositors add ≥1 frame. | Misses the display and USB entirely. Windows 11 high-latency reports ([issue #12](https://github.com/pavelfatin/typometer/issues/12)). |
| **Is It Snappy?** ([site](https://isitsnappy.com/), [announce](https://chadaustin.me/2017/04/announcing-is-it-snappy/)) | iPhone 240 fps and a manual scrubbing UI, giving 4.2 ms resolution. | Quick sanity checks during bring-up. | 4 ms resolution is too coarse for our deltas. |
| **piLagTester** ([blog](https://alantechreview.blogspot.com/2020/05/input-lag-measurement-using-slow-high.html)) | Gets ms accuracy from slower cameras through statistics. | An idea for a cheaper camera path. | — |

Lessons that feed straight into our design:

- Use a photodiode, not an LDR, phototransistor or ALS.
- Make the gain adjustable.
- Use a relative, per-trial threshold.
- Account for refresh-phase uncertainty by randomizing.
- Timestamp at the true input event.
- Keep raw traces.
- Report precision honestly.

---

## 5. Bill of materials

Prices are approximate retail as of Sep 2026, in USD, before shipping and tax. Unit prices apply at qty 1 to 10. Suppliers: PJRC, DigiKey or Mouser (DK/M), Adafruit (AF), Amazon (AMZ).

### Core rig (required to replicate)

| # | Part | Qty | ~Unit | ~Ext | Supplier |
|---|---|---|---|---|---|
| 1 | Teensy 4.0 (no pins) | 1 | $23.80 | $23.80 | PJRC, SparkFun, Micro Center |
| 2 | Vishay BPW34 PIN photodiode (through-hole) | 2 | $1.30 | $2.60 | DK/M |
| 3 | Microchip MCP6002-I/P dual RRIO op-amp, DIP-8 (alt: TI TLV9062/TLV9061 on an adapter) | 2 | $0.45 | $0.90 | DK/M |
| 4 | Passives: 1% resistors (22k, 47k, 100k, 150R, 100R), C0G caps (10 pF, 22 pF), 100 nF and 10 µF decoupling, 3-position DIP switch or jumpers for gain | 1 lot | — | $4.00 | DK/M |
| 5 | 5 mm high-brightness white LEDs (calibration and sync) | 3 | $0.35 | $1.05 | DK/M |
| 6 | Analog Devices **ADuM1201BRZ** dual-channel digital isolator, 10 Mbps grade (the AR grade is only 1 Mbps, too slow for 3 Mbaud), SOIC-8, plus SOIC-to-DIP adapter | 1 | $4 + $1 | $5.00 | DK/M |
| 7 | **CP2102N** USB-UART breakout (up to 3 Mbaud), e.g. Adafruit CP2102N Friend. Use an Adafruit FT232H (#2264, ~$15, 12 Mbaud) if more than 3 Mbaud is needed. **Do not use a CP2104**, which tops out at 2 Mbaud. | 1 | $8 | $8.00 | AF/DK |
| 8 | 3.5 mm TRRS panel jacks | 4 | $1.00 | $4.00 | DK/M, AF |
| 9 | Shielded 4-conductor cable, 1 m, with TRRS plugs (or TRRS audio cable) | 2 | $3.00 | $6.00 | AMZ, DK |
| 10 | Perfboard, headers, hookup wire | 1 lot | — | $4.00 | AF/AMZ |
| 11 | Micro-B USB data cable, 1 m (Teensy → machine under test), plus a USB-C to micro-B cable for the Mac | 1 + 1 | $4 + $5 | $9.00 | AMZ |
| 12 | USB cable (CP2102N → orchestrator) | 1 | $4.00 | $4.00 | AMZ |
| 13 | Suction cups, 40–45 mm, with M4 stud (pack) | 1 | $7.00 | $7.00 | AMZ |
| 14 | Elastic cord or strap and cord locks (matte monitors, laptop lid) | 1 | $4.00 | $4.00 | AMZ |
| 15 | Black EVA/neoprene foam sheet, 2 mm, adhesive-backed | 1 | $5.00 | $5.00 | AMZ |
| 16 | 3D-printed sensor heads, calibration puck, enclosure (black PETG/PLA, ~40 g) | 1 | — | $1.50 | Own printer (or ~$15 from a print service) |
| 17 | M4 heat-set inserts and screws, heatshrink | 1 lot | — | $2.00 | AMZ |
| | **Core total** | | | **≈ $91** | |

About $91 is **under $100 but not "well under"**. Most of the margin is cables, suction cups and foam, which most labs already have. With parts-bin passives, cables and foam, a replicator's real cost is **about $55–65**. There are two ways to cut further:

- Drop the isolator (−$4.50) and accept the ground loop (not recommended for us).
- Swap in a Pico 2 for the Teensy (−$18), at 1 kHz only.

### Lab extras (recommended for this project, not counted in the replication BOM)

| Part | Qty | ~Cost | Why |
|---|---|---|---|
| Teensy 4.1 (dev unit, matches the firmware workstream's lean; the core-BOM 4.0 is the spare and reference replication build) | 1 | $31.50 | A second rig for parallel Mac and PC sessions, plus Ethernet/SD options |
| CJMCU-101 OPT101 module | 1 | $7 | Independent sensor cross-check at dim settings |
| Camera "magic arm" and clamp | 1 | $12 | Repeatable, no-touch mount for the desktop monitor |
| Raspberry Pi Pico 2 | 1 | $5 | To validate the cheap replication path at 1 kHz |
| **Extras total** | | **≈ $56** | |

### Validation camera (excluded from the $100 budget)

| Option | Real fps / resolution | Cost | Caveats |
|---|---|---|---|
| iPhone slo-mo | 240 fps (4.2 ms) | $0 (owned) | Too coarse for 1 ms honesty checks. Fine for bring-up sanity checks. |
| **Samsung Galaxy "960 fps Super Slow-mo"** | Recent models capture at 240/480 fps and **interpolate** up to 960 ([Android Authority](https://www.androidauthority.com/samsung-galaxy-s22-ultra-960fps-3139987/), [Android Central](https://www.androidcentral.com/galaxy-s20-ultra-cannot-capture-true-960fps-super-slow-mo-video)) | — | **Reject for marker honesty and tearing.** Interpolated frames invent intermediate states, which is exactly what we're trying to detect. |
| **Sony RX100 V / VA / VI / VII (used)** | True 960–1,000 fps HFR. 1244×420 for ~3 s (quality priority) or 912×308 for ~6 s ([Sony help guide](https://helpguide.sony.net/dsc/1810/v1/en/contents/TP0001138299.html), [DPReview](https://www.dpreview.com/forums/thread/4092436)) | **~$325–450 used** ([Phoblographer, Sep 2026](https://www.thephoblographer.com/2026/09/14/the-sony-rx100v-is-10-years-old-is-it-good-in-2026/), [MPB](https://www.mpb.com/en-us/product/sony-cyber-shot-rx100-mark-v)) | What Dan Luu used. Buffer-limited clips (2–6 s), and slow buffer writes between clips. **Rolling shutter**, which is fast on a stacked sensor but must be accounted for in the tearing check. Low resolution is fine for a 64 px marker at close range. **Recommended.** |
| Kron Chronos 1.4 / 2.1-HD | ~1,000–1,500 fps at 720p, long record, global shutter (1.4) | $2.5–3.5k (1.4), $5k (2.1) ([Newsshooter](https://www.newsshooter.com/2021/01/09/krontech-chronos-2-1-hd-1000fps-for-4995-usd/)). Rental available ([ATEC](https://www.atecorp.com/products/kron-technologies-inc/chronos-1-4)) | Best evidence for tearing (global shutter, long captures). **Rent** for the Gate-1 honesty audit if the RX100's rolling shutter proves ambiguous. |

Camera practice: put the **sync LED and the marker in the same frame**. Use a 1/1000 s or shorter shutter with the screen at full brightness. Mount the camera on a tripod, square to the screen.

---

## 6. Risks, lead times, open decisions

### Lead times

| Item | Typical lead |
|---|---|
| Teensy (PJRC ships from Oregon; also SparkFun, Micro Center, Adafruit) | 2–5 days in the US. Stock has been fine recently, but PJRC had shortages in 2022. **Buy 2.** |
| DigiKey/Mouser parts | 1–3 days |
| Amazon cables and mounts | 1–2 days |
| Printed parts | Same day with a printer; 5–10 days from a print service |
| Used RX100 | 3–7 days (MPB/eBay) |
| Chronos rental | 1–2 weeks to book |

**Critical path: about 1 week from order to bench bring-up.**

### Risks

| Risk | Effect | Mitigation |
|---|---|---|
| **Scan-out position.** Displays scan top to bottom, so a marker in a bottom corner lights up to about 1 frame (16.7 ms at 60 Hz) later than one at the top. | A systematic offset. It becomes a false layer delta if the rungs and the floor apps put the marker at **different screen positions**. | Fix the marker at identical **absolute screen coordinates** for every rung and both floor apps, not a window-relative corner (browser chrome shifts the page). Document it. Decision D4. |
| Photodiode saturation or low signal across displays (250–1,600 nits, OLED true black) | Clipped edges, missed triggers | Switchable gain, and a per-display gain setting recorded in session metadata. The OPT101 module as a cross-check. |
| **MBP mini-LED 14.88 kHz PWM at every brightness.** OLED refresh dips. Budget-LCD PWM. | False or jittered triggers | ≥100 kS/s with digital filtering, a per-display flicker characterization, relative thresholds, camera cross-check. The spec's "full brightness" mitigation is **not sufficient on the MacBook**. Flag this to the spec owner. |
| LCD rise/fall asymmetry and slow budget-laptop panels (GtG can be 10–30 ms) | Results depend on the threshold definition and flip direction | Report each direction. Fix a "first photon change" threshold (e.g. 10% of the span). Decision D3. |
| Ground loop between the machine under test and the orchestrator | Noise on the analog signal; worst case, a current path between PCs | ADuM1201BRZ isolation on the orchestrator link |
| Mount drifts between sessions | Threatens "day-to-day within 2 ms at p50" (mostly through signal amplitude and missed triggers, less through timing) | Targeting pattern, peak-reading placement, photographed placement, and a strap or arm instead of suction on matte panels |
| USB path differences (hub, dock, front-panel port) | Extra polling jitter | Direct rear port, the same port every session, recorded in metadata |
| macOS: 8 kHz HID behaviour and the "Keyboard Setup Assistant" on first plug-in | Enumeration friction; possibly capped poll rate | Check the effective poll rate on each OS during bring-up (Windows: USBPcap/Wireshark or USBTreeView; Linux: `usbmon`; macOS: IORegistry and report-interval stats from rig timestamps). Dismiss the assistant once. |
| Fragile Teensy micro-USB jack | The rig dies mid-study | Strain relief and a spare Teensy |
| Camera rolling shutter (RX100) | Ambiguous tearing verdicts | Put the camera's scan direction on record; rent a global-shutter Chronos if needed |
| Firmware dependency | Hardware bring-up needs minimal firmware (ADC stream, LED toggle, HID) | Agree the pin map and a "bring-up sketch" with the firmware workstream on day 1 |

### Open decisions needing a human

- **D1. Board:** Teensy 4.0 with an isolated UART (recommended), or Teensy 4.1 with Ethernet to the orchestrator?
- **D2. Headline poll rate:** 1 kHz (what the spec says; typical keyboard) or 8 kHz (lower noise)? Suggestion: 1 kHz as the headline, with an 8 kHz variant on R1 and R5 to quantify the difference. Either way, log the transfer-complete time.
- **D3. Endpoint threshold and direction:** what % of the black↔white span counts as "first photon change", and do we report black→white and white→black separately? (Recommended: yes, both.)
- **D4. Marker placement:** which absolute screen position (top-left recommended, to minimize scan-out delay), applied to all rungs and both floor apps? This touches the spec itself.
- **D5. Camera:** approve a used RX100 V/VA (~$350), rent a Chronos for the audits, or both?
- **D6. Tooling:** is a 3D printer available? An oscilloscope? A scope makes bring-up much easier. Without one, the Teensy streams raw ADC as its own scope, adding about +0.5 day.
- **D7. Rig count:** one rig, or two so the Mac and Windows sessions can run in parallel (+~$35)?
- **D8. The spec's "full brightness so backlight flicker does not trigger the sensor" is false for the MacBook Pro.** Accept the firmware filtering approach, and update the spec wording?

---

## 7. Effort estimate (hardware only; one person who owns the rig)

| Task | Person-days |
|---|---|
| Finalize BOM, place orders (DK/M, PJRC, AF, AMZ, camera) | 0.5 |
| CAD for the sensor head, calibration puck and enclosure; 2–3 print iterations | 1.0 |
| Assembly: 2 sensor heads (TIA with gain switch), main perfboard (Teensy, isolator, CP2102N, jacks, LEDs), cabling and strain relief | 1.0 |
| Bench bring-up, analog: gain selection and headroom on each of the 4 displays, noise floor, a PWM/flicker characterization per display, sensor vs OPT101 cross-check | 1.5 |
| Bench bring-up, timing: calibration-LED rig latency (target <100 µs, stable to within µs), USB enumeration and **measured poll rate** on Windows, Linux and macOS, sync LED | 1.0 |
| Camera setup and first photodiode-vs-camera agreement check (≥20 trials) | 0.5–1.0 |
| Rig build documentation for replication (photos, pin map, BOM) | 0.5 |
| **Total** | **≈ 6–6.5 person-days**, about 2 calendar weeks including shipping |

Contingency is +1–2 days, if a PCB respin, gain rework or mount redesign is needed for the matte monitor.

The two calibration days in the Phase 0 checklist are separate from this estimate. They depend on firmware and the orchestrator.

---

## Sources

- PJRC Teensy 4.0: https://www.pjrc.com/store/teensy40.html · pricing: https://www.microcenter.com/brand/4294818527/pjrccom
- Teensy 8 kHz HID: https://forum.pjrc.com/index.php?threads/usb-2-0-teensy-port-at-8000-hz-poll-rate.63948/ · https://github.com/Trip93/teensy4_mouse
- Teensy ADC rates: https://forum.pjrc.com/index.php?threads/adc-library-with-support-for-teensy-4-3-x-and-lc.25532/page-10
- RP2350 datasheet: https://pip.raspberrypi.com/documents/RP-008373-DS-rp2350-datasheet.pdf · Pico 2: https://www.adafruit.com/product/6006
- OPT101 datasheet: https://www.ti.com/lit/ds/symlink/opt101.pdf · module: https://www.amazon.com/OPT101-Intensity-Monolithic-Photodiode-CJMCU101/dp/B0B2CWRG98
- NVIDIA LDAT: https://www.nvidia.com/en-us/geforce/news/nvidia-reviewer-toolkit/ · https://hothardware.com/reviews/nvidia-reviewer-toolkit-explained · https://www.igorslab.de/en/nvidia-ldat-latency-display-analysis-tool-presented-and-tested/
- OSRTT: https://github.com/andymanic/OSRTT · https://andymanic.github.io/OSRTTDocs/docs/input-lag/measurements/
- Open-Source-LDAT: https://github.com/S4N-T0S/Open-Source-LDAT
- click2photon / FrameProbe: https://github.com/marconett/click2photon
- OpenLDAT: https://github.com/adolfintel/OpenLDAT · https://sid.onlinelibrary.wiley.com/doi/10.1002/jsid.1104
- sqwk arduino-latency-test: https://github.com/sqwk/arduino-latency-test
- Dan Luu: https://danluu.com/input-lag/
- Typometer: https://github.com/pavelfatin/typometer · https://github.com/pavelfatin/typometer/issues/12
- Is It Snappy: https://isitsnappy.com/ · https://chadaustin.me/2017/04/announcing-is-it-snappy/
- piLagTester: https://alantechreview.blogspot.com/2020/05/input-lag-measurement-using-slow-high.html
- MacBook Pro PWM: https://www.notebookcheck.net/Apple-MacBook-Pro-14-2024-review-The-M4-Pro-and-matte-display-are-massive-upgrades.914597.0.html · https://ledstrain.org/d/3412-macbook-pro-m4-pwm
- OLED PWM: https://forums.blurbusters.com/viewtopic.php?t=12196
- Samsung 960 fps interpolation: https://www.androidauthority.com/samsung-galaxy-s22-ultra-960fps-3139987/ · https://www.androidcentral.com/galaxy-s20-ultra-cannot-capture-true-960fps-super-slow-mo-video
- RX100 HFR: https://helpguide.sony.net/dsc/1810/v1/en/contents/TP0001138299.html · https://www.dpreview.com/forums/thread/4092436 · used price: https://www.thephoblographer.com/2026/09/14/the-sony-rx100v-is-10-years-old-is-it-good-in-2026/ · https://www.mpb.com/en-us/product/sony-cyber-shot-rx100-mark-v
- Chronos: https://www.newsshooter.com/2021/01/09/krontech-chronos-2-1-hd-1000fps-for-4995-usd/ · https://www.atecorp.com/products/kron-technologies-inc/chronos-1-4

Caveats: the part prices are recent retail figures and were not checked against live distributor stock today. The BPW34 current-per-lux figure under display-white light is an engineering estimate, and bench bring-up must confirm it.
