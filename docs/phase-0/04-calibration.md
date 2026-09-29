# Phase 0 scope, workstream 04: calibration targets and procedure

Scope: (a) the **display-floor app**, (b) the **browser-floor page**, (c) the **marker placement contract** that every rung shares, and (d) the **calibration procedure** (rig LED loopback, then display floor, then browser floor, on two days).

Written from the spec (Calibration, Latency marker, Test environment, ladder R5/R6, Phase 0 checklist) plus platform knowledge. Items marked **[verify]** are API or driver facts to confirm against current docs or on hardware before building. I did not re-check them live.

---

## 0. Summary of recommendations

1. **Write small raw-API floor programs, one per platform, in a single Rust workspace.** Do not build on winit + wgpu. The floor is a reference instrument, so every layer between key and flip should be visible and controllable. Add a separate **winit + wgpu "empty R5"** diagnostic for about a day of work. It measures what the R5 toolkit costs before any UI exists.
2. **Report three floors per machine and refresh rate, and name them clearly:**
   - **Composed floor:** windowed, the OS compositor in the path, vsync. This is the natural comparison for R5 and the browser rungs.
   - **Fair floor:** compositor bypassed (iFlip on Windows, direct scanout on KMS and Wayland, direct-to-display on macOS where available), tear-free. This is the floor for parity-passing rungs, which includes R6.
   - **True floor:** compositor bypassed with tearing allowed. This is the absolute physical floor and the comparator for R6's tearing variant.
   The spec's rule "no rung can beat this number" holds against the **true** floor for all rungs, and against the **fair** floor for all tear-free rungs.
3. **Flip only the marker, not the whole screen.** Pre-render both states, so the flip involves no GPU work at present time where the platform allows it (KMS). This keeps the optical stimulus identical to the rungs and avoids backlight and ABL power effects from a full-screen invert.
4. **Define the marker as a fixed rectangle on the physical screen, in device pixels, stored in a per-machine `marker.json` that the harness owns.** Do not define it as "a corner of each app's window". Place it **near the top-left**, just below the maximized browser's toolbar and clear of the macOS notch and menu bar. Top placement keeps the scanout offset under about 1 ms. It also makes tear-free and tear-allowed floors directly comparable (see §5.5).
5. **The browser floor is a single inline HTML file.** A capturing `keydown` listener on `document` toggles a `position:fixed` div's background in the same task. Canvas and editable-input variants report *why* the rungs differ, but the div variant is the headline "browser floor".
6. **Calibration produces the harness noise band.** Two-day agreement on the floors gives the per-machine, per-refresh noise band (bootstrap p95 CI at 200 trials) that the agent acceptance rule in "How agents are used" needs. Treat that as a Phase 0 deliverable, not a side effect.

---

## 1. Display-floor app

### 1.1 One codebase or per-platform programs?

| Option | Pros | Cons |
| --- | --- | --- |
| winit + wgpu, present mode switch | One codebase; matches R5's likely stack | Hides the exact DXGI and Metal flags, and wgpu's swap-chain flags and waitable-object use change across versions. winit's event loop adds its own dispatch. There is no clean bare-KMS path: wgpu's DRM/VK_KHR_display surface support is recent and Vulkan-only **[verify]**. If R5 is also wgpu, a wgpu floor would share R5's bugs and hide the toolkit cost. |
| Per-platform C programs | Smallest, most transparent, easy for outsiders to audit | Three build systems. R5/R6 are Rust, so the toolchain splits. |
| **Rust workspace, one binary per platform, raw APIs only** (`windows` crate for D3D11/DXGI/Win32; `drm` + `input`/raw evdev via `libc` for KMS; `wayland-client` for Wayland; `objc2` / `objc2-metal` / `objc2-app-kit` for macOS) | One repo, one CI, one log format and CLI. Every flag is explicit in about 300–600 LOC per platform. Same toolchain as R5/R6. | Unsafe FFI-heavy code. Needs a reviewer who knows each API. |

**Recommendation:** the Rust workspace with raw APIs. Shared crate `floor-common` holds the CLI, `marker.json` parsing, the log ring buffer and the key-to-toggle logic. Each platform binary is `floor-win`, `floor-kms`, `floor-wl` or `floor-mac`. The optional `floor-wgpu` uses winit + wgpu in Immediate/Mailbox/Fifo modes with `desired_maximum_frame_latency = 1` **[verify field name for the pinned wgpu]**.

### 1.2 Common behaviour, all platforms

- The app is fullscreen, or a maximized window in the composed variant. It paints a flat background matching the rungs' page background (shadcn light: white; dark mode is a separate run). The marker rect from `marker.json` is painted black at start.
- On each **key-down of the scenario key**, the marker toggles black and white. Key-up and autorepeat are ignored. The scenario key is the same HID usage Scenario A sends (for example `a`), so the OS input path is identical to the rungs'.
- It is event-driven: the app blocks in the OS wait primitive, and on a key it renders and presents immediately. There is no frame loop while idle, which matches how rungs idle between trials.
- There is no just-in-time or late-latch trick. That belongs to R6. For a clear-colour frame, render cost is about 10–100 µs, so "render immediately, present immediately" is already optimal, whether vsync'd or tearing.
- **Busy-wait variant** (`--spin`): poll the input source in a tight loop on a pinned high-priority thread. Expect it to be within about 0.1 ms of event-driven. It exists as a sanity check that OS wake-up latency (C-states, timer coalescing) is not hiding cost. If the difference is larger, fix the power configuration; do not adopt spinning.
- `--log` (off by default, never on in headline sessions) records into a preallocated ring buffer, flushed at exit, per event: input timestamp (OS event time plus the app's receipt time on the monotonic or QPC clock), the present-call time, and the platform's presentation feedback (DXGI frame statistics, DRM flip event timestamp, `wp_presentation` feedback, `MTLDrawable.presentedTime`). This is the software self-check (§6).
- `--pattern align` blinks the marker at 2 Hz and draws a crosshair around it, for mounting the photodiode.

### 1.3 Windows 11: `floor-win`

- **API:** D3D11 (simpler than D3D12 here, with an identical present path) and a DXGI flip-model swap chain.
  - `DXGI_SWAP_EFFECT_FLIP_DISCARD`, `BufferCount = 2`, `DXGI_FORMAT_B8G8R8A8_UNORM` (or R10G10B10A2; avoid HDR/FP16 so MPO and iFlip promotion works).
  - Flags: `DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT`, plus `DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING` for the true-floor variant (check `DXGI_FEATURE_PRESENT_ALLOW_TEARING`).
  - `IDXGISwapChain2::SetMaximumFrameLatency(1)`. Before rendering each frame, wait on the waitable object (it should already be signalled when idle).
- **Window:** a `WS_POPUP` borderless window exactly covering the monitor, with nothing overlapping. The Windows 11 DWM then promotes the flip-model swap chain to **Independent Flip**, or to "Hardware Composed: Independent Flip" through an MPO plane. Legacy fullscreen exclusive (`SetFullscreenState(TRUE)`) is an optional `--fse` variant only. On current Windows it is largely emulated through the same flip path, so it is not expected to differ **[verify on the machine]**.
- **Variants:**
  - `composed`: a windowed but maximized non-fullscreen window, sync interval 1. DWM composes it.
  - `fair`: fullscreen borderless, `Present(1, 0)`.
  - `true`: fullscreen borderless, `Present(0, DXGI_PRESENT_ALLOW_TEARING)`.
- **Verification of present mode:** run PresentMon in a *separate* session. The `PresentMode` column must read `Hardware: Independent Flip` (or `Hardware Composed: Independent Flip`) for fair and true, and `Composed: Flip` for composed. Record the output with the session. Common spoilers of iFlip: Xbox Game Bar, GPU vendor overlays (GeForce/Adrenalin), Discord or Steam overlays, the Windows HDR toggle, and notifications. Disable all of them in the machine-prep checklist.
- **Input:** `RegisterRawInputDevices` for keyboard (usage page 1, usage 6), handled as `WM_INPUT` in the window proc. The loop is `MsgWaitForMultipleObjectsEx(1, &waitable, INFINITE, QS_ALLINPUT, MWMO_INPUTAVAILABLE)` followed by a `PeekMessage` drain. WM_INPUT and WM_KEYDOWN both come from the Raw Input Thread, so the difference is expected to be negligible. Raw input is still used because it skips translation and gives the device handle, which lets the app ignore any other keyboard. Also measure a `--input=wm_keydown` variant once to confirm this, since the rungs use WM_KEYDOWN (Chrome, winit).
- **Clock:** QPC for logs. DXGI `GetFrameStatistics` gives `SyncQPCTime` for the self-check.
- **Budget laptop:** same binary. Additional prep: disable Panel Self Refresh (Intel Graphics Command Center "Panel Self-Refresh", or AMD Vari-Bright), Content Adaptive Brightness Control (Settings > Display > Brightness), and Dynamic Refresh Rate. PSR exit and CABC both inject latency or brightness drift that belongs to neither the app nor the display floor as the spec defines it. **Open question:** should PSR stay on, because that is what users get, and be measured as a variant?

### 1.4 Linux, bare KMS: `floor-kms`

- Boot to `multi-user.target` with no compositor. The app becomes DRM master on `/dev/dri/cardN` (as root, or through logind `TakeControl`).
- **Buffers:** two dumb buffers (`DRM_IOCTL_MODE_CREATE_DUMB`), CPU-filled once at startup with the background and the two marker states. The flip is *only* an atomic commit that changes the primary plane's `FB_ID`, so no rendering happens on the key path at all. This is the purest floor on any platform.
- **Fair:** `drmModeAtomicCommit(fd, req, DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_PAGE_FLIP_EVENT)`, then handle the flip event, which carries the vblank timestamp and sequence.
- **True:** add `DRM_MODE_PAGE_FLIP_ASYNC`. Async flips through the atomic API landed in kernel 6.8, limited to an FB_ID change on the primary plane on amdgpu/i915 **[verify for the GPU in the reference desktop]**. The fallback is legacy `drmModePageFlip(..., DRM_MODE_PAGE_FLIP_ASYNC)`.
- **Input:** open the keyboard's `/dev/input/eventN` directly, call `EVIOCGRAB` to keep keys out of the VT, and `EVIOCSCLOCKID(CLOCK_MONOTONIC)` so event timestamps share a clock with DRM flip events. Wait with `epoll` on the evdev fd and the DRM fd. Nothing else sits between the kernel and the app, so this is also the input floor.
- VRR off (`VRR_ENABLED=0` on the CRTC). The mode is fixed to the tested refresh rate (60 or 240) with an explicit `MODE_ID`.

### 1.5 Linux, Wayland: `floor-wl`

- `wayland-client` with `xdg_toplevel.set_fullscreen`. Use two pre-rendered buffers: `wl_shm` for simplicity, or dmabuf through `zwp_linux_dmabuf_v1` so the compositor can do **direct scanout** (most compositors only scan out dmabuf client buffers, not shm) **[verify per compositor]**. Commit on `wl_keyboard.key` (pressed).
- Variants: windowed (composited), fullscreen with direct scanout (fair), and fullscreen with `wp_tearing_control_v1` set to async (true), where the compositor supports it.
- Feedback: `wp_presentation` feedback gives the presented timestamp and whether the zero-copy flag was set. That confirms direct scanout without a camera.
- **Compositor choice (open decision):** KWin (Plasma 6) is recommended. It supports direct scanout, `tearing-control-v1` and VRR control, and is widely used. Mutter's support for tearing-control is uncertain **[verify]**. Sway/wlroots is a lean alternative. Pick one and pin its version.
- Input here passes through libinput and the compositor. KMS floor minus Wayland floor measures the compositor's input and present cost, which is exactly the "compositor comparison" the test matrix asks for.

### 1.6 macOS: `floor-mac`

- An AppKit `NSWindow` with a layer-backed `NSView` whose layer is a `CAMetalLayer`: `opaque = YES`, `framebufferOnly = YES`, `pixelFormat = BGRA8Unorm`, `maximumDrawableCount = 2` (the minimum allowed), `presentsWithTransaction = NO` (YES ties presentation to Core Animation transactions and adds latency).
- **Variants:**
  - `composed`: a windowed, maximized window with `displaySyncEnabled = YES`.
  - `fair`: native fullscreen (`toggleFullScreen:`) with `displaySyncEnabled = YES`. An opaque fullscreen layer is eligible for **direct-to-display**, which bypasses WindowServer composition. Confirm with the Instruments Metal System Trace display track in a separate session.
  - `true`: fullscreen with `displaySyncEnabled = NO`. This only has an effect when direct-to-display is active; windowed, WindowServer still syncs.
- **Render path:** in `keyDown:` call `nextDrawable` (it should return immediately, because drawables are free when idle), run one render pass with a clear plus a scissored clear or quad for the marker, then `presentDrawable:` and commit. Do not wait for CVDisplayLink or `CAMetalDisplayLink`.
- **ProMotion:** the built-in 120 Hz panel is variable refresh. The Displays settings expose ProMotion plus fixed rates (60, 59.94, 50, 48, 47.95 Hz), but not a fixed 120 Hz **[verify on the target model and macOS version]**. So the Mac cannot meet "VRR off at 120 Hz". Test **ProMotion** (the default users get) and **60 Hz fixed**. With ProMotion, set the layer's `preferredFrameRateRange` or `CAFrameRateRange` to 120 max and use `presentDrawable:afterMinimumDuration:` only if the rungs do. An idle panel may be at a low refresh rate, and a present during idle may be scanned out almost immediately. This is a real VRR latency *advantage*. Expect a distribution narrower than one 120 Hz frame, which breaks the "spread ≈ one refresh" sanity check (§5.5).
- **Input:** the default is `NSEvent` `keyDown:` in the first-responder view. Optional variant: an `IOHIDManager` input-value callback on the main run loop, which needs the Input Monitoring permission. It skips the WindowServer event path and bounds that path's cost. Expect a difference well under 1 ms.
- Mac-specific prep: disable True Tone, auto-brightness, and "Slightly dim the display on battery"; stay plugged in. The notch: fullscreen apps on notched MacBooks are laid out below the camera housing by default, and the strip beside the notch stays black. The marker must sit below that strip (§4).
- Panel caveats: the mini-LED **local dimming** means a 64 px black-to-white change also drives a backlight zone change, which may add latency beyond pixel response. The backlight may PWM even at full brightness, visible at 20 kHz sampling. The rig-analysis workstream needs a low-pass filter and threshold hysteresis. Measure this during calibration day 1.

### 1.7 Tear-allowed vs tear-free: report both

Yes, report both, plus composed (§0.2). Physics note for the write-up: for a marker at the **top** of the screen, tear-free and tear-allowed floors should have nearly the same distribution.
- Tear-free: the wait is until vblank, uniform over 0..T, and scanout then reaches the top rows almost immediately.
- Tearing: the wait is until the scanline next passes the marker rows, also uniform over 0..T.

Tearing only helps content lower on the screen, by the scanout offset y/H·T, and removes the missed-latch penalty. So if the true floor is materially faster than the fair floor with a top marker, the tear-free path is queueing a frame somewhere. That is a free consistency check. It is also why the marker's vertical position must be recorded and held fixed (§4).

---

## 2. Input path, per OS (summary)

| OS | Floor app input | Wait primitive | What the rungs use (for context) |
| --- | --- | --- | --- |
| Windows | Raw Input `WM_INPUT` (keyboard, specific device) | `MsgWaitForMultipleObjectsEx` on swap-chain waitable + queue | Chrome and winit use `WM_KEYDOWN`/`WM_CHAR` |
| Linux KMS | evdev `/dev/input/eventN`, grabbed, `CLOCK_MONOTONIC` | `epoll` (evdev fd + DRM fd) | n/a (R6 only) |
| Linux Wayland | `wl_keyboard.key` | `wl_display` fd in `poll` | Chrome (Ozone/Wayland), winit |
| macOS | `NSEvent keyDown:` (IOHIDManager variant) | `NSApplication` run loop | Chrome, winit/GPUI via NSEvent |

Rules:
- Event-driven, with render and present in the input handler. No fixed-rate polling loop, because a loop at period P adds U(0, P).
- High-performance power plan. On Windows, confirm no `timeBeginPeriod` dependency; blocking waits do not need it.
- Keyboard polling on the host side is USB 1 kHz (spec). Record whether the rig's t0 is "report queued" or "IN transfer completed". The first adds U(0,1) ms jitter. **Coordinate with the firmware workstream: IN-completion is recommended as t0.**
- Optional kernel-path sanity check: the rig presses Caps Lock and times the host's LED output report (SET_REPORT) coming back. That is a USB-in to OS-kernel to USB-out loopback with no display, which bounds OS input-stack latency per machine.

---

## 3. Browser-floor page

### 3.1 Headline variant (`browser-floor.html`, div)

```html
<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser floor</title>
<style>
  html,body{margin:0;height:100%;background:#fff}
  #m{position:fixed;left:var(--mx);top:var(--my);width:var(--ms);height:var(--ms);
     background:#000;contain:strict;will-change:background-color}
</style></head>
<body><div id="m"></div>
<script>
  // Marker rect in device px comes from marker.json, injected at build time (see §4).
  const R = /*MARKER*/{x:16,y:160,size:64}/*END*/;
  const m = document.getElementById('m');
  function place(){ const d = devicePixelRatio;
    m.style.setProperty('--mx', (R.x - screenLeftDev())/d + 'px'); /* see §4.3 */
    m.style.setProperty('--my', (R.y - viewportTopDev())/d + 'px');
    m.style.setProperty('--ms', R.size/d + 'px'); }
  let white = false;
  document.addEventListener('keydown', e => {
    if (e.repeat) return;
    white = !white;
    m.style.backgroundColor = white ? '#fff' : '#000';
  }, {capture: true});
</script></body></html>
```

The actual placement helper is §4.3. Design points:
- **`keydown` on `document`, capture phase**, toggling in the same task. `keydown` is a discrete event: Chrome dispatches it immediately, not rAF-aligned like `pointermove`. It is also the earliest DOM event for a keystroke. `beforeinput` and `input` fire later and need a focused editable, so they are not the floor.
- **Editable variant** (`--variant input`): a focused `<input>` whose `input` event toggles the marker. This is the path R1–R3 actually use. Editable floor minus keydown floor measures the browser's text-editing cost, which is useful for the waterfall. Report it as the "browser floor (editable)".
- **Canvas variants** matching R4:
  - `canvas2d`: `getContext('2d', {alpha:false})` with `fillRect`.
  - `canvas2d-desync`: the same plus `desynchronized: true`. This is Chrome's low-latency hint and can use a front-buffer or overlay path that may tear. Support and behaviour vary by OS and GPU **[verify on the pinned Chrome per OS]**; the tearing and camera check applies.
  - `webgpu`: `configure({ ..., alphaMode:'opaque' })` and a clear pass.
  Each is measured, but the **div variant is the headline** browser floor. The canvas variants are R4's comparator, and the desynchronized one may beat the div floor, so it must be labelled.
- No fonts, images, external scripts or frameworks. Everything is inline. The page is served by the **same local static server the web rungs use**, so origin and process model match; `file://` gets different process treatment in some cases.
- Marker style: `position:fixed` plus `contain:strict` makes the change a paint-only invalidation of a small layer.

### 3.2 Chrome configuration

- **Pinned Chrome stable.** The Windows/Mac enterprise installer with updates disabled by policy (`UpdateDefault=0` / `AutoUpdateCheckPeriodMinutes=0`, or the macOS Keystone policy), or the matching **Chrome for Testing** build of the same version. Open decision: the spec says "stable". Chrome for Testing is the easiest exact pin, but it is a different channel binary. Both are default-sandboxed.
- Launch: `--user-data-dir=<fresh temp dir>` and `--no-first-run --no-default-browser-check`, extensions off. **No** `--disable-gpu-vsync`, `--disable-frame-rate-limit`, `--no-sandbox`, `--disable-site-isolation-trials`, or any flag the parity "Sandbox" check would reject. `--kiosk` / `--start-fullscreen` are UI flags, not security flags, but they change compositor behaviour (below), so treat them as variants.
- **Windowed vs fullscreen:** a maximized windowed Chrome is composed by DWM, WindowServer or the Wayland compositor. Fullscreen (F11 or `requestFullscreen`) may be promoted to iFlip or direct scanout, depending on Chrome's DirectComposition and overlay decisions. **Recommendation:** the headline is **maximized windowed**, because that is what users get and it keeps the compositor delta inside R5 to R6 as the ladder intends. Also measure **fullscreen** once per machine as a diagnostic. This decision has to be the same for R1–R4 (open decision, §8).

### 3.3 Expected position in the waterfall

Composed native floor ≤ browser floor (div) ≤ browser floor (editable) ≤ R3. The browser floor minus the composed native floor is the irreducible browser-pipeline cost: main-thread frame, commit, raster, Viz draw, GPU process present.

---

## 4. Marker placement contract (shared by all rungs)

### 4.1 Problem

The spec's "64 px square in a fixed corner" is ambiguous in three ways:
- Browser chrome, the taskbar, the Dock, the menu bar and the notch occupy some screen corners.
- "64 px" at 150% or 200% scaling is either 64 or 128 physical px.
- The scanout position of the marker adds up to one frame of bias, depending on the corner.

The photodiode mount must never move between rungs, so the contract has to be a **physical screen rectangle**.

### 4.2 Contract

- A `marker.json` per machine and display mode, owned by the harness (agents cannot edit it):
  `{ "screen": "<EDID id>", "mode": "2560x1440@240", "x": 16, "y": 160, "size": 64, "units": "device_px", "scanout_row_fraction": 0.11 }`
- **Size:** `size` is fixed in device pixels per machine and is **not** re-scaled when OS scaling changes. Pick it so the square is at least 8–10 mm on the panel, which leaves margin for suction-mount alignment and a typical 2.7×2.7 mm photodiode such as the BPW34. For example: 27" 1440p (0.233 mm pitch): 64 px ≈ 15 mm. 27" 4K (0.155 mm): 64 px ≈ 10 mm. MacBook Pro 14" (about 0.1 mm): 64 px ≈ 6.4 mm, too small, so use 128 device px there. Proposal: size = 64 × (the machine's default scale factor), fixed thereafter.
- **Position:** near the top-left, at the first position that is inside the content area of a **maximized browser window**. That means below the tab strip and toolbar, and on the Mac below the menu bar and notch strip. With auto-hidden taskbar and Dock, the same screen rect is then inside every rung's content in both windowed and fullscreen modes. Keep y small, so the scanout offset is 1 ms or less at 60 Hz, and record `scanout_row_fraction = y/H` so analysis can report the offset. Left is preferred to right because RTL layouts (Arabic/Hebrew) do not move a left-edge fixed element, and shadcn's palette content is centred.
- **Every rung, and both floors,** converts the screen rect to its own coordinates at startup and on window move or scale change:
  - Native: screen device px → window client px, via `ClientToScreen`/DPI APIs, `convertRectFromScreen:` × `backingScaleFactor`, or the Wayland surface position (fullscreen only; Wayland does not expose a window's global position, so on Wayland windowed rungs the marker is defined relative to the fullscreen or maximized surface origin instead **[design note]**).
  - Web: CSS px = (screen device px − window content origin in device px) / `devicePixelRatio`. The content origin comes from `window.screenX/screenY` plus `outerHeight − innerHeight` chrome. That is fragile, so the harness should inject the viewport-relative rect for the pinned Chrome layout, measured once per machine with the alignment pattern. Browser zoom changes `devicePixelRatio`, and the formula keeps the marker physically fixed under zoom.
- **Colours:** pure `#000` and `#fff` in sRGB, no transitions, no antialiasing on the edges (snap to device pixels). Dark mode does not change the marker.
- **Verification:** each rung's launch includes a one-shot "alignment" check. The harness triggers the alignment pattern (a key the rungs map to a debug toggle, or a query string) and the photodiode must see ≥90% contrast. A camera frame of the mount goes into the session record.

### 4.3 Open point

If the project prefers a literal screen corner, **top-left in fullscreen** is the only corner with no scanout bias. It forces all rungs to run fullscreen, which moves the compositor-bypass question out of R6 and into every rung. That is why §4.2 recommends a fixed near-top-left rectangle instead.

---

## 5. Calibration procedure

### 5.1 Order within one session (per machine, per refresh rate)

1. **Prep checklist** (scripted where possible, results saved to the manifest):
   - Power plan and plugged in; notifications, overlays and Game Bar off.
   - Resolution, refresh, scaling and brightness at 100% (max SDR). HDR off. VRR off unless it is the variant under test.
   - Monitor OSD recorded: overdrive level, game or low-lag mode, dynamic contrast, local dimming, black equalizer.
   - Laptop: PSR, CABC and DRR off. Mac: True Tone and auto-brightness off.
   - Reboot, then 10 min idle warm-up for panel temperature.
2. **Rig LED loopback:** the MCU drives an LED inside the mount, facing the photodiode. 1,000 trials. Expected: under 0.1 ms, with jitter at or below one sample (50 µs at 20 kHz). This value is subtracted as rig offset only if it is not negligible. Check the amplifier's rise and fall time and threshold choice at the same time.
3. **Mount** the photodiode on the marker rect with `--pattern align`, and record the contrast levels (black and white ADC means).
4. **Display floor, all variants.** For each variant: 50 warm-up trials plus 500 measured, with random 50–250 ms gaps (spec protocol).
   - Windows desktop: composed, fair, true, and `--spin` (100 trials).
   - Linux: KMS fair, KMS true, and Wayland composed, fair and true.
   - macOS: composed, fair, true, at ProMotion and 60 Hz.
   - Windows laptop: composed and fair (true if the driver allows).
5. **Browser floor, all variants.** div (headline), editable, canvas2d, canvas2d-desync and webgpu; windowed, plus one fullscreen div run.
6. **Camera spot check** (at least day 1): 20 trials of the fair floor and the browser-floor div with the 1,000 fps camera. Photodiode and camera must agree within ±1 camera frame. The camera also confirms tearing is absent in fair and present in true.
7. **Negative control:** move the sensor 2 cm off the marker. Trials must time out, which shows the sensor responds to the marker and not to global brightness.
8. **Attribution session** (separate, never mixed with the numbers above): PresentMon on Windows confirms the present mode; Instruments on the Mac confirms direct-to-display; `wp_presentation` or the DRM `--log` self-check on Linux.

Trial count: about 550 trials × about 0.2 s ≈ 2 min per variant. A full desktop session (about 20 variants across 60 and 240 Hz) is about 1 hour of trials plus about 1 hour of prep and mounting.

### 5.2 Two days

- Day 2 is on a different calendar day. Reboot, and **remove and re-mount the photodiode** so mount repeatability is part of the test. Keep the same binaries (same git SHA), the same Chrome build and the same OS build (pause Windows Update).
- Randomize the variant order per day (spec rule).

### 5.3 What "agreement" means

- **Primary (spec criterion):** |p50(day1) − p50(day2)| ≤ 2 ms for every floor variant. Target ≤ 1 ms at 240 Hz, where 2 ms is half a frame.
- **Secondary:** |Δp95| ≤ max(2 ms, 0.25 × refresh period), and the bootstrap 95% CI of the day-to-day difference contains 0 or lies inside ±2 ms.
- Rise (black to white) and fall (white to black) are **analysed separately**, because LCD response differs by direction. Pooled numbers are reported as well.
- **The noise band output:** from the pooled two-day data, compute the bootstrap distribution of p95 for 200-trial subsamples (the agent loop's trial count). Its 95% width is the per-machine, per-refresh noise band used by the acceptance rule. Publish it.
- Failure handling: if a floor disagrees, do **not** proceed to rung measurements on that machine. Investigate the mount, OSD settings, PSR, power state and driver.

### 5.4 What gets recorded

- **Per trial:** trial id; variant; inter-trial delay; MCU timestamps (report queued, IN completed, light crossing, threshold used); transition direction; a raw ADC window of about 30 ms around the event (compressed; kept for all trials, since it is cheap); timeout flag.
- **Per session manifest:** machine; OS build; GPU and driver version; monitor model, firmware, EDID and OSD settings; resolution, refresh, scaling and brightness; VRR, PSR, HDR and CABC state; power plan; Chrome version and exact launch flags; floor-app git SHA and build hash; `marker.json`; mount photo; ambient light (lux, if a meter is available); rig firmware version; LED loopback result; operator; date.
- **Attribution artefacts** in separate files: PresentMon CSV, Instruments trace, `--log` dumps.

### 5.5 Sanity checks and rough expectations

Model: latency ≈ USB (0–1 ms) + OS input (~0.1–0.5) + render (~0.1) + wait-for-scanout-of-marker (U(0,T)) + queued frames (k·T) + panel processing (0–a few ms) + pixel response to the 50% threshold (~1–5 ms on a fast LCD; more on slow panels or mini-LED).

| Config | Expected p50 (rough) | Expected spread (p99−p1) |
| --- | --- | --- |
| Desktop 60 Hz, fair floor | ~10–14 ms | ≈ one frame (~17 ms) + 1–2 ms |
| Desktop 60 Hz, true floor (top marker) | ≈ fair floor ±1 ms | ≈ one frame |
| Desktop 60 Hz, composed floor | fair + ~one frame (~25–30 ms) | ≈ 1–2 frames |
| Desktop 240 Hz, fair floor | ~4–6 ms | ≈ 4.2 ms + 1–2 ms |
| Desktop 240 Hz, composed | ~8–10 ms | |
| Browser floor div, windowed, 60 Hz | ~30–50 ms (2–3 frames) | 1–2 frames |
| MacBook ProMotion, fair or direct-to-display | ~8–15 ms, highly panel-dependent | possibly < one 120 Hz frame (VRR) |
| MacBook ProMotion, composed | ~15–25 ms | |
| Budget laptop 60 Hz, composed | ~30–40 ms (PSR off) | |

These are priors for spotting a broken setup, not targets.

Automated checks, run by the analysis script on every session:
1. **Spread ≈ refresh period.** For fixed-refresh vsync'd floors, p99−p1 should be within [0.8T, T + 3 ms]. A much wider spread means queueing or missed latches. **Bimodality with modes about T apart** means an occasional extra queued frame. Flag it.
2. **Phase uniformity.** A histogram of (latency mod T) should be flat when inter-trial randomization works. It is inspected visually, and a χ² test gives a warning only.
3. **Refresh scaling.** p50(60 Hz) − p50(240 Hz) ≈ (16.7 − 4.2)/2 ≈ 6 ms, plus any difference in panel response between modes. A large deviation points at queueing.
4. **Tear vs no tear:** true ≈ fair within about 1 ms for a top marker (§1.7).
5. **Ordering:** true ≤ fair ≤ composed ≤ browser div ≤ browser editable. Any violation is an error, except canvas-desync, which may beat the div.
6. **Minimum plausibility:** min latency > (USB min + panel min), about ≥ 1–2 ms. Anything faster means the threshold or trigger is wrong.
7. **Rise and fall:** the two directions differ by a consistent amount on both days.
8. **Timeouts** below 0.2%. Any timeout in a floor variant is investigated.
9. **Spin vs event-driven** within about 0.2 ms.
10. **LED loopback** under 0.1 ms and stable across days.

---

## 6. CI and development without the rig

Latency cannot be measured in CI, but **correctness and plumbing** can:
- **Builds:** GitHub Actions matrix on `windows-latest`, `macos-latest` and `ubuntu-latest`, running `cargo build --release`, clippy and unit tests for `floor-common`, including marker-rect conversion across scale factors (100/150/200%).
- **Linux KMS smoke test:** the `vkms` virtual KMS driver supports atomic commits and simulated vblank page-flip events, and `uinput` injects key events. Run `floor-kms --log` under vkms, inject 100 keys, and assert one flip per key-down, alternating framebuffers, and flip timestamps after input timestamps. Hosted runners may not ship or allow loading `vkms` **[verify]**. The fallback is a QEMU VM step (virtio-gpu has KMS) or a self-hosted runner.
- **Wayland smoke test:** run a headless compositor (`weston --backend=headless`, or sway headless), inject keys with a virtual keyboard protocol client or `wtype`, and check `wp_presentation` feedback alternation.
- **Windows:** hosted runners have WARP (the software D3D). Run `floor-win --log` windowed, with keys injected through `SendInput`, and assert toggles and DXGI frame statistics. iFlip will not engage. This checks logic only **[verify interactive-session availability on hosted runners]**.
- **macOS:** build plus unit tests. Metal on hosted runners may be limited, so the render test runs only if a device is available.
- **Browser floor:** use Playwright (or Puppeteer) with the *same pinned Chrome* version, headful under Xvfb or headless. Dispatch real key events through CDP `Input.dispatchKeyEvent`, and assert that the marker's computed colour and on-screen screenshot pixel at the marker rect alternate. Assert no `--no-sandbox` in the launcher config (a lint for the parity Sandbox check). Assert the page has zero external requests.
- **Software-timestamp self-check** (on real hardware, not the headline): `--log` gives input-to-present and input-to-presented-vblank distributions from OS timestamps. In the page, `event.timeStamp` to next rAF to (optionally) Perfetto trace. The self-check lower-bounds the photodiode result. If photodiode minus the self-check "presented" time is not about equal to panel processing plus response (a roughly constant few ms), something is off between present and photon.

---

## 7. Task breakdown

Estimates are person-days (pd) for a human with agents writing most code. The rig-owner days need the physical rig.

| # | Task | Est. | Depends on |
| --- | --- | --- | --- |
| 1 | Marker contract: `marker.json` schema, device-px to window/CSS conversion lib (Rust and TS), alignment pattern, per-machine placement procedure | 1.0 | agreement on §4 |
| 2 | `floor-common`: CLI, ring-buffer log, key filter, align pattern | 0.5 | 1 |
| 3 | `floor-win`: D3D11 + flip-model swap chain, waitable, max latency 1, composed/fair/true/FSE variants, raw input and WM_KEYDOWN variant, frame stats log | 2.5 | 2 |
| 4 | `floor-kms`: DRM master, dumb buffers, atomic flip plus async flip, evdev grab, monotonic clock log | 2.0 | 2 |
| 5 | `floor-wl`: fullscreen xdg, dmabuf buffers, tearing-control, presentation feedback | 2.0 | 2, compositor decision |
| 6 | `floor-mac`: AppKit + CAMetalLayer, composed/fair/true, NSEvent and IOHID variants, `presentedTime` log | 2.0 | 2 |
| 7 | `floor-wgpu` diagnostic (winit + wgpu, empty R5) | 1.0 | 2 (optional) |
| 8 | Browser-floor page and variants (div, editable, canvas2d, desync, webgpu); marker injection; pinned-Chrome launcher script with the flags audit | 1.5 | 1, Chrome pin decision |
| 9 | CI: 3-OS build matrix, vkms+uinput test, headless Wayland test, Playwright page test | 2.0 | 3–8 |
| 10 | Machine-prep checklists and scripts per OS (PSR, CABC, overlays, power, OSD recording, manifest capture) | 1.0 | |
| 11 | Calibration analysis script: percentiles, rise/fall split, bootstrap day-to-day diff, noise band, the §5.5 automated checks, report | 2.0 | rig log format (firmware workstream) |
| 12 | Calibration run day 1: 4 machine configs (Win desktop at 60 and 240, Linux, Mac, laptop), including camera spot check | 2.5 | rig, 3–11 |
| 13 | Calibration run day 2 and comparison; investigate disagreements | 2.0 (+1–3 contingency) | 12 |
| | **Total** | **about 22 pd** (about 14 dev, largely agent-assisted, and about 5–8 rig-owner days) | |

---

## 8. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| iFlip or direct scanout silently not engaged (overlays, HDR, format, notification) | Fair floor quietly equals composed | PresentMon, Instruments or `wp_presentation` in every attribution session; ordering check §5.5 |
| PSR, CABC or DRR on laptops | Extra frames, or brightness drift that trips thresholds | Prep checklist; optional "as shipped" variant |
| Monitor overdrive or OSD mode differences between days | Day-to-day disagreement | Record the OSD in the manifest; tape or lock the buttons |
| MacBook: no fixed 120 Hz, mini-LED local dimming, PWM backlight | Mac floor not comparable to fixed-refresh machines; noisy sensor | Report ProMotion and 60 Hz; filter plus hysteresis; camera cross-check |
| Notch, menu bar or browser chrome overlap the marker | Marker hidden in some rungs | §4 contract with a per-machine placement and alignment check |
| Async atomic flip unsupported on the GPU or kernel | No KMS true floor | Legacy `drmModePageFlip` async; else report as N/A |
| Chrome auto-update or a different binary | Browser floor drifts | Update policy off; record the version per session |
| CI cannot load vkms or use GPU devices | Weaker smoke tests | QEMU or self-hosted fallback; logic-only tests elsewhere |
| USB t0 definition mismatch with firmware | 0–1 ms uniform bias or jitter | Agree on IN-completion timestamping |
| The floor app accidentally becomes an "R6 lite" that agents copy for rungs | Blurs the ladder | It lives in the harness repo; agents cannot edit it (spec guardrail) |

---

## 9. Open decisions for a human

1. **Marker definition:** a fixed near-top-left screen rectangle in device px, below browser chrome (recommended), or a literal screen corner (forces fullscreen everywhere). Size fixed in device px per machine (recommended), or logical 64 px that scales.
2. **Windowed vs fullscreen for R1–R5 and the browser floor.** Recommended: maximized windowed, with fullscreen as a diagnostic. This decides whether the compositor cost lands in R5 to R6 (as designed) or disappears earlier.
3. **Which floor is "the" floor** in the headline waterfall: fair (tear-free, recommended), with true and composed shown as reference lines.
4. **Wayland compositor** to pin: KWin/Plasma 6 recommended.
5. **Mac refresh modes:** ProMotion plus 60 Hz fixed (a fixed 120 Hz does not exist). Accept that the Mac's "spread ≈ refresh" check does not apply under VRR.
6. **Laptop PSR:** disable (clean floor) or keep as shipped (user reality), or both.
7. **Chrome pinning:** Chrome stable with updates frozen by policy, or Chrome for Testing at the same version.
8. **Rig t0:** HID report queued, or IN transfer complete (shared with the firmware workstream).
9. **Agreement thresholds:** keep 2 ms p50 at all refresh rates, or tighten to about 1 ms at 240 Hz; whether p95 agreement is also required.
10. **Language:** Rust workspace with raw APIs (recommended), or C per platform.
