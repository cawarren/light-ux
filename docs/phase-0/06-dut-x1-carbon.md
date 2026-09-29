# 06: ThinkPad X1 Carbon Gen 13 (OLED, Lunar Lake) as a Phase 0 machine under test

Written 2026-09-29. It assesses the owner's proposal to use their own laptop as the Phase 0 reference machine under test (DUT), running Linux:

> Lenovo ThinkPad X1 Carbon Gen 13 Aura Edition (Copilot+), 14" 2.8K OLED (2880×1800), Intel Core Ultra 7 258V/268V (Lunar Lake), Arc 140V iGPU, 32 GB.

Claims marked **[verify]** could not be confirmed from a primary source and must be checked on the actual unit during bench bring-up. Commands for doing that are given in §8.

## TL;DR

- **Panel:** a Samsung Display **ATNA40YK20-0** OLED at **120 Hz and 60 Hz, fixed refresh**. Its EDID has no range-limits descriptor, so it has **no VRR**. It **modulates at 240 Hz at every brightness**: roughly 33% amplitude at 100%, deeper PWM below 50%, "DC-like" above 50%. Pixel response is about 1–2 ms. A 500-nit "120 Hz VRR" OLED also exists for this model, so confirm which panel this unit has from its EDID.
- **The 240 Hz flicker changes the detector plan.** The ≥100 kS/s boxcar-over-a-PWM-period filter designed for the MacBook's 14.88 kHz would be a **4.2 ms** window here, which is unusable. Rising edges (black to white) are clean, because OLED black emits nothing. Falling edges need a threshold **below the PWM trough**, not "10% below peak". Run at **100% brightness**.
- **Linux:** the `xe` kernel driver (the default for Lunar Lake since **Linux 6.12**) and **Mesa ≥ 24.2.2**. In practice use a current kernel (≥ 6.15) and Mesa 25.x. PSR and Panel Replay can be controlled with `xe.enable_psr=` and `xe.enable_panel_replay=`, or switched at runtime through debugfs.
- **Known platform bug:** CPUs **stuck at 400 MHz** (out of the box on the "balanced" profile, and intermittently after long s2idle sleeps; still open in Sep 2026). **Reboot before every session and use the `performance` platform profile.**
- **Compositor bypass works on this hardware:** atomic `DRM_MODE_PAGE_FLIP_ASYNC` on the primary plane (linear buffers are allowed on Xe2), KWin tearing-control plus direct scanout, and bare KMS from a VT. Mutter still has **no merged tearing-control support**.
- **240 Hz only through an external monitor** over Thunderbolt 4 / DP alt mode. **1440p240 and 1080p240 are fine. Avoid 4K240**, which needs the pipe joiner, and the xe driver **rejects async flips with the joiner**. The HDMI port is limited to 4K60.
- **Matrix fit:** a good *Linux laptop with an OLED panel* data point and a fine bring-up DUT. It does **not** fully replace "same desktop, dual boot", which exists to hold the hardware constant across operating systems. The best use is to **dual-boot the X1 itself** (it ships with Windows 11), which gives Windows versus Linux on identical hardware.

---

## 1. Panel

| Property | Value | Source |
| --- | --- | --- |
| Panel | Samsung Display (EDID mfr `SDC`, model 16799) **ATNA40YK20-0**, 14", 2880×1800, 16:10, 243 PPI, 10-bit DP/eDP | EDID dump from a Gen 13 hardware probe ([linux-hardware.org #0b8d80807b](https://linux-hardware.org/?probe=0b8d80807b)); [Notebookcheck review](https://www.notebookcheck.net/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-laptop-review-The-X1-Carbon-is-finally-back.924998.0.html) |
| Modes | DTD1 **2880×1800 @ 119.9996 Hz**, DTD2 **@ 59.9998 Hz**. Both use the **same 652.26 MHz pixel clock and 218.879 kHz line rate**. The 60 Hz mode is made by stretching vertical back porch from 8 to **1832 lines** | EDID (probe above) |
| VRR | **None on this panel.** The EDID has no Display Range Limits descriptor and Notebookcheck saw "no dynamic refresh rate". The unit **ships at 60 Hz**; 120 Hz must be selected. Lenovo's PSREF lists two OLEDs: **400-nit 120 Hz (Lunar Lake only)** and **500-nit "120Hz VRR"**. Confirm this unit's panel from its EDID | [Notebookcheck](https://www.notebookcheck.net/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-laptop-review-The-X1-Carbon-is-finally-back.924998.0.html); [PSREF Aura Edition spec (PDF)](https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_X1_Carbon_Gen_13_Aura_Edition/ThinkPad_X1_Carbon_Gen_13_Aura_Edition_Spec.PDF) |
| Brightness | 410 cd/m² average SDR (Notebookcheck), 0.8 cd/m² minimum. The EDID's HDR metadata gives 617 nits max and 400 nits max frame-average. DisplayHDR True Black 500, Dolby Vision | Notebookcheck; EDID; PSREF |
| **Flicker** | **240 Hz** modulation, **33% amplitude**, "≤ 100% brightness", i.e. present even at full brightness. "Above 50 percent brightness, it switches to DC dimming", still at 240 Hz; below 50% it is true PWM. Notebookcheck published no duty cycle or per-level depth, so **characterise it ourselves** | [Notebookcheck EN](https://www.notebookcheck.net/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-laptop-review-The-X1-Carbon-is-finally-back.924998.0.html), [Notebookcheck DE](https://www.notebookcheck.com/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-Laptop-Test-Das-X1-Carbon-ist-endlich-back.932285.0.html) |
| Response | Black↔white **1.9 ms** (0.9 ms rise + 1.0 ms fall); 50–80% grey 2.4 ms | Notebookcheck |
| HDR / dimming modes | SDR by default. HDR is optional (Windows HDR, or KWin HDR on Linux). **Keep SDR** for all sessions | — |

**Timing consequences:**

- **Fast scanout at 60 Hz.** Both modes scan the 1800 active lines in 1800 / 218.879 kHz ≈ **8.2 ms**. At 60 Hz the remaining ~8.4 ms is vertical blank. So "60 Hz" on this laptop is not like 60 Hz on a typical desktop monitor, which scans over most of 16.7 ms. Vblank-wait statistics match at the top of the screen but differ further down. Record `scanout_row_fraction` against the *active scan time*, not the frame period.
- **The 240 Hz modulation is 4 cycles per frame at 60 Hz and 2 per frame at 120 Hz.** OLED emission is usually driven row by row in step with scanout, so the dips are probably phase-locked to refresh **[verify]**. The marker then sees its dips at a fixed phase relative to its own scanout. Characterise at both refresh rates.
- The marker size in physical units shrinks on this panel. 64 device px at 243 PPI is **6.7 mm**, against about 15 mm on a 27" 1440p monitor. The BPW34 active area is 2.65 × 2.65 mm, so a 64 px marker still covers it, with about ±2 mm of mounting tolerance. **Recommend 128 device px (13.4 mm) on this panel**, or specify the marker's minimum size in mm in `marker.json` (§7).

## 2. Linux on Lunar Lake

### 2.1 Driver and versions

- **Kernel driver: `xe`, not `i915`.** Lunar Lake is the first Intel generation where xe is the default. Xe2 is enabled by default (no `force_probe`) from **Linux 6.12** ([Phoronix, 6.12 default](https://www.phoronix.com/forums/forum/linux-graphics-x-org-drivers/intel-linux/1488256-intel-enables-xe2-lunar-lake-battlemage-graphics-by-default-with-linux-6-12); [Phoronix, LNL firmware](https://www.phoronix.com/news/Intel-Lunar-Lake-Graphics-FW)). The display code is shared with i915, so display module parameters have the same names but take the **`xe.` prefix**.
- **Mesa ≥ 24.2.2** enables Lunar Lake in Iris/ANV by default, and needs Linux 6.12+ ([Phoronix](https://www.phoronix.com/news/Mesa-24.2.2-Released)).
- Phoronix found the laptop "works well" on **Ubuntu 25.04 / Fedora 42** once the 400 MHz issue is worked around ([Phoronix review](https://www.phoronix.com/review/lenovo-thinkpad-x1-gen13-linux)). A Sep 2026 bug report used Fedora 44. Lenovo lists Fedora and Ubuntu preloads for this model and publishes a Linux user guide ([PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_X1_Carbon_Gen_13_Aura_Edition/ThinkPad_X1_Carbon_Gen_13_Aura_Edition_Spec.PDF); [Linux UG](https://download.lenovo.com/pccbbs/mobiles_pdf/x1_carbon_gen13_2in1_gen10_linux_ug.pdf); [Arch wiki](https://wiki.archlinux.org/title/Lenovo_ThinkPad_X1_Carbon_(Gen_13)): GPU `8086:64a0` works). Audio needs `sof-firmware`, and BIOS updates go through fwupd.
- **Recommendation:** a current stable distro with kernel **≥ 6.15** and Mesa **25.x**, e.g. Fedora 44 or Arch, pinned (§6). One owner report fixed external-monitor flicker only by moving to 6.15.9 ([Lenovo forum thread](https://forums.lenovo.com/t5/Lenovo-Yoga-Series-Laptops/X1-Carbon-Gen13-Laptop-monitor-flickers-when-external-monitor-connected/m-p/5384568)).

### 2.2 PSR, PSR2 and Panel Replay

- Lunar Lake display (display version 20) supports PSR1, PSR2 with selective update/fetch, and **eDP 1.5 Panel Replay**, if the panel advertises it. The **default follows the VBT**: `psr_global_enabled()` returns `vbt.psr.enable` for eDP ([`intel_psr.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/intel_psr.c)). Whether this panel enters PSR1, PSR2 or Panel Replay is **[verify]**: read debugfs (§8).
- Why it matters: PSR exit or re-sync on the first update after idle can add up to about a frame of latency. That delay belongs to "what users get" but not to the app. Report 04 (D-B 14) already treats this as a variant.
- **Controls** (from [`intel_display_params.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/intel_display_params.c)):
  - `xe.enable_psr=0` disables PSR (0 = off, 1 = up to PSR1, 2 = up to PSR2; −1 = per-chip default).
  - `xe.enable_panel_replay=0` disables Panel Replay.
  - `xe.enable_psr2_sel_fetch=0` disables PSR2/Panel Replay selective fetch.
  - At runtime without a reboot: `echo 0x1 > /sys/kernel/debug/dri/<dev>/i915_edp_psr_debug` (`I915_PSR_DEBUG_DISABLE`), or `0x40` for `PANEL_REPLAY_DISABLE`. Check the result in `i915_edp_psr_status`. The debugfs files keep the `i915_` names under xe **[verify path on the unit]**.
  - Lunar Lake laptops with the same flicker fixed by `xe.enable_psr=0`: [Zorin forum, Dell Pro 14 Plus](https://forum.zorin.com/t/dell-pro-14-plus-pb14250-intel-lunar-lake-fix-for-lid-open-screen-flicker-on-zorin-os-using-xe-enable-psr-0/62785). Lenovo documents PSR-off as the flicker fix for Gen 10 and 11 ([Lenovo HT516438](https://support.lenovo.com/us/en/solutions/ht516438-disable-panel-self-refresh-to-avoid-screen-flickering-thinkpad-x1-carbon-11th-gen-thinkpad-x1-yoga-8th-gen)).
- Other notes:
  - The driver **disables PSR whenever VRR is enabled** (`_psr_compute_config`), which matters only on the VRR panel variant.
  - PSR2 selective fetch is **not used while async flips are active**: it falls back to full-frame updates.
  - Panel Replay has had eDP quirks in 2026: a Dell XPS 16 panel updating at about 5 fps ([intel-gfx patch](https://ratatoskr.run/intel-gfx/2026/07/17230977/t)), and Panther Lake OLED tearing and corruption with selective fetch ([report](https://ratatoskr.run/intel-gfx/2026/08/17466425/t)).
  - The X1 Gen 13's Samsung panel is not in those quirk lists as far as found. If the "fair" floor ever tears or stalls on the internal panel, suspect PR/SU first.
- **Proposed default for headline sessions on this DUT:** `xe.enable_psr=0 xe.enable_panel_replay=0` on the kernel command line, recorded in the manifest. A separate "as shipped" PSR-on variant is optional (D-B 14).

### 2.3 VRR

No VRR on the ATNA40YK20-0 (§1), so `VRR_ENABLED` stays 0 and nothing is lost. If the unit turns out to be the 500-nit VRR panel, keep VRR off for the floors. Report 04 already fixes `VRR_ENABLED=0`; note also that the driver disables PSR while VRR is on.

### 2.4 Known platform issues

1. **CPU stuck at 400 MHz.**
   - Out of the box on Ubuntu 25.04 and Fedora 42. The workaround is the **`performance` ACPI platform profile** ([Phoronix review](https://www.phoronix.com/review/lenovo-thinkpad-x1-gen13-linux); [Phoronix LNL Linux vs Windows](https://www.phoronix.com/review/lunarlake-xe2-windows-linux-2025)).
   - Separately, all cores can **intermittently stall at about 400 MHz for seconds to tens of seconds after s2idle sleeps longer than about 3 h**. It traces to PL1 averaging state after resume and was **unresolved as of 2026-09-07** ([platform-driver-x86 report, X1C Gen 13 / 258V / Fedora 44](https://ratatoskr.run/platform-driver-x86/2026/09/17526315)).
   - This is a direct threat to a latency study: it produces a bimodal session. Mitigation: **cold boot before each session, never resume from suspend**, and log `scaling_cur_freq` and the RAPL PL1-limited residency during trials (§6).
2. **Internal panel flicker with an external monitor attached**, reported by owners. PSR-off did not fix it for everyone; kernel 6.15.9 did for one owner ([Lenovo forum](https://forums.lenovo.com/t5/Lenovo-Yoga-Series-Laptops/X1-Carbon-Gen13-Laptop-monitor-flickers-when-external-monitor-connected/m-p/5384568)). For the 240 Hz external sessions, **turn the internal panel off** (close the lid or disable eDP in KMS). That also frees a display pipe and bandwidth.

## 3. Compositor bypass on this hardware

### 3.1 Bare KMS from a VT (`floor-kms`)

- Stop the display manager (`systemctl isolate multi-user.target`) and run `floor-kms` on a VT as DRM master.
- xe exposes **async flips on the primary plane** through both the legacy page-flip ioctl and the **atomic API with `DRM_MODE_PAGE_FLIP_ASYNC`**. Atomic async is DRM-core support since 6.8; only `FB_ID` and `IN_FENCE_FD` may change, and any other property change is rejected ([`drm_atomic_uapi.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_atomic_uapi.c); [atomic async series](https://lkml.iu.edu/hypermail/linux/kernel/2310.2/02478.html)).
- On display version ≥ 12, `tgl_plane_can_async_flip` allows **LINEAR**, X-, Y- and 4-tiled buffers, and LNL CCS ([`skl_universal_plane.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/skl_universal_plane.c)). So `floor-kms` can use dumb (linear) buffers for the "true" floor.
- Caveats from [`intel_display.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/intel_display.c):
  - **The first async flip is turned into a sync flip.** Discard it as warm-up.
  - Stride, modifier, format and size must not change between the two buffers.
  - **Async flips are rejected when the pipe joiner is in use.** This rules out 4K240 externally (§4).
  - **Async flip-done event timestamps "correspond to the last vblank and have no relation to the actual time"** the flip happened. `floor-kms --log` self-checks (04 §8, attribution session) must not treat async flip timestamps as photon-time proxies. The photodiode is the only truth for the true floor.
  - The "fair" floor (sync atomic flip, no compositor) needs nothing special.

### 3.2 Wayland

- **KWin (Plasma 6):**
  - Supports `wp_tearing_control_v1` with atomic modesetting on kernel ≥ 6.8. Tearing is gated by a per-output "allow tearing" option in the display settings (KScreen) and happens only when the fullscreen window is directly scanned out.
  - KWin falls back to synchronous presentation for any frame where it changes colour or other KMS properties ([KWin MR !4800](https://invent.kde.org/plasma/kwin/-/merge_requests/4800)).
  - Direct scanout for fullscreen clients is on by default; `KWIN_DRM_NO_DIRECT_SCANOUT=1` disables it ([KWin MR !502](https://invent.kde.org/plasma/kwin/-/merge_requests/502)). That is useful for a forced "composed" variant.
  - Direct scanout is blocked by anything that makes KWin transform the buffer: fractional scaling, ICC or colour profiles, Night Light, HDR, rotation, overlapping OSDs or notifications, and screen capture ([example of the scaling/ICC constraint](https://github.com/Bitbitbet/kwindirectscanouthelper)).
  - **On this panel, set scale to an integer (200% or 100%)** and disable Night Light and ICC profiles for fair and true variants. The GNOME/KDE default of 175% or 150% scaling on a 2.8K 14" screen would silently kill direct scanout.
- **GNOME Mutter:** unredirects (direct-scans) fullscreen clients, but **tearing-control / async flip is still an unmerged draft** ([mutter !3797](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/3797); [issue #2517](https://gitlab.gnome.org/GNOME/mutter/-/issues/2517)). A GNOME "true" floor is therefore impossible, which supports **KWin as the pinned compositor (D-B 12)**.
- **Buffers:** clients must hand the compositor **dmabuf** buffers in a format and modifier the primary plane accepts. `wl_shm` buffers get composited. This matches 04 §1.5.

## 4. External monitor for the 240 Hz variant

- **Ports:** 2× Thunderbolt 4 / USB4 40 Gbps USB-C (left side, DP alt mode), 1× HDMI (right side), 2× USB-A 5 Gbps. **HDMI is limited to 4K@60** per PSREF, so it is **unusable for 240 Hz**. Thunderbolt carries up to 8K@60, and ">60 Hz supported at reduced max resolution". Lunar Lake drives the internal panel plus two external displays ([PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_X1_Carbon_Gen_13_Aura_Edition/ThinkPad_X1_Carbon_Gen_13_Aura_Edition_Spec.PDF)). The Lenovo Linux guide lists USB-C to DP 1.4 up to 5120×3200@60 and DP 2.1 up to 7680×4320@60 ([Linux UG](https://download.lenovo.com/pccbbs/mobiles_pdf/x1_carbon_gen13_2in1_gen10_linux_ug.pdf)).
- **Per-pipe limit:** Lunar Lake's max CDCLK is 652.8 MHz at 2 pixels/clock, which gives a **max dot clock per pipe of about 1,306 MHz** ([`intel_cdclk.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/intel_cdclk.c)):
  - **1920×1080@240:** about 0.5–0.6 GHz. Single pipe, no DSC needed on HBR3×4. OK.
  - **2560×1440@240:** about 0.95–1.0 GHz (CVT-RB2). Single pipe; about 22 Gbps at 8 bpc fits HBR3×4 (25.9 Gbps) without DSC. **Recommended.** Use the same monitor as the desktop 240 Hz sessions.
  - **3840×2160@240:** about 2.1 GHz, which **needs the pipe joiner, and xe rejects async flips with the joiner**, so there is no "true" floor. It also needs DSC or UHBR. **Avoid.**
- **Cabling:**
  - Use a **direct USB-C to DisplayPort cable with 4-lane DP alt mode** into the monitor's DP input.
  - Avoid docks, MST hubs and monitors whose USB-C input also runs USB 3 data. USB 3 data drops DP to 2 lanes, and HBR3×2 (13 Gbps) cannot carry 1440p240 uncompressed.
  - MST adds a branch device and forces modes through the MST topology manager. DSC adds a small, fixed decode delay and is a configuration difference from the desktop. Record `DSC on/off` and link rate and lane count in the manifest (see `i915_dp_*` / `i915_dsc_fec_support` in debugfs **[verify names under xe]**).
- Turn the internal eDP **off** during external sessions (§2.4).

## 5. USB and the rig

- The platform has two xHCI controllers: the **Lunar Lake-M USB 3.2 xHCI [8086:a87d]** and the **Thunderbolt 4 (TCSS) xHCI [8086:a831]** ([hardware probe lspci](https://linux-hardware.org/?probe=0b8d80807b)).
- The same probe shows an **internal Genesys Logic USB 2.0 hub (05e3:0610)** on one root bus, next to the Chicony webcam and the Goodix fingerprint reader. Some ports or internal devices hang off a hub.
- **Recommendation:** plug the rig in and run `lsusb -t` for **each** of the four ports. Use a port where the Teensy (480 Mbit/s high-speed) appears **directly under a root hub**, not under `05e3:0610`.
  - The Thunderbolt/USB-C ports on the TCSS xHCI should be root-direct, using a plain C-to-micro-B or C-to-C cable with no hub.
  - Keep the other USB-C port for power (65 W PD) and the external monitor.
  - Which USB-A port, if any, is root-direct is **[verify]**. The PSREF marks the right USB-A as "Always On", which suggests a different power path, not a hub.
- **Polling:**
  - Linux usbhid honours the descriptor's `bInterval`. Check that no `usbhid.kbpoll` or `usbhid.mousepoll` overrides are set: `cat /sys/module/usbhid/parameters/*`.
  - Set `power/control=on` for the rig's device to rule out autosuspend. Open HID devices normally stay active, but be explicit.
  - Measure the effective poll rate with `usbmon` during bring-up, as 01 already plans.
  - No Lunar Lake-specific xHCI polling quirk was found.

## 6. Using a personal laptop as a clean DUT

| Control | Recommendation |
| --- | --- |
| **Install** | A **separate OS install, not a separate user.** Kernel command line, compositor config, systemd units, Chrome policy, `power-profiles-daemon` state and firmware settings are all system-wide. Options: (a) a second partition on the internal SSD (dual boot, alongside Windows 11 if kept, which gives Windows vs Linux on identical hardware); or (b) a **USB4/TB4 NVMe enclosure** booted as the DUT OS, which leaves the personal install untouched. (b) adds PCIe tunnelling traffic on the TCSS during measurement, so prefer (a) for headline data **[judgement]** |
| **Pinning** | Pin the kernel, `linux-firmware` (xe GuC/HuC/DMC blobs), Mesa, KWin/Plasma, and Chrome (Chrome for Testing or a policy-frozen stable, D-B 11). Pin the BIOS/EC version: disable fwupd auto-updates on the DUT OS and record the BIOS version. Keep the personal OS from updating the shared UEFI firmware between calibration days (fwupd or Lenovo Vantage on Windows will do this). |
| **Power** | On AC; Notebookcheck measured about 8% less CPU performance on battery. ACPI `platform_profile=performance` (Phoronix 400 MHz workaround). `power-profiles-daemon` set to `performance`, **or** TLP, never both. Leave `thermald` as the distro ships it and record its state. Optional battery charge threshold via `thinkpad_acpi` (`charge_control_end_threshold`) for long plugged sessions. **Cold boot before each session; no suspend/resume** (§2.4). |
| **Lunar Lake topology** | 4 Lion Cove P-cores + 4 Skymont LP E-cores, no SMT. Input, compositor and Chrome threads may be scheduled on the LP E-cores at low clocks, which widens the tail. Headline: leave the scheduler as shipped but log per-trial CPU frequency. Optional attribution variant: `cpu_dma_latency` PM QoS = 0, or pin the floor app to P-cores, and compare. |
| **Quiet system** | Do Not Disturb on; disable Baloo/Tracker indexing, `packagekit`/`dnf-makecache`/`unattended-upgrades` timers, `fwupd-refresh`, and browser sync. Airplane mode, or at least no Wi-Fi scanning, during blocks (04 already plans the SSH quiet period). Disable the webcam, fingerprint and IR presence sensing if exposed; this model has Human Presence Detection hardware (PSREF), though it is unsupported on Linux. |
| **Display** | SDR; 100% brightness (§7); auto-brightness off; Night Light off; integer scaling; KWin HDR off; screen blanking and lock off during blocks. |
| **Manifest additions** | Panel EDID string (`ATNA40YK20-0`), mode (60/120), raw backlight value (`/sys/class/backlight/*/brightness` and `max_brightness`), `i915_edp_psr_status` output, `platform_profile`, ppd profile, per-trial `scaling_cur_freq` summary, RAPL PL1-limited counter, uptime since cold boot, BIOS and EC versions, xe GuC/DMC firmware versions (from dmesg). |

## 7. What this means for the spec and Phase 0

### 7.1 Matrix coverage

| Spec role | Can the X1 cover it? |
| --- | --- |
| Reference desktop, Windows, 60/240 Hz (headline) | **No.** The headline is defined on the desktop, so the X1 can be the *bring-up* DUT, but calibration must still be repeated on the reference desktop. |
| "Same desktop, dual boot": Linux Wayland and bare KMS; R6 direct scanout; compositor comparison | **Partly.** It fully covers bare KMS, KWin direct scanout and tearing, and the compositor comparison, on its own panel and on an external 240 Hz monitor. What is lost is **OS vs OS on the same hardware *as the desktop***. Remedy: dual-boot **the X1** with its shipped Windows 11, which restores same-hardware Windows vs Linux on both the OLED and the external monitor. Or keep the desktop dual boot as well. |
| MacBook Pro "second OS; laptop panel" | Adds a **second laptop panel** (OLED, 60/120 Hz fixed, no VRR). Complements the MacBook's mini-LED ProMotion; does not replace it. |
| Budget laptop "low-end stress" | **No.** A 2024–25 flagship ultrabook is the opposite of the "lazy tax" machine. |
| 240 Hz variant | **External monitor only**, over TB4 → DP. 1440p240 or 1080p240; not 4K240 (§4). Ideally the *same* monitor as the desktop, so the display is held constant across machines. |

### 7.2 Risks this device adds

1. **240 Hz OLED modulation**, much lower in frequency than the MacBook case.
   - With 33% amplitude at 100% brightness, the photodiode on a white marker sees a ripple of about ⅓ of full scale with a **4.17 ms period**. That is longer than the panel's 1–2 ms transition.
   - **Consequences for detection (changes 01 §2 and 02's filter):**
     - **Do not low-pass across a PWM period.** A 4.17 ms boxcar would add about 2 ms of delay and smear the edge. Keep only a short (≤ 50 µs) noise FIR. 100 kS/s is far more than enough; aliasing is not the issue, ambiguity is.
     - **Rising edge (black to white):** OLED black emits nothing, so the baseline has no ripple and "first change" is clean. But a pixel that switches on during a modulation low will come up at the trough level. At 100% brightness light never goes fully off (33% depth), so the rise is not hidden. **Below 50% brightness the PWM is deeper, and first-photon could be delayed by up to the off-phase.** Run at **100%**, which also sits in the DC-dimming region.
     - **Falling edge (white to black):** a "10% below the white level" threshold will be **tripped by every PWM dip**. Define the fall threshold relative to the **trough envelope** measured during the static-white flicker characterisation, e.g. below 50% of the trough level, with hysteresis. The alternative is envelope (peak-hold over one 4.17 ms period) detection, which costs a period of latency and is only acceptable for the 50% metric, not first-change.
     - The **flicker characterisation must be per refresh mode (60 and 120 Hz)** and per brightness, and must record the phase of the dips relative to vblank if they are phase-locked (§1).
2. **ABL (automatic brightness limiting, from average picture level).**
   - The marker's white level depends on how much of the screen is lit. The EDID's 617-nit peak against 400-nit full-field shows the panel limits large bright areas.
   - A floor app with a black background plus a white marker gives peak marker luminance. R1's light-themed page will make the same marker **dimmer**, and changing list contents can modulate it slightly.
   - Relative thresholds (per session, from the pre-trial static levels) handle the level. Also log the white plateau per trial to detect ABL drift. The floor-vs-rung comparison must use per-block levels, not a per-display constant.
3. **Burn-in protection.**
   - Lenovo's OLED tools (pixel shift, pixel refresh, "Display Refresh") are Windows Vantage utilities. Pixel refresh is limited to certain models, and no panel-firmware pixel orbiting is documented for the X1 Carbon Gen 13 ([PCWorld](https://www.pcworld.com/article/2918628/your-oled-displays-worst-enemy-burn-in-heres-how-to-fight-back.html)).
   - **Risk on Linux is low but unverified.** Even a 4 px orbit (~0.4 mm) would not uncover a BPW34 behind a 64 px (6.7 mm) marker, but it would shift the marker's scanout row by 4 lines, which is negligible.
   - **Test on calibration day 1:** hold the marker white for 10 minutes and log luminance drift (catches TCON static-content dimming as well as ABL). Photograph the marker edges at the start and end.
   - Do **not** run Windows' or Vantage's OLED features on a shared dual-boot install before sessions, because they can change the panel state.
   - Burn-in on the owner's personal screen: 500+ trials × many variants of a static marker in the same spot for hours per session. Alternate its polarity, keep sessions bounded, and use dark-mode desktops between blocks.
4. **Fast scanout / long vblank at 60 Hz** (§1): the "60 Hz" floor on this laptop is not equivalent to 60 Hz on the desktop monitor. Report it as its own row.
5. **400 MHz stalls** (§2.4) can make a whole session bimodal: cold boot, performance profile, per-trial frequency logging.
6. **Personal-device drift:** firmware and OS updates between day 1 and day 2 through the personal OS. Freeze or record the BIOS and EC versions and check them in the manifest before rung sessions.

### 7.3 Suggested README and spec changes

- **Machine matrix:** add "ThinkPad X1 Carbon Gen 13 (OLED 60/120 Hz fixed; Linux + Windows dual boot; external 1440p240 via TB4)". Either keep the desktop dual boot for "same hardware as the headline", or explicitly re-scope the Linux role to "X1, dual boot on the X1".
- **S1/S2 (flicker):** extend beyond the MacBook to *low-frequency OLED modulation at 240 Hz, present at 100% brightness*. The filter spec must not assume the ripple is fast compared with the edge. Add "fall threshold relative to trough envelope" to D-A 6 (photon edge definition).
- **S3 / marker contract:** add a **minimum physical marker size** (e.g. ≥ 10 mm, which is 128 device px on this 243 PPI panel), plus integer scaling as a requirement for fair and true variants.
- **D-B 12:** the evidence favours KWin, since Mutter has no tearing-control.
- **D-B 14 (PSR):** for this DUT, default `xe.enable_psr=0 xe.enable_panel_replay=0` for headline data, with PSR-on as a variant.
- **04 §8 (attribution):** async-flip event timestamps on xe are vblank-based and not photon proxies.
- **240 Hz sessions:** avoid 4K240 on Intel iGPUs (joiner blocks async flip). Use 1440p240.
- **Top risks:** add "Lunar Lake 400 MHz stalls after resume" and "personal-device firmware drift".
- **Calibration day 1 list (week 4):** "Linux" becomes "X1 internal 60 and 120 + X1 external 240".

## 8. Bring-up checklist (about half a day on the unit)

```sh
# Panel identity and modes
sudo cat /sys/class/drm/card*-eDP-1/edid | edid-decode | grep -E "Alphanumeric|DTD|Range"
drm_info | grep -A3 -i "vrr_capable"            # expect 0 on ATNA40YK20-0
# Driver and firmware
lspci -k -s 00:02.0 ; uname -r ; glxinfo -B | grep -i "OpenGL version"
sudo dmesg | grep -iE "xe .*(GuC|HuC|DMC)"
# PSR / Panel Replay state (path may be dri/0 or dri/0000:00:02.0)
sudo cat /sys/kernel/debug/dri/*/i915_edp_psr_status
cat /sys/module/xe/parameters/enable_psr /sys/module/xe/parameters/enable_panel_replay
# CPU / power
cat /sys/firmware/acpi/platform_profile ; powerprofilesctl get
grep MHz /proc/cpuinfo | sort | uniq -c
# USB topology: rig must sit directly under a root hub
lsusb -t ; cat /sys/module/usbhid/parameters/*
# External monitor link (under xe the debugfs names may differ)
sudo ls /sys/kernel/debug/dri/*/ | grep -iE "dp|dsc|link"
```

Then, with the rig: static white and static black flicker traces at 60 and 120 Hz and at 100%, 75% and 40% brightness; a 10-minute static-white drift test; fair and true KMS floors, with the first async flip discarded.

## Sources

- Notebookcheck review (EN): https://www.notebookcheck.net/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-laptop-review-The-X1-Carbon-is-finally-back.924998.0.html
- Notebookcheck review (DE): https://www.notebookcheck.com/Lenovo-ThinkPad-X1-Carbon-Gen-13-Aura-Edition-Laptop-Test-Das-X1-Carbon-ist-endlich-back.932285.0.html
- Lenovo PSREF, X1 Carbon Gen 13 Aura Edition (2026-08-28): https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_X1_Carbon_Gen_13_Aura_Edition/ThinkPad_X1_Carbon_Gen_13_Aura_Edition_Spec.PDF
- Lenovo Linux user guide: https://download.lenovo.com/pccbbs/mobiles_pdf/x1_carbon_gen13_2in1_gen10_linux_ug.pdf
- Hardware probe (EDID, lspci, lsusb) of an X1C Gen 13 21NS: https://linux-hardware.org/?probe=0b8d80807b
- ArchWiki: https://wiki.archlinux.org/title/Lenovo_ThinkPad_X1_Carbon_(Gen_13)
- Phoronix Linux review: https://www.phoronix.com/review/lenovo-thinkpad-x1-gen13-linux · LNL Linux vs Windows (400 MHz): https://www.phoronix.com/review/lunarlake-xe2-windows-linux-2025
- 400 MHz after s2idle bug report (2026-09-07): https://ratatoskr.run/platform-driver-x86/2026/09/17526315
- Xe2 default in 6.12: https://www.phoronix.com/forums/forum/linux-graphics-x-org-drivers/intel-linux/1488256-intel-enables-xe2-lunar-lake-battlemage-graphics-by-default-with-linux-6-12 · Mesa 24.2.2: https://www.phoronix.com/news/Mesa-24.2.2-Released
- Kernel source (master): `intel_display_params.c`, `intel_psr.c`, `intel_display.c`, `skl_universal_plane.c`, `intel_cdclk.c`, `drm_atomic_uapi.c` under https://github.com/torvalds/linux/tree/master/drivers/gpu/drm
- Panel Replay quirk (2026-07): https://ratatoskr.run/intel-gfx/2026/07/17230977/t · PTL OLED PR/SU tearing (2026-08): https://ratatoskr.run/intel-gfx/2026/08/17466425/t
- `xe.enable_psr=0` on Lunar Lake: https://forum.zorin.com/t/dell-pro-14-plus-pb14250-intel-lunar-lake-fix-for-lid-open-screen-flicker-on-zorin-os-using-xe-enable-psr-0/62785
- Lenovo PSR-off guidance (earlier gens): https://support.lenovo.com/us/en/solutions/ht516438-disable-panel-self-refresh-to-avoid-screen-flickering-thinkpad-x1-carbon-11th-gen-thinkpad-x1-yoga-8th-gen
- Owner flicker thread: https://forums.lenovo.com/t5/Lenovo-Yoga-Series-Laptops/X1-Carbon-Gen13-Laptop-monitor-flickers-when-external-monitor-connected/m-p/5384568
- KWin tearing with atomic (MR !4800): https://invent.kde.org/plasma/kwin/-/merge_requests/4800 · direct scanout (MR !502): https://invent.kde.org/plasma/kwin/-/merge_requests/502
- Mutter async flip draft (!3797): https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/3797 · issue #2517: https://gitlab.gnome.org/GNOME/mutter/-/issues/2517
- OLED burn-in mitigations: https://www.pcworld.com/article/2918628/your-oled-displays-worst-enemy-burn-in-heres-how-to-fight-back.html
