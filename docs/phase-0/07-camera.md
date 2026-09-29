# Phase 0 scope, workstream 07: validation camera (modules instead of a retail camera)

Scope: can we replace the "1,000 fps validation camera" in [01-hardware.md §5](01-hardware.md) (a used Sony RX100 V or a rented Chronos) with a **camera module**, meaning a machine-vision board or cube camera, or a Raspberry Pi CSI module, driven from the rig? The spec gives the camera three jobs:

- **(a)** Spot-check the photodiode latency.
- **(b)** Check **marker honesty**: the 64 px marker and the list update land in the same display frame.
- **(c)** Check **tearing**.

Researched Sep 2026. Prices are USD before tax and shipping unless marked. Figures marked **[est]** are my calculations from cited data, and figures marked **[verify]** need a check on hardware or with the vendor before we buy.

---

## TL;DR

- **Yes, modules work, and they beat the RX100 for our jobs.** A global-shutter USB3 machine-vision camera with the **Sony IMX287** sensor (720×540, 6.9 µm pixels) runs 520–540 fps at full frame. Its frame rate rises roughly in inverse proportion to ROI height: The Imaging Source publishes **1,923 fps at 640×120 and 4,659 fps at 640×24** for this sensor ([argocorp/TIS](https://www.argocorp.com/cam/usb3/tis/DxK37UX287.html)). A 720×64 strip should run at about **2,900 fps [est]**. Unlike the RX100, these cameras have a **hardware trigger input and an exposure-active output**, so every frame can be timestamped on the Teensy's µs clock. They also record continuously instead of in 2–6 s clips.
- **Budget pick: Daheng MER2-041-528U3M** (mono, IMX287; **not** the "-L" variant, which has no I/O). About **€218** ([VA Imaging](https://va-imaging.com/products/usb-camera-3-0-0-4mp-monochrome-sony-imx287-mer2-041-525u3m)). With an 8 mm C-mount lens, cables and an arm, the BOM is **≈ $370**.
- **Better pick: Basler ace U acA720-520um** (same sensor). About **$436** ([Edmund Optics listing via search](https://www.edmundoptics.com/p/basler-ace-aca720-520um-usb-30-monochrome-camera/40357/)). Its GPIO is documented as 3.3 V-compatible, and it has official pip-installable `pypylon` wheels for Linux aarch64 (Pi 5). With a Computar lens, locking cables and a Manfrotto-class arm, the BOM is **≈ $800**. **The Imaging Source DMK 37BUX287** ($436, [RMA](https://www.rmaelectronics.com/the-imaging-source-tis-dmk-37bux287/)) is an equal alternative and has the best-documented ROI frame rates.
- **Near-free stopgap: Raspberry Pi Global Shutter Camera** (IMX296, $50). It tops out at **~536 fps** on a 96-row crop ([Hermann-SW GScrop](https://gist.github.com/Hermann-SW/e6049fe1a24fc2b5a53c654e0e9f6b9c)). That is enough to tell "same refresh" from "next refresh" at 120 Hz, but it misses the spec's ~1,000 fps and is marginal at 240 Hz.
- **A second photodiode on the first list row** can check marker honesty on **every trial**, for about $10. The camera is still needed for the other jobs: tearing, whole-list atomicity, validating that second diode, spot-checking diode latency, measuring the panel's scanout, and publishable video evidence. Do both.

---

## 1. Why a module fits this job better than a consumer camera

| Need | RX100 V / phone | Machine-vision module |
|---|---|---|
| Frames on the rig clock | No. Only visual sync via the sync LED. | **Yes.** The Teensy drives the trigger input or reads the exposure-active output (§5). |
| Shutter | Rolling. Skew must be modelled for tearing. | **Global.** Every pixel shares one exposure window. |
| Record length | 2–6 s clips, then a slow buffer flush ([Sony help guide](https://helpguide.sony.net/dsc/1920/v1/en/contents/TP0001138299.html)) | Continuous to disk while bandwidth allows (~130–200 MB/s here). Can run the whole 500-trial session. |
| fps | 960–1,000 fps at 1244×420 (upscaled from skipped pixels) | 540 fps full frame; **~1,900–4,600 fps on narrow strips** |
| Automation | Manual | Python: GenICam, Aravis or the vendor SDK. Frames go straight into the analysis pipeline. |
| Cost | $325–450 used | $240–440 new, plus lens |

The limitation: a module has **no screen, battery or lens**. It needs a host (the Pi 5 orchestrator, or any Linux or Windows PC) and a C-mount lens.

---

## 2. Machine-vision USB3 cameras: comparison

All cameras below are global shutter, C-mount and USB3 Vision/GenICam, so **Aravis** (open-source, arm64, Python via PyGObject) or **Harvesters** plus the vendor's GenTL `.cti` should drive any of them ([Aravis](https://github.com/AravisProject/aravis)).

### 2.1 The frame-rate-versus-ROI model (IMX287)

The IMX287's frame period is set by row count, not width. Fitting The Imaging Source's published table ([source](https://www.argocorp.com/cam/usb3/tis/DxK37UX287.html): 640×240 → 1,111 fps, 640×120 → 1,923, 640×24 → 4,659, 720×540 → 540) gives:

> **T_frame ≈ 140 µs + 3.17 µs × rows** [est; reproduces all four published points within 1%]

| ROI height (rows) | Frame period | fps [est] | Data rate at 720 px wide, Mono8 |
|---|---|---|---|
| 540 (full) | 1.85 ms | 540 | 210 MB/s |
| 240 | 0.90 ms | 1,110 | 192 MB/s |
| 120 | 0.52 ms | 1,920 | 166 MB/s |
| 96 | 0.44 ms | 2,250 | 156 MB/s |
| **64** | **0.34 ms** | **~2,900** | 134 MB/s |
| 32 | 0.24 ms | ~4,150 | 96 MB/s |

Teledyne FLIR's official table for the same sensor agrees: **997 fps at 320×240** in Mono8 ([BFS-U3-04S2 spec](https://softwareservices.flir.com/BFS-U3-04S2/latest/Model/spec.html)). Each vendor's FPGA adds a little overhead, so confirm the exact numbers per model with the vendor's calculator: [Basler Frame Rate Calculator](https://www.baslerweb.com/en/tools/frame-rate-calculator/) or Daheng's Excel tool in the MER2 manual §9.4 **[verify]**. USB3 Gen 1 carries about 350–400 MB/s in practice, so bandwidth never binds; the sensor's row time does. Use **Mono8** because 10/12-bit modes lower the fps (FLIR table: 437 fps at 10-bit).

### 2.2 Shortlist

| Model | Sensor | Full-frame fps | Strip fps (720×64) | Trigger in / strobe out: 3.3 V Teensy? | Linux arm64 / Python | Price new | Notes |
|---|---|---|---|---|---|---|---|
| **Daheng MER2-041-528U3M** | IMX287 | 528 ([VA](https://va-imaging.com/products/usb-camera-3-0-0-4mp-monochrome-sony-imx287-mer2-041-525u3m)) | ~2,900 [est] | Opto input Line0 needs **5–24 V at ≥7 mA**, so a Teensy can't drive it directly. **Non-isolated GPIO Line2/3 input: logic 1 ≥ 1.9 V, <100 µA**, so the Teensy can drive it directly. Line2/3 outputs are open-collector, specified for 5–24 V pull-ups, with <20 µs delay ([MER2 manual v2.0.6, §7.3](https://en.daheng-imaging.com/index.php?a=file_down&c=index&f=%2Fuploadfile%2F2024%2F0108%2F20240108091546758.pdf&m=content)). A 3.3 V pull-up probably works but is out of spec **[verify]**. Offers ExposureActive, trigger delay and timestamp. | Galaxy SDK for ARMv8 plus `gxipy` ([VA ARM guide](https://va-imaging.com/en-us/blogs/machine-vision-knowledge-center/how-to-install-raspberry-pi-with-daheng-imaging-usb3-machine-vision-camera)); Aravis | **€218** | "UltraShort" exposure mode 1–100 µs. The **-L variant has no I/O**, so avoid it. The MER2-041-608U3M-HS (608 fps, €232) is a faster sibling. |
| **Basler ace U acA720-520um** | IMX287 | 525 ([docs](https://docs.baslerweb.com/aca720-520um)) | ~2,900 [est; verify in calculator] | **GPIO input: logic 1 > 2.0 V, <100 µA, 0–24 V safe**, so the Teensy can drive it directly. **GPIO output: open collector, ≈2 kΩ, 3.3–24 V**, so pull it up to the Teensy's 3.3 V (in spec). Opto input needs > 2.2 V at ≥5 mA. Offers ExposureActive, FrameTriggerWait and chunk timestamps. | pylon for Linux aarch64; **`pypylon` wheels for aarch64** ([pypylon](https://github.com/basler/pypylon)); Aravis | **$436** (Edmund) | Draws ~3.0 W from USB (§2.4). eBay "new" grey-market listings run $1,490–2,700, so buy from a distributor. |
| **TIS DMK 37BUX287** | IMX287 | 539 | **documented**: 1,923 at 640×120, 4,659 at 640×24 | Trigger input and strobe output on the "B" (I/O) model | `tiscamera` for Ubuntu x64 and **ARM64**, IC4 SDK, GStreamer ([TIS](https://www.theimagingsource.com/products/industrial-cameras/usb-3.1-monochrome/dmk37bux287/)) | **$436** ([RMA](https://www.rmaelectronics.com/the-imaging-source-tis-dmk-37bux287/)) | Exposure from 1 µs. USB-powered at 380 mA, the lowest draw here. Get the **B**UX model; the AUX model has no I/O. I/O electrical levels **[verify in TRM]**. |
| Teledyne FLIR Blackfly S BFS-U3-04S2M | IMX287 | 522 | ~2,900 [est]; 997 at 320×240 official | Opto input plus non-isolated GPIO **[verify levels]** | Spinnaker and PySpin for arm64 **[verify current build]** | ~$361 (B&H) | 240 MB frame buffer smooths USB hiccups. |
| Hikrobot MV-CA004-10UM | IMX287 | 526.5 ([Hoplong](https://hoplongtech.com/products/mv-ca004-10uc)) | ~2,900 [est] | Opto input and GPIO **[verify]** | MVS SDK for Linux, Python `MvImport` ([Hikrobot](https://en.hikrobotics.com/service/soft.htm)) | ~$410 | Viable. Fewer English docs. |
| Basler acA640-750um | onsemi PYTHON 300 (4.8 µm) | 751 (fast readout) ([docs](https://docs.baslerweb.com/aca640-750um)) | Higher than IMX287 at small ROI; PYTHON supports up to 4 ROIs **[verify fps in calculator]** | Same GPIO as above: 3.3 V OK | pypylon aarch64 | ~$500 | Older line. Smaller pixels mean ~2× less light per pixel than the IMX287. |
| Allied Vision Alvium 1800 U-040m | IMX287 | **~281** on USB ([1stVision](https://www.1stvision.com/cameras/models/Allied-Vision/Alvium%201800%20U-040)) | Lower | 4 TTL GPIO | Vimba X arm64 | — | **Reject**: throttled to about half the sensor's speed. |
| IMX273 / IMX296 / IMX426 USB cameras | 1.6 MP / 0.5 MP | 160–280 | < 1,500 [est] | — | — | — | More pixels, slower rows. No benefit for a 64–200 px target. |

IDS uEye and Arducam USB3 do not currently offer an IMX287- or PYTHON 300-class camera worth adding. Arducam's USB3 global-shutter modules (OV9281 and similar) top out around 100–200 fps.

**Used market:** eBay listings for these models were mostly grey-market "brand new" units at or above the distributor price (acA640-750um from ~$498, acA720-520um from $1,490). Used Blackfly S units exist, but in other resolutions. New from a distributor is the sensible route at $220–440.

### 2.3 Why IMX287 rather than a higher-resolution sensor

- **Pixels:** 6.9 µm pixels collect **4× the light** of the Pi GS camera's 3.45 µm pixels (§4).
- **Resolution:** 720 px across is plenty. The strip across the marker and first list row (~150 mm on the 14" panel) comes out at ~2 screen px per camera px, so the 64 px marker covers ~32 camera px.
- **Speed:** its row time (~3.2 µs) is what makes 2–4 kfps strips possible.

### 2.4 Running it on a Raspberry Pi 5

- **Power:** a Pi 5 limits total USB current to **600 mA** unless it runs on the official 27 W (5 V/5 A) supply. The Basler draws ≈3.0 W typical and 3.2 W max, which is 600–640 mA and over the limit. **Use the 27 W PSU** (then 1.6 A is available) or a powered USB3 hub. The TIS (380 mA) and Daheng (<2.7 W) have more margin.
- **usbfs buffer:** raise it (`usbcore.usbfs_memory_mb=1000` on the kernel cmdline). Vendor SDKs and Aravis recommend this to avoid dropped frames at high rates.
- **Disk:** 134–210 MB/s to disk is too fast for the SD card. Record short bursts to RAM (8 GB ≈ 60 s at 134 MB/s), or write to an NVMe HAT. For spot checks, keep only frames in a window around each trial.
- **Alternative host:** any x86 Linux or Windows PC works if the Pi misbehaves.

---

## 3. Raspberry Pi CSI route

| Module | Shutter | Documented max fps | External trigger | Cost |
|---|---|---|---|---|
| **Raspberry Pi Global Shutter Camera** (IMX296, 1456×1088, 3.45 µm, C/CS mount) | Global | 60 fps full frame. With **Hermann-SW's `GScrop`** (media-ctl crop): **536 fps at 1456×96 (full width)**, 400 fps at 688×136, Pi 5 supported, µs timestamps in the MP4 ([gist](https://gist.github.com/Hermann-SW/e6049fe1a24fc2b5a53c654e0e9f6b9c), [forum](https://forums.raspberrypi.com/viewtopic.php?t=348642)). 536 fps is the ceiling; narrowing further does not help. | **XTR pad**: a **1.8 V** input, so a 3.3 V Teensy needs a divider or level shifter. Boards with Q2 fitted need **R11 removed**. Enable with `echo 1 > /sys/module/imx296/parameters/trigger_mode`. Exposure = low-pulse width + 14.26 µs ([Pi docs](https://www.raspberrypi.com/documentation/accessories/camera.html)). Users report long delivery latency in trigger mode ([forum](https://forums.raspberrypi.com/viewtopic.php?t=371940)), which doesn't matter if the Teensy logs the edge. Max triggered rate with a crop **[verify]**. | **$50** ([Pi](https://www.raspberrypi.com/products/raspberry-pi-global-shutter-camera/)), plus a Pi 5 22-to-15-pin cable (~$1–3) |
| InnoMaker, Arducam or Waveshare IMX296 clones | Global | Same sensor, same limits | Trigger broken out on some ([InnoMaker](https://www.amazon.com/innomaker-Global-Shutter-Camera-Raspberry/dp/B093BY2TK2)) | $50–80 |
| Pi Camera v1/v2 with Hermann-SW's `raspiraw` | **Rolling** | 640×64 at 665 fps (v1), **640×75 at 1,007 fps (v2, IMX219)** ([fork-raspiraw](https://github.com/Hermann-SW/fork-raspiraw)) | None | $15–25 |

**Pi 5 caveats**

- `raspiraw` depends on the legacy firmware camera stack and **does not run on the Pi 5**. Only `GScrop` (libcamera) is Pi 5-ready.
- The v2 camera's rolling shutter also defeats the tearing check.
- `SensorTimestamp` is the kernel time at which the CSI receiver saw frame start. It is not the exposure time ([picamera2 #821](https://github.com/raspberrypi/picamera2/issues/821), [forum](https://forums.raspberrypi.com/viewtopic.php?t=355707)). It is on the Pi's clock, not the Teensy's, and two-camera users report 8–10 ms of unexplained skew ([picamera2 #1107](https://github.com/raspberrypi/picamera2/issues/1107)). **Do not trust it for µs timing.** Use the XTR trigger from the Teensy, or the sync LED in frame (§5).

**Verdict:** at 536 fps the sample period is 1.87 ms, against 8.3 ms per refresh at 120 Hz. That resolves "same refresh versus next refresh" for marker honesty (expected offset ~0.7 ms versus ≥8.3 ms if dishonest). At 240 Hz (4.2 ms) it is marginal, and it gives only ~4 samples per refresh for tearing. It is a good **$80 bring-up and stopgap camera**, not the spec's 1,000 fps instrument.

---

## 4. Optics and light

### 4.1 Geometry of the 14" 2.8K panel

- The active area is about 302 × 189 mm (2880×1800, 16:10), so one screen pixel ≈ **0.105 mm**. The 64 px marker is ≈ 6.7 mm, and a 200 px region is ≈ 21 mm.
- The marker sits at top-left (x = 16, y = 160 device px, per [04-calibration §4](04-calibration.md)). The palette list is centred, so a **horizontal strip from the marker to the first list row spans ~1,450 px ≈ 150 mm** and ~100–150 px ≈ 10–16 mm tall.

The thin-lens approximation gives f ≈ WD × m / (1 + m), with magnification m = sensor width / field width. The IMX287 sensor is 4.97 × 3.73 mm; the IMX296 is 5.0 × 3.8 mm.

| View | Field width | Working distance | Focal length (IMX287 or IMX296) |
|---|---|---|---|
| Close-up: marker + sync LED + first row (≈ 60 × 45 mm) | 60 mm | 15–20 cm | **12–16 mm** |
| Strip: marker → first list row (≈ 160 mm wide), plus sync LED | 160 mm | 25–30 cm | **8 mm** |
| Full screen for tearing (310 mm wide) | 310 mm | ~50 cm with 8 mm, ~75 cm with 12 mm | 8 mm or 12 mm |

- **One 8 mm C-mount lens covers the strip and full-screen views** by moving the camera. Add a 12–16 mm lens for close-ups.
- **Minimum focus distance:** for example, Computar's 12 mm M1214-MP2 is spec'd at a 150 mm working distance ([Basler shop](https://www.baslerweb.com/en/shop/computar-lens-m1214-mp2-f1-4-f12mm-2-3/)). To focus closer, add a **0.5–5 mm C-mount extension ring**. A C-to-CS adapter is itself a 5 mm ring. **CS-mount lenses (e.g. the Pi 6 mm) do not fit C-mount cameras**; C-mount lenses do fit CS cameras with the adapter.
- **Lens prices:**
  - Computar M1214-MP2 12 mm f/1.4: **~$142 new, ~$90 used** ([B&H](https://www.bhphotovideo.com/c/product/888992-REG/computar_M1214_MP2_2_3_Fixed_Lens.html), [PicClick](https://picclick.com/Computar-M1214-MP2-C-Mount-12mm-Fixed-Focal-Lens-395971003511.html)).
  - Computar 8 mm (M0814-MP2): ~$150 **[verify]**.
  - Generic or Arducam 8–16 mm C-mount lenses: **$25–50** ([Arducam](https://www.arducam.com/c-mount-lens-for-raspberry-pi-high-quality-camera-16mm-focal-length-with-manual-focus-and-aperture-adjustment.html), [Pi 16 mm C / 6 mm CS](https://www.raspberrypi.com/documentation/accessories/camera.html)).
  - A 1/2.9" sensor uses only the centre of a 1/2" or 2/3" lens, so cheap lenses are sharp enough to resolve a 64 px black/white square.

**Camera tilt trick for tearing at high fps.** Rotate the camera 90° so that the sensor's 720-px rows run **down the screen**. A 64-row ROI then images a **full-height, ~18 mm-wide vertical strip** at ~2,900 fps, with 8 mm at ~50 cm. That is ~24 samples per 120 Hz refresh, or ~12 at 240 Hz, over every scan line.

### 4.2 Light budget at ≤ 1 ms exposure [est]

Sensor illuminance from an extended source is E ≈ π·L·T / (4·N²), independent of distance.

For white at L = 400 cd/m² (typical SDR full white on a 2.8K OLED; the Vivobook S14X peaks at 550 nits, per [Notebookcheck](https://www.notebookcheck.com/Das-Asus-Vivobook-S14X-OLED-ist-das-weltweit-erste-14-5-Zoll-Notebook-mit-2-8K-120-Hz-OLED.617680.0.html)), with T ≈ 0.9 and f/1.4, E ≈ 140 lux on the sensor.

| Camera (pixel) | f-number | Exposure | Signal [est] | Shot-noise SNR |
|---|---|---|---|---|
| IMX287 (6.9 µm, 47.6 µm², QE ≈ 0.65) | f/1.4 | 300 µs (fits a 2.9 kfps strip) | ≈ 5,500 e⁻ (~25% of a ~20 ke⁻ full well) | ~75:1 |
| IMX287 | f/2.8 | 300 µs | ≈ 1,400 e⁻ | ~37:1 |
| IMX296 (3.45 µm, 11.9 µm²) | f/1.4 | 1 ms (536 fps) | ≈ 4,500 e⁻ | ~67:1 |

(1 lux ≈ 4,100 photons/µm²/s at 555 nm. White light gives somewhat more photons per lux, so these figures are conservative.)

- **Brightness is sufficient.** The marker is a black/white step on a self-emissive panel whose black is ~0, so even SNR 20 gives an unambiguous edge. Run at 0 dB gain, stop down to f/2–2.8 for depth of field (the lens is not square to a glossy screen), and add gain only if needed. Each +6 dB of gain doubles read noise relative to signal, but we are shot-noise limited at these levels.
- **Watch these panel artifacts; the camera will show them:**
  - **OLED PWM or refresh-synchronous dimming**, as a brightness ripple at the refresh rate or at 240–480 Hz at lower brightness. The spec's full brightness minimises it, but OLEDs can still show a dark band once per frame ([Notebookcheck PWM ranking](https://www.notebookcheck.net/PWM-Ranking-Notebooks-Smartphones-and-Tablets-with-PWM.163979.0.html), [OLED-Info](https://www.oled-info.com/pulse-width-modulation-pwm-oled-displays)).
  - **LCD backlight strobing** on the 240 Hz monitor (ULMB/MPRT modes). Turn it off.
  - **LCD pixel response** of 1–5 ms, which smears one transition over several camera frames.
  - **Mitigation:** threshold at 50% of the local black→white step, and use a static reference patch in the same ROI to normalise ripple.

---

## 5. Putting camera frames on the rig clock

Ranked by preference.

1. **The Teensy triggers each frame (hardware trigger, `TriggerMode=On`, `TriggerSource=Line3`/GPIO).**
   - The Teensy emits trigger pulses (e.g. 2–2.5 kHz for a 64–96-row strip) from a timer, so every edge is known on its µs clock.
   - Exposure starts at edge + a fixed, small latency (a few µs). Measure that latency once by imaging the calibration LED.
   - Frame N maps to edge N. Dropped frames show up as `frames received < edges sent` and gaps in the camera's FrameID/chunk counter.
   - A trigger-to-trigger period shorter than exposure plus overhead makes the camera ignore edges. Check with `FrameTriggerWait`.
2. **Free-run the camera and feed ExposureActive (or strobe) into a Teensy input-capture pin.**
   - The camera runs at its maximum ROI rate. The Teensy timestamps every exposure start and end edge, which is the true exposure window of each frame, and counts them.
   - Align the first frame by resetting the camera's frame counter at acquisition start and matching the counts.
   - Simplest to set up, and just as accurate.
3. **Do 1 and 2 together:** trigger, and read ExposureActive back. This proves each trigger produced an exposure, and it measures trigger latency on every frame. Recommended once the rig is stable.
4. **Sync LED in frame (camera-agnostic; already in [01-hardware §3](01-hardware.md)).**
   - The Teensy lights it on USB transfer-complete, and analysis reads the latency as (sync-LED frame → marker frame).
   - This works for the RX100, phones and the Pi GS camera, and it is the independent cross-check for options 1–3.
   - Better: a **binary LED bar** (e.g. 8 LEDs showing a ms counter) makes every frame self-timestamping.

**Electrical notes**

- Teensy 4.x pins are **3.3 V and not 5 V tolerant**. Never pull a camera output up to 5 V.
- Basler GPIO in and out and Daheng GPIO in are fine at 3.3 V (§2.2). Opto inputs need ≥5 mA (Basler) or 5 V at 7 mA (Daheng), so drive those through a transistor.
- **Ground loop:** the camera's ground is the orchestrator's ground via USB, and the Teensy's ground is the machine under test's. Wiring camera GPIO straight to the Teensy **bypasses the ADuM1201 isolation** from [01-hardware §3](01-hardware.md). Route trigger and ExposureActive through a **second ADuM1201BRZ** (one channel each way, ~$5). Power its camera side from the Pi's 3.3 V header, which shares the camera's ground.
- For the Pi GS XTR pad (1.8 V input): put the isolator output through a 1 kΩ/1.2 kΩ divider, or use a 1.8 V-supplied isolator side.

---

## 6. Dedicated high-speed cameras, for comparison

| Option | Real capability | Price | Verdict |
|---|---|---|---|
| **Kron Chronos 1.4** | Global shutter, ~1,000 fps at 1280×1024, far higher at reduced height, long records, trigger and sync I/O ([Krontech](https://www.krontech.ca/product/chronos-1-4-high-speed-camera/)) | ~$2.5–3.5k new ([01-hardware](01-hardware.md)); **rental** ([ATEC](https://www.atecorp.com/products/kron-technologies-inc/chronos-1-4)) | Excellent, but ~8× the module's cost for our 64–200 px targets |
| **Kron Chronos 2.1-HD** | 1080p at ~1,000 fps | **$3,400–3,800 used** ([KronTalk forum](https://forum.krontech.ca/threads/chronos-2-1-hd-cameras-for-sale-8-16-and-32-gb.20590/)); ~$5k new | Overkill |
| **Sony RX100 V/VI/VII** | 960–1,000 fps at 1244×420, from skipped and upscaled pixels ([DPReview](https://www.dpreview.com/forums/post/60743855)), for 2–6 s clips | $325–450 used | **Rolling shutter**: readout skew must be modelled for the tearing check, and there is no rig-clock sync except the sync LED. The module is cheaper and better. |
| iPhone slo-mo | Real **240 fps** (4.2 ms) | $0 | Bring-up sanity only |
| Samsung "960 fps" | Captured at 240/480 fps and **interpolated** ([Android Authority](https://www.androidauthority.com/samsung-galaxy-s22-ultra-960fps-3139987/)) | — | **Reject**: synthesised frames can fake exactly the intermediate states we are testing for |

---

## 7. Can a second photodiode replace the camera for marker honesty?

**On every trial, mostly yes.** Put sensor head 2 (already budgeted in [01-hardware](01-hardware.md)) over the **first list row**, and drive the scenario script so the first row's content changes with every query change. For example, choose query sequences where the top-ranked item changes, or aim at a high-contrast region such as the row highlight. The Teensy's second ADC channel then gives t_row alongside t_marker on the same clock.

- **Honesty criterion:**
  - The expected lag is **Δt ≈ (y_row − y_marker) / H × T_refresh**, i.e. the scanout offset. That is ≈ 0.65 ms at 120 Hz for a ~140 px row offset on this panel.
  - An honest rung gives Δt ≈ that offset, ± panel response.
  - A rung that flips the marker early shows Δt ≥ one refresh (≥ 8.3 ms at 120 Hz, ≥ 4.2 ms at 240 Hz).
  - The gap between the two cases is large, so a threshold at T_refresh/2 works trial by trial.
- **Cost:** ~$10 for a head. It audits all 500+ trials per rung, not a sampled few.

**What the camera is still needed for**

1. **Tearing** (spec parity row). A diode sees one spot, and a tear is a spatial discontinuity.
2. **Whole-list atomicity.** The diode proves row 1 changed with the marker, but not that rows 2…n did. A progressive or split render could pass the diode.
3. **Validating diode 2 itself.** Check that its threshold crossing is the text or highlight change, not anti-aliasing flicker, a hover effect or cursor blink. Check how glyph-level changes (low contrast, small area) register.
4. **Spot-checking diode latency** against an independent sensor, via the sync LED and ExposureActive (spec job a).
5. **Measuring the panel's scanout** (direction, duration, any pulsed or banded refresh). This feeds `scanout_row_fraction` in `marker.json` and the 240 Hz monitor's model. OLED scanout may not take exactly one refresh period.
6. **Scenario C** (scroll): dropped or late frames, as the spec lists.
7. **Publishable evidence:** video that a skeptic can scrub.

**Recommendation:** diode 2 for honesty on every trial, and the camera **on every accepted change** (as the spec's agent loop already says) plus once per machine and refresh-rate setup.

---

## 8. Recommendations and BOMs

### Budget pick: Daheng MER2-041-528U3M (≈ $370)

| # | Part | ~Price | Source |
|---|---|---|---|
| 1 | Daheng **MER2-041-528U3M** (mono IMX287, 528 fps; **not the -L**) | €218 ≈ $245 | [VA Imaging](https://va-imaging.com/products/usb-camera-3-0-0-4mp-monochrome-sony-imx287-mer2-041-525u3m) |
| 2 | 8 mm C-mount lens, 1/2" or larger (generic or Arducam), plus a 5 mm C-CS ring for close focus | $30–45 | Arducam, Amazon |
| 3 | USB3 Micro-B (screw-lock) to A cable, 1–2 m | $15–20 | Amazon |
| 4 | Hirose HR25 8-pin I/O pigtail | $20–30 **[verify part]** | Daheng distributor |
| 5 | Articulating arm and clamp with 1/4-20 screw, and a small tripod | $25–35 | Amazon |
| 6 | Second ADuM1201BRZ plus passives (isolated trigger and ExposureActive) | $5 | DK/M |
| | **Total** | **≈ $340–380** | |

Caveats:
- ROI fps is estimated from the sensor, not a Daheng table. Run Daheng's calculator or ask VA Imaging for the 720×64 and 720×96 fps before ordering.
- The opto input needs 5 V, so use GPIO Line2/3.
- The US site shows "contact for price".

### Better pick: Basler ace U acA720-520um (≈ $800)

| # | Part | ~Price | Source |
|---|---|---|---|
| 1 | Basler **acA720-520um** (mono IMX287, 525 fps, 3.3 V-compatible GPIO, `pypylon` aarch64) | $436 | [Edmund Optics](https://www.edmundoptics.com/p/basler-ace-aca720-520um-usb-30-monochrome-camera/40357/) |
| 2 | Computar M1214-MP2 12 mm f/1.4 (close-ups and full screen at ~75 cm), plus an 8 mm lens for strip views | $142 + $30–150 | [B&H](https://www.bhphotovideo.com/c/product/888992-REG/computar_M1214_MP2_2_3_Fixed_Lens.html) |
| 3 | Basler USB 3.0 Micro-B screw-lock cable, 3 m | $40–55 **[verify]** | Basler |
| 4 | Hirose HR10A-7P-6S 6-pin I/O cable, open end | $20–50 | [eBay generic](https://www.ebay.com/itm/203137185875) / Basler |
| 5 | Manfrotto-class micro arm and super clamp, or a copy stand | $40–80 | |
| 6 | Second ADuM1201BRZ plus passives | $5 | DK/M |
| 7 | Pi 5 27 W PSU, if the Pi 5 hosts the camera (USB current limit, §2.4) | $12 | Pi resellers |
| | **Total** | **≈ $730–930** (≈ $800 typical) | |

**Equal alternative:** TIS DMK **37BUX287** ($436, documented 1.9–4.7 kfps ROI table, 1 µs exposure, 380 mA). Pick it if the published ROI table matters more than pypylon's convenience.

### Stopgap: Pi Global Shutter Camera (≈ $80)

Pi GS camera ($50), Pi 6 mm CS lens (~$25), and a Pi 5 camera cable. 536 fps at 1456×96 with `GScrop`; trigger via XTR at 1.8 V. Use it now for bring-up and 120 Hz honesty checks while the machine-vision camera ships.

---

## 9. Open items [verify]

- Actual fps at 720×64 and 720×96 on the chosen model, including the minimum ROI height and step, and whether exposure overlaps readout at that rate.
- Daheng Line2/3 output with a 3.3 V pull-up; TIS I/O levels.
- Sustained 134–210 MB/s capture on a Pi 5 with the vendor SDK or Aravis (buffer count, usbfs, RAM or NVMe). Fall back to an x86 host if frames drop.
- Pi GS triggered (XTR) maximum rate with a crop.
- Measure trigger → exposure latency once with the calibration LED in frame.
