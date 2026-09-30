# orchestrator: `ladder soft`, the Phase A software measurement harness

This directory holds the `ladder` Python package. Phase A adds `ladder soft`, the harness specified in
[`docs/phase-a/README.md`](../docs/phase-a/README.md) (§1–§4, §7). It measures the web rungs on the
owner's laptop with software instrumentation only:

- It injects real OS keystrokes: uinput on Linux, Quartz on macOS.
- For each keystroke it takes the in-page Element Timing `presentationTime` of the frame in which the
  latency marker flipped.
- It maps the injector clock into the page clock and writes raw data plus a manifest.
- `ladder soft report` turns one or more sessions into a self-contained HTML report with the Gate A table.

The blind playground (`playground/`) is a separate tool. The report reads its results file (see
[Perceptibility input](#perceptibility-input)).

## Layout

```
orchestrator/
  pyproject.toml, uv.lock      uv project; no required dependencies (extras: parquet, trace)
  ladder/cli.py                `python3 -m ladder soft run|plan|analyze|report|stages|selftest`
  ladder/soft/
    runner/                    runs on the device under test; standard library only, Python >= 3.9
      session.py               the run loop: plan -> servers -> Chrome per block -> segments -> manifest
      schedule.py              seeded plan (ABBA block order, A.first_key / A.clear / A.seq, jitter)
      injector.py              settle-aware key executor; uinput / Quartz child process; CDP (simulate)
      injector_main.py         child entry point (so only it can run under sudo)
      server.py                one origin per rung, COOP/COEP, collector injection, WebSocket, next proxy
      chrome.py                find / version floor / flag allow- and denylist / launch / kill
      trace.py                 T sessions: CDP Tracing with a hand-encoded Perfetto TraceConfig
      web/collector.js         page-side collector: settle, focus, clock sync, buffer read-back
      clock.py cdp.py uinput.py quartz.py envinfo.py   vendored from tools/a1-check/a1lib (see headers)
      keymap.py                a1lib keymap + digits, space, Backspace
    timing.py                  clock mapping, key matching, coalesced-key attribution, trial rows
    manifest.py                session manifest schema + validator
    analysis/                  standard library only (pyarrow and perfetto optional)
      build.py                 raw -> derived/trials.csv (+ .parquet), segments.json, summary.json
      correctness.py           flips vs parity/ reference ranker (strict / tie / set levels)
      stats.py                 type-7 quantiles, bootstrap (BCa / percentile / moving-block), ratios
      gate.py                  Gate A rule, perceptibility input
      report.py, svg.py        HTML report with inline SVG charts
      stages.py                T sessions: trace_processor -> derived/stages.csv
  tests/                       python3 -m unittest discover -s tests
  results/                     session output (git-ignored)
```

The runner and the analysis share one code path for timing extraction (`ladder/soft/timing.py`). The
runner uses it for its preflight checks on the device, and the analysis uses it to build `trials.csv`.

## Install

The whole package runs on a bare `python3` (3.9 or later, standard library only), so the owner needs
nothing beyond what the A1 check already needed. Run it from this directory:

```sh
cd light-ux/orchestrator
python3 -m ladder soft --help
```

Optional extras, managed with uv:

```sh
uv sync --extra parquet --extra trace          # pyarrow (trials.parquet), perfetto (T-session stages)
uv run python -m ladder soft report ...
```

**The rungs must be built first.** Run these from the repo root, with Node 22 and npm:

```sh
npm install
node rungs/scripts/prepare.mjs                          # dataset dev-1 + probe into each rung
(cd rungs/r1-vite && npm ci && npm run build)
(cd rungs/r2-diligent && npm ci && npm run build)
(cd rungs/r3-no-framework && npm ci && npm run build)
(cd rungs/r1-typical && npm ci && npm run build)        # optional: Next.js R1, served via `next start`
```

The harness serves each rung's production build itself: `dist/`, or a proxy to `next start` for
r1-typical. The probe and `marker.json` come from `rungs/shared/`, and the dataset from
`dataset/out/dev-1/`, so every rung gets identical bytes.

## A real session on the X1 Carbon (Linux, KDE Plasma on Wayland)

1. **Prepare the laptop.** This is the 06 §6 subset:
   - AC power, performance power profile.
   - Other apps closed, Do Not Disturb on.
   - Integer display scaling, Night Light off.
   - The refresh rate set in *System Settings → Display & Monitor* (60 Hz first).
   - Optionally boot with `xe.enable_psr=0 xe.enable_panel_replay=0`; the manifest records it.
2. **Chrome.** Install current Google Chrome stable, version 145 or later. The runner refuses an older
   Chrome (see the table below). If Chrome is not on the PATH, pass `--chrome /path/to/chrome`.
3. **uinput permission** (same as A1). Either run
   `sudo modprobe uinput; sudo setfacl -m u:$USER:rw /dev/uinput`, or add `--sudo-injector` to the
   run command; then only the injector child process runs under sudo. **Never run the whole thing with
   sudo**: Chrome must keep its sandbox.
4. **Rehearsal** (about 3 minutes). This checks the whole chain on one rung:
   ```sh
   python3 -m ladder soft run --machine x1 --hz 60 --rungs r3-no-framework --sizes 10k \
       --first-key 20 --seq 1 --warmup 5 --reps 1
   ```
5. **Headline session, 60 Hz, maximized window:**
   ```sh
   python3 -m ladder soft run --machine x1 --hz 60 --display maximized \
       --rungs r1-typical,r1-vite,r2-diligent,r3-no-framework --sizes 10k,50k
   ```
   The defaults are 300 `A.first_key` trials (each followed by an `A.clear`) and 5 `A.seq` sequences
   (40 keys each) per rung × size. They are split over 2 blocks per condition in ABBA order, with 50
   warm-up trials per block.
6. **Extra blocks worth running** (§7.4). Each is the same command with one change:
   - `--hz 120`, after switching the panel to 120 Hz;
   - `--display fullscreen`;
   - on a second day, the same commands again.
7. **T session** (attribution only, never headline): add `--trace --first-key 100 --seq 2`. It writes
   `blocks/*/trace.pftrace`.
8. **During a run:**
   - Park the mouse pointer at the screen edge. If it hovers over the list, cmdk changes its selection.
   - Chrome opens a fresh window with a fresh profile for every block. If the terminal says
     `Click once inside the new Chrome window`, click in the palette.
   - After that, keep your hands off the keyboard and mouse.
   - If the window loses focus, injection stops within one key and the block is marked `aborted`.

**Expected duration.** A block takes from about 1.5 minutes (R3) to about 8 minutes (R1 at 50k, if it
needs about 1 s per key). The time goes on page load, 50 warm-up and 150 measured isolated trials (two
keys each, plus a 50–250 ms gap before each key), 2–3 typed sequences, and 10 s of idle between blocks.

- The default 4-rung, 2-size session has 16 blocks and takes about **45–90 minutes**, most of it
  hands-off.
- The 120 Hz and fullscreen variants take the same again.

**macOS (MacBook Air).**
- Grant the Accessibility permission to your terminal once, as for A1.
- Run the same commands with `--machine mba`.
- The runner activates each new Chrome window. If activation fails, click it.
- The host clock is `CLOCK_UPTIME_RAW` (`mach_absolute_time`), which is Chrome's TimeTicks base on
  macOS.

**What to send back.** Send one tarball of the session directories, for example:

```sh
tar czf ladder-x1-sessions.tgz -C results sessions
```

Each session holds:

- `manifest.json`
- `plan.json`
- `env/`
- `runner.log`
- `blocks/<id>/{block.json, seg-*.json[, trace.pftrace]}`

It contains timings, Chrome's version, GPU and command line, and OS and display info. It contains no
hostname and no personal files. Raw files are written read-only, and the manifest lists their sha256
digests.

Optionally, run `python3 -m ladder soft report results/sessions/* --out report.html` and send
`report.html` as well.

## What depends on the A1 check, and how the runner adapts or refuses

The runner re-checks each A1 assumption on the first segment of every block. A1's report is not an
input, and nothing is assumed from it. On failure the runner refuses the session. An explicit,
recorded override exists where measuring anyway still makes sense.

| A1 row | Assumption | Runner behaviour |
| --- | --- | --- |
| `env` / version | Chrome ≥ 145 | Refuses older Chrome. `--allow-old-chrome` measures with the fallback (below) |
| `et_presentation`, `et_every_flip` | Every inserted `ladder-flip-<seq>` span gets an Element Timing entry with `presentationTime` | Preflight refuses if `presentationTime` is absent. `--allow-fallback-timing` uses `paintTime`, then `renderTime`, and records per trial which field was used (`t_present_source`). A flip with no entry makes its key `no_probe_entry` (block unhealthy above 1%). Keys whose own flip was never painted are attributed to the next presented flip (`own_flip_unpainted`) |
| `coi`, `timer_res` | `crossOriginIsolated`, 5 µs timers | Refuses the block if the page is not cross-origin isolated |
| `keys_delivered` | Injected keys reach the page | Preflight refuses if any key of the first segment is missing (focus, uinput, compositor). Later losses are `no_event` rows |
| `os_delivery`, `ts_semantics` | `event.timeStamp` − injection is small and non-negative (Chrome stamps at read time on Wayland) | Preflight refuses if the OS-delivery p50 is outside [−1.5, 20] ms. `--allow-odd-timestamps` overrides. The headline never uses `event.timeStamp`: it is injector time → presentation, with OS delivery reported as a separate stage |
| `clock_sync` | Minimum-RTT WebSocket sync; drift < 0.2 ms | Syncs before and after every segment. A segment whose offsets differ by more than 0.2 ms is `clock_flag` and excluded |
| `injector_jitter` | p99 < 0.2 ms | Self-test at session start, recorded, with a warning above 0.2 ms. It never refuses, because injection times are measured, not planned. Every key's actual−planned error is in `inject_late_us` |
| `wayland` | Native Wayland | Adds `--ozone-platform=wayland` when `WAYLAND_DISPLAY` is set and records the child-process switches per block |
| `et_inline_span` | The probe span must be block-level | Already true in `ladder-probe.js` (the span is `position:absolute`) |

## Trials, timing and statuses

- **Trials** (§4.5, decisions of 2026-09-30):
  - `A.first_key`: one key into the empty palette.
  - `A.clear`: the Backspace that follows it.
  - `A.seq`: a 20-character typeable query at U[90,117] ms key-to-key, then 20 Backspaces; `seq_pos`
    0..39.
  - Isolated keys wait for the page-reported settle, then U[50,250] ms.
  - Holds are U[30,60] ms.
  - Everything derives from the recorded session seed (`plan.json`).
- **Latency:** `lat_present_ms = t_marker_present − (t_inject_down − offset)`.
- **Stage splits:**
  - OS delivery: injection → `event.timeStamp`.
  - Event → flip: handler, worker, deferred rendering.
  - Flip → present.
  - Event Timing input delay and processing, when an entry exists (≥ 16 ms).
- **Coalesced keys.** A key with no flip of its own is attributed to the first presented flip whose
  query includes it. It gets status `coalesced`, and its latency is kept. This also covers a key whose
  own flip was replaced before any paint.
- **Statuses:**
  - `ok`, `coalesced` and `wrong_result` keep their latency.
  - `timeout` means no settle within 2 s (5 s for r1*). Its latency is kept if a flip presented,
    otherwise it counts as +∞ (censored).
  - `no_event`, `clock_flag` and `no_probe_entry` are excluded from the latency statistics, and
    counted in the health table.
- **Correctness.** Each attributed flip's displayed prefix (up to 50 rows) and count are compared with
  `rank()` from `parity/`. The Rust `rank-cli` is used when built, otherwise ranker-ts under Node.
  - R1: tie-insensitive on forward keys, result set only on backspace.
  - R2 and up: strict.
  - Any mismatch sets status `wrong_result`, and the report marks the rung non-parity.
- **Statistics:**
  - Hyndman–Fan type 7 quantiles.
  - 10,000-resample bootstrap: BCa for p50/p95, percentile for p99.
  - A moving-block bootstrap when Ljung–Box finds lag-1..10 autocorrelation.
  - Ratios and frame deltas vs R1 from an independent bootstrap of both arms.

## Deviations from the phase-a text (and why)

- **Origins.** Each rung gets its own local port (origin) instead of `/r/<token>/` paths on one
  origin. The builds use absolute asset URLs (`/assets/…`, `/ladder/probe.js`), and blind-mode
  opaque URLs are the playground's job.
- **Settle.** Settle is "the flip reflecting the current input value has its Element Timing entry,
  then 2 frames without a new flip". It is not "no DOM mutation for 2 frames": a MutationObserver on
  a 50k-item list costs R1 much more than the other rungs.
  - For the same reason, the marker-honesty MutationObserver (§4.4 same-frame check) is **not** run
    in M sessions. The parity suite (`rungs/tests`, spec (c)) checks it on every query change.
- **Gap before `A.clear`.** `A.clear` also waits U[50,250] ms after settle. Pressing Backspace right
  at settle would lock it to the rAF phase.
- **Segments.** Blocks run in segments of at most 40 keys. The probe's ring buffers hold 8,192
  records (about 68 s of frames at 120 Hz), so buffers are read back, and the clock re-synced, between
  segments. Nothing is posted inside a segment except the one settle message per key.
- **Standard library only.** No pydantic, polars, typer, Jinja or matplotlib. The manifest is
  validated by a small schema table (`manifest.py`), the report uses inline SVG, and Parquet is
  written only when pyarrow is installed.
- **Jitter self-test.** It times 2,000 scheduled wake-ups without writing keys: any key device types
  into the focused window. The evdev kernel-timestamp readback of §3.3 is not implemented yet (TODO).
- **Chrome info.** The version, command line and GPU come from a separate short-lived Chrome with a
  DevTools port at session start, never from a measured block.
- **Clock rebasing (T sessions).** Chrome trace timestamps in the container were on the host
  monotonic clock, so they can be joined to injector time directly. **[verify]** on the laptop.

## Simulate mode (development only, NOT REAL)

`--simulate` injects keys with CDP `Input.dispatchKeyEvent` into headless Chromium 141
(`/opt/pw-browsers`, `--no-sandbox` as root in the container). It exists so the whole pipeline can
run without a display.

- Every output is labelled:
  - the session directory is prefixed `SIMULATED-`;
  - the manifest has `simulated: true`, `input_source: cdp-simulated` and a `not_real_warning`;
  - every CSV row has `validity = SIMULATED-NOT-REAL`;
  - the report has a red banner, and Gate A reads "NOT VALID (simulated)".
- Chromium 141 has no `paintTime`/`presentationTime`, so simulate runs exercise the **renderTime
  fallback**, which is flagged per trial.
- Chromium 141's `renderTime` sometimes precedes the flip task, apparently the start of a long frame.
  Such values are clamped to the flip time and flagged (`present_clamped`), rather than dropped, since
  dropping them would drop R1's slow tail.

```sh
python3 -m ladder soft run --simulate --rungs r1-vite,r2-diligent,r3-no-framework --sizes 10k,50k \
    --first-key 40 --seq 2 --warmup 3 --reps 2 --settle-timeout-ms 8000 --seed 20260930
python3 -m ladder soft report results/sessions/SIMULATED-* --out results/sim-report.html
```

**The dev-container run of 2026-09-30.** This was the command above, seed 20260930: 12 blocks, 1,032
trials. Everything below is **SIMULATED, NOT REAL**: headless Chromium 141 on 4 vCPUs, CDP input, and
the renderTime fallback. Its output is in `results/sessions/SIMULATED-…`, `results/sim-report.html` and
`results/sim-report-trials.csv`.

- All 12 blocks were valid. Statuses: 915 `ok`, 115 `coalesced`, 2 `timeout`.
- 47 keys had their own flip unpainted (R1 draining queued keys). 121 fallback times were clamped.
- Correctness: 1,032 flips checked against ladder-rank, 0 wrong (688 strict, 132 tie, 212 set).
- A.first_key p95, 10k: r1-vite 674 ms, r2 36 ms, r3 36 ms.
- A.first_key p95, 50k: r1-vite 3,876 ms, r2 52 ms, r3 36 ms.
- R1's A.seq at 50k queues up to about 24 s.

The pipeline works. The numbers mean nothing.

## Perceptibility input

`ladder soft report --perceptibility FILE` accepts any of these:

```json
{"pairs": [{"a": "r1-vite", "b": "r3-no-framework", "n": 40, "correct": 29},
           {"a": "r3-no-framework", "b": "r3-no-framework", "n": 40, "correct": 21}], "jnd_ms": 30}
{"trials": [{"a": "r1-vite", "b": "r3-no-framework", "correct": true}, ...]}
```

`"pair": [a, b]` may replace `a`/`b`, and `"placebo": true` marks a placebo pair. `correct: null`
("no difference") is ignored. With no file, perceptibility is "pending" and Gate A cannot pass.

## Tests

```sh
python3 -m unittest discover -s tests        # 63 tests, ~3 s, Python 3.9-3.13, no dependencies
```

The tests cover:

- schedule generation: seeded determinism, jitter bounds, ABBA order, expected values;
- clock mapping: min-RTT offset, asymmetry bound, drift flag;
- key matching and coalesced-key attribution;
- trial extraction from a synthetic segment, including fallbacks, lost keys and settle timeouts;
- statistics: type-7 quantiles, jackknife, bootstrap coverage on synthetic data, moving-block
  switching, ratios, binomial;
- the Gate A rule and the perceptibility formats;
- the manifest schema;
- correctness levels;
- the WebSocket framing, Chrome flag lint and the Perfetto config encoding;
- the settle-aware injector executor, with a fake page.

**Not tested here, because it needs the real machines:**

- uinput and Quartz injection end to end;
- KWin/macOS focus handling;
- Chrome ≥ 145 `presentationTime`;
- real OS-delivery numbers;
- the sudo injector path.
