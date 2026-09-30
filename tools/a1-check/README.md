# A1 on-device check

This is task **A1** from [`docs/phase-a/README.md`](../../docs/phase-a/README.md). It is a short, scripted check that runs on the owner's own machine. It confirms the **[verify]** assumptions that the Phase A harness depends on, before anyone builds the harness.

- It takes about 3–5 minutes, most of it hands-off.
- It needs Python 3 (standard library only) and Google Chrome.
- It works on the ThinkPad X1 Carbon (KDE Plasma on Wayland) **or** the MacBook Air.
- At the end it prints a PASS / FAIL / UNKNOWN table and writes a JSON report for you to send back.

## What it does

1. It starts a small local web server on `127.0.0.1`. The server sends the test page with cross-origin-isolation headers (COOP/COEP).
2. It opens a **separate** Chrome window with a fresh temporary profile. This does not touch your normal Chrome profile. No sandbox-weakening flags are used.
3. It creates a virtual keyboard through the real OS input path:
   - **Linux:** `/dev/uinput` → kernel → libinput → KWin → Chrome.
   - **macOS:** Quartz `CGEventPost` → WindowServer → Chrome.
4. It measures from inside the page, with no DevTools client attached during the measurements.
5. It types about **150 lowercase letters** into a text box on the test page. Keys are 50–250 ms apart. It never presses Enter, modifier keys or Backspace.
6. If the Chrome window loses focus while typing, the check stops typing at once.

## Before you start (both machines)

- Plug in the power adapter.
- Close other apps.
- Turn on Do Not Disturb, or turn notifications off.
- Keep the laptop awake and don't let it sleep.
- When the script asks, **click once inside the Chrome window titled "A1 check"**. After that, **don't touch the keyboard or mouse** until the result table appears. The page shows a countdown before typing starts.
- **Don't** run the whole script with `sudo`. Chrome must run as you.

## Linux (ThinkPad X1 Carbon, KDE Plasma / Wayland)

**One-time permission.** The script needs write access to `/dev/uinput`. The quickest fix lasts until you reboot:

```sh
sudo modprobe uinput                      # only if /dev/uinput does not exist
sudo setfacl -m u:$USER:rw /dev/uinput
```

You can skip that step. Instead, add `--sudo-injector` to the run command. Then only the small key-injector process runs under `sudo`, and it asks for your password once. For a permanent udev rule, see what the script prints when it can't open `/dev/uinput`.

**Run:**

```sh
git clone <repo> light-ux && cd light-ux
python3 tools/a1-check/a1_check.py --label 120hz
```

**60 vs 120 Hz.** The panel can run at 60 Hz or 120 Hz, and the plan measures both. If you can, run the check once at each rate:

1. Set the refresh rate in *System Settings → Display & Monitor → Refresh rate*.
2. Run the check again with the matching label, for example `--label 60hz`.

The script also estimates the refresh rate itself from `requestAnimationFrame` intervals, so a wrong label is caught.

**Chrome location.** The script looks for `google-chrome-stable`, `google-chrome` and `/opt/google/chrome/chrome` in that order. If Chrome is elsewhere, pass the path with `--chrome /path/to/chrome`.

## macOS (MacBook Air)

**One-time permission.** macOS must allow your terminal app to send key presses:

1. Run the command below once. The script asks macOS to show its permission prompt, then stops.
2. Open *System Settings → Privacy & Security → Accessibility*. Turn **on** the app you run the script from (Terminal, iTerm, VS Code, …). If it is not listed, add it with **+**. Terminal is in `/Applications/Utilities`.
3. **Quit that app completely (Cmd-Q) and reopen it.** Then run the command again.

You can turn the permission off again after the check.

**Run:**

```sh
git clone <repo> light-ux && cd light-ux
python3 tools/a1-check/a1_check.py --label mac
```

- If `python3` offers to install the Command Line Tools, accept. The system Python 3.9 is enough.
- The script looks for Chrome in `/Applications/Google Chrome.app`. If it is elsewhere, pass the path with `--chrome`.
- The script tries to bring the test window to the front. If that doesn't work, click the window.

## What to send back

At the end, the script prints the path of the report, for example:

```
tools/a1-check/reports/a1-linux-120hz-20261001-101500.json
```

Send that file, one for each run. Pasting the printed table as well is helpful but optional.

The report contains:

- timings
- Chrome's version and GPU info
- OS, kernel and display information (`kscreen-doctor -o` on KDE)
- the Chrome command line

It contains no personal files, no browsing data and no hostname. The `reports/` folder is git-ignored.

## The rows

Each row tests one assumption from the Phase A plan. The § numbers refer to [`docs/phase-a/README.md`](../../docs/phase-a/README.md).

**Statuses:**

| Status | Meaning |
| --- | --- |
| PASS | The assumption holds on this machine |
| FAIL | The assumption does not hold; the evidence says why |
| UNKNOWN | The row could not be measured. Usually an earlier step failed, for example there was no injector permission or focus was lost |
| INFO | A measured fact that the plan does not assume either way |

**Page and timer:**

| Row | What it checks | Plan § |
| --- | --- | --- |
| `coi` | `crossOriginIsolated === true` | §1.3 |
| `timer_res` | `performance.now()` steps are about 5 µs | §1.3 |
| `env` | Chrome version, platform, DPR, refresh rate estimated from rAF, and, on Linux, the ozone platform | §4.2 |
| `wayland` | Linux only: Chrome runs natively on Wayland (`--ozone-platform=wayland` on its child processes), not on XWayland | §0.5 |

**Element Timing.** These rows use 30 marker flips for each of 4 probe variants, with no input.

| Row | What it checks | Plan § |
| --- | --- | --- |
| `et_every_flip` | Every freshly inserted `<span elementtiming>` in the marker gets its own entry. This includes repeated flips and elements inserted after load | §0.4, §1.1 |
| `et_presentation` | Entries have `presentationTime`, not only `renderTime` or `paintTime`. The fields that actually carry values are listed | §0.3/§0.4 |
| `et_present_minus_paint` | `presentationTime − paintTime` is 0–2 frames | §1.1 |
| `et_same_frame` | `paintTime` lands inside the frame whose rAF ran just after the flip, so the probe times the marker's own frame | §0.4/§4.4 |
| `et_resolution` | Times are not coarsened to 4 ms; it prints the coarsest grid each field sits on | §0.4/§1.3 |
| `et_same_colour` | A glyph in the marker's own colour, so invisible to a photodiode, still gets an entry | §4.4 |
| `et_inline_span` | A plain inline `<span>` gets **no** entry, so the probe span must be `inline-block` or `block`. See the dev-container finding below | §4.4 |
| `et_reuse` | Info: whether changing the text of one reused span reports again | §1.1 |

**Event Timing.** One key in five blocks the page for 20 ms, so that entries are emitted. Those keys are left out of the floor.

| Row | What it checks | Plan § |
| --- | --- | --- |
| `evt_start_eq_ts` | Entry `startTime` equals the page's own `event.timeStamp` | §1.1 |
| `evt_duration_8ms` | `duration` is a multiple of 8 ms | §0.3 |
| `evt_no_presentation` | `PerformanceEventTiming` has no `paintTime` or `presentationTime`. A FAIL here is good news: it means the plan's claim is outdated | §0.3 |

**Injector clock, delivery and floor:**

| Row | What it checks | Plan § |
| --- | --- | --- |
| `clock_sync` | NTP-style WebSocket exchange before and after typing. It passes if the minimum round trip is under 1 ms and the offset drifts less than 0.2 ms | §3.4/§2.5 |
| `keys_delivered` | Every injected key reached the page, matched by `KeyboardEvent.code` and time | §3.3 |
| `os_delivery` | Injector time → `event.timeStamp` is non-negative and small, 0.05–5 ms at p50. This is the "OS delivery segment" | §3.2 |
| `ts_semantics` | What `event.timeStamp` means on this OS (details below) | §0.2 |
| `floor` | Injector time → the Element Timing `presentationTime` of the marker flip, as p50/p95/p99 in ms and in frames. This is the first browser floor for this machine. Slow keys are excluded | §1.1/§3.4 |
| `injector_jitter` | Injector scheduling error, p99 under 0.2 ms | §3.3 |

How `ts_semantics` decides:

- **Linux:** The plan says Chrome ignores the compositor's whole-millisecond timestamp and stamps the event when it reads it. If that is true, the sub-millisecond phase of `event.timeStamp` on `CLOCK_MONOTONIC` is uniform, so its concentration R is near 0. If Chrome used the compositor's timestamp instead, R would be near 1.
- **macOS:** Half the keys are posted 4 ms after the `CGEvent` is created. If Chrome uses the OS event timestamp, `event.timeStamp` moves 4 ms earlier for those keys. This row is INFO, because the plan makes no claim for macOS.

## What was checked in the dev container, and what was not

The tool was developed in a Linux container with no display, using headless Chromium 141 and `--simulate`. In simulate mode, keys go through CDP `Input.dispatchKeyEvent` instead of the OS. **That is not a real test:** it skips the whole OS path, and CDP input is stamped when the DevTools message arrives.

**Tested in the container:**

- The server with COOP/COEP.
- The page and its command channel.
- WebSocket clock sync: minimum round trip 0.3–0.4 ms, drift about 0.02 ms.
- Element Timing variants.
- Event Timing.
- The analysis and the JSON report.
- The injector's parent/child protocol, with a fake uinput device.
- The uinput ioctl numbers and struct packing, checked against the kernel headers' values.
- The macOS ctypes signature table.
- Python 3.9–3.13.

Run the unit tests with `python3 tools/a1-check/test_a1.py`.

**Findings already made in the container (Chromium 141):**

- A plain inline `<span elementtiming>` is **never** reported. An `inline-block` or `block` span is reported every time, including when it is inserted repeatedly after load. The reason is that Chrome attributes text paint to the containing block. **So the marker probe in phase-a §4.4 must use `display: inline-block` or `block`.**
- A same-colour (invisible) glyph *is* reported.
- Changing the text of a reused span reports only once.
- Chromium 141 has no `paintTime` or `presentationTime` on Element Timing entries. Those fields shipped in Chrome 144/145, and the owner's Chrome should have them.

**Not tested here, because it needs the real machines:**

- Real `/dev/uinput` device creation, and KWin picking up the device.
- Quartz posting and the Accessibility check.
- Focus handling with a real window manager.
- Wayland detection.
- Real presentation times.

## Options

```
--label TEXT       tag for this run (60hz / 120hz / mac)
--chrome PATH      Chrome binary if not auto-detected
--sudo-injector    Linux: run only the uinput injector under sudo
--keys N           number of injected keys (default 150)
--no-inject        page checks only (rows 4-6 become UNKNOWN)
--simulate         DEV ONLY: CDP keys, headless if no display; NOT a real test
```
