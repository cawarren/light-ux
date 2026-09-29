# Workstream 03: Orchestrator and Results Pipeline (Phase 0 scope)

Scope owner: harness (human-owned). Covers the Phase 0 checklist item "Write the orchestrator: N trials, randomized delays, CSV output", plus rung switching, environment capture, storage, statistics, calibration reporting, and a mock rig. Firmware internals and rig hardware are other workstreams; this document only fixes the **interfaces** it needs from them.

---

## 0. Design principles (the non-negotiables)

1. **The orchestrator is never in the timed loop.** All timing (input report sent, photon edge seen, inter-trial delays) happens on the microcontroller clock. The orchestrator uploads a plan, says "go", and receives records. Its own jitter (Python GC, OS scheduler, USB-serial buffering) cannot affect a single measured number.
2. **Nothing runs on the machine under test (SUT) during a measured block** except the OS, the rung, and whatever the rung needs (e.g. Chrome). Setup traffic (SSH, file copy, launch) happens *before* a block, then the link goes quiet for a fixed quiesce period.
3. **Raw data is immutable and self-describing.** Every number in every report can be regenerated from raw device bytes + a manifest + a pinned version of the analysis code.
4. **Nothing is dropped silently.** Timeouts, spurious edges and desyncs are rows with a status, counted in every report.
5. **Seeds, not sequences, are the unit of reproducibility.** Every random choice (rung order, inter-trial delays, key choice) derives from a recorded seed.

---

## 1. Language, stack and CLI

### Recommendation: Python 3.12, managed with `uv`

| Concern | Choice | Why |
|---|---|---|
| Env / packaging | `uv` + `pyproject.toml` + `uv.lock` | Reproducible, fast; lockfile is part of provenance (hash recorded in manifest). |
| Serial | `pyserial` (sync, in a dedicated reader thread) | Mature, cross-platform; throughput needs are tiny (< 1 MB/s even with waveforms). No asyncio needed. |
| Framing | COBS + CRC32 (`cobs` package or 30-line impl) | Robust resync after garbage; same framing trivially implemented in firmware. |
| Schemas | `pydantic` v2 | Session manifest / plan / env validated and versioned; JSON Schema exported for firmware and docs. |
| Tables | `polars` + `pyarrow` (Parquet) | Fast, strict typing, columnar; CSV export is one call. |
| Stats | `numpy`, `scipy.stats` (bootstrap, circular stats by hand) | Standard, auditable. |
| Plots / reports | `matplotlib` (static PNG/SVG) + Jinja2 → single static HTML report | Publishable, no server, diffable. |
| CLI | `typer` | Typed subcommands, auto help. |
| Remote control of SUT | `asyncssh` or plain `ssh` subprocess (OpenSSH on Win11/macOS/Linux) | No custom agent binary on the SUT. |
| Tests | `pytest`, `hypothesis` (property tests on parser/stats) | Runs in CI against the mock rig. |

**Why not Rust?** Nothing here is latency-critical (principle 1), and the bulk of the work is stats, reporting, schema juggling and SSH orchestration, where Python is much faster to write and to audit by outsiders reproducing the study. The one place Rust would help (a byte-exact shared protocol codec with firmware) is solved by a written protocol spec plus shared test vectors. If the firmware team wants a shared codec, a small `protocol/` crate with Python bindings can come later without changing the architecture.

### CLI shape (`ladder`)

```
ladder rig info                         # firmware version, build SHA, protocol version, sensor self-test
ladder rig calibrate-led --trials 1000  # rig latency via LED (writes a calibration record, id cal_...)
ladder rig threshold --target <sut>     # measure black/white marker levels, set detection thresholds

ladder env capture --target win-ref     # snapshot SUT environment over SSH -> env.json
ladder plan  --target win-ref --rungs r1,r3,r5,floor-display,floor-browser \
             --scenario A --trials 500 --warmup 50 --days 2 --seed 1234
                                        # writes plan.json for day 1 and day 2 (different rung orders)
ladder run   --plan plans/2026-10-06-win-ref-d1.json      # executes whole session
ladder run   --rung r1 --scenario A --trials 500 --warmup 50 --target win-ref   # ad-hoc single block

ladder eval  --rung r3 --candidate <build-id> --baseline <build-id> --trials 200 \
             --interleave 4 --json      # the agent-loop entry point; returns accept/reject verdict JSON
ladder aa    --rung r3 --build <id> --pairs 10       # A/A runs to estimate the noise band

ladder analyze <session-dir>            # (re)derive trials.parquet + stats.json from raw
ladder report calibration --sessions s1,s2 --out reports/cal-win-ref.html
ladder report scorecard --machine win-ref --hz 60
ladder export csv <session-dir>         # flat CSV for outsiders
ladder verify <session-dir>             # hash check of raw files vs manifest
```

`--target` names an entry in `orchestrator/targets/*.toml` (host, SSH user, OS, rung launch recipes, display config expectations).

---

## 2. The trial loop

### 2.1 Who generates the randomized schedule: orchestrator decides, firmware executes

- The orchestrator derives the full block schedule from the block seed (PCG64 via `numpy.random.Generator`) and **uploads it as an explicit table** to the device: per trial `{trial_idx, action_id, pre_delay_us}`. 550 trials x ~8 bytes ≈ 4.4 KB, trivial.
- The firmware then runs the block autonomously from its own clock: wait `pre_delay_us`, emit HID report at a USB SOF-aligned instant, record `t_input`, watch the photodiode, record `t_photon`, stream the record back, move on.
- **Why on-device execution:** if the host triggered each keystroke, every trial's input time would inherit USB-serial latency (1-16 ms on some adapters/drivers), Python scheduling jitter and GC pauses. That jitter is not in the measured interval (t_input is stamped on-device either way), but it *would* distort the inter-trial delay distribution and could correlate trial timing with host activity. More importantly, a streaming host-driven loop breaks if the host stalls; a pre-uploaded plan is immune.
- **Why explicit table rather than seed-on-device:** the orchestrator's schedule is then the authoritative record, no need to keep two PRNG implementations bit-identical, and the device echoes each executed `pre_delay_us` so the schedule actually executed is verified against the planned one.
- Delay distribution: uniform continuous on [50, 250] ms at 1 µs resolution (not integer ms, to avoid any lattice). Delay is measured from **end of previous trial's settle** (see 2.4), not from the previous keystroke.

### 2.2 Refresh-phase caution in Scenario A's "100 ms per key"

**Finding:** 100 ms is exactly 6 frames at 60 Hz, 12 at 120 Hz and 24 at 240 Hz. A fixed 100 ms key cadence therefore locks every key in a query sequence to the same refresh phase as the first key, which is precisely the aliasing the protocol is trying to prevent. Recommendation: keep a nominal 100 ms cadence but add uniform jitter (e.g. 100 ms ± 8.4 ms, i.e. at least half a 60 Hz frame; or draw 90-117 ms) per key. Listed as an open decision (section 9).

### 2.3 Block structure

```
session  (one SUT, one display config, one day)
 └─ block  (one rung x scenario x variant; order randomized per session)
     ├─ preflight  (focus probe, marker polarity sync, threshold check)
     ├─ warm-up trials (50, flagged warmup=true, stored but excluded from stats)
     └─ measured trials (500)
```

- **Rung order:** per-session random permutation from the session seed. Calibration blocks (display floor, browser floor) are included in the permutation, and additionally a short browser-floor "sentinel" block (100 trials) is run at the start and end of each session to detect within-session drift.
- **Second day:** `ladder plan --days 2` emits two plans sharing a `pair_id`, same trial counts, independent rung-order seeds. The day-2 plan must be run on a different calendar day after a reboot of the SUT (recorded; enforced as a warning if uptime indicates no reboot).
- **Machine hygiene between blocks:** close the previous rung, wait a fixed cool-down (e.g. 30 s), launch the next, wait settle (e.g. 20 s + CPU idle check), close SSH, quiesce (10 s), then start.

### 2.4 Trial definition per scenario (Phase 0 needs A and the floors; B-D defined so the schema supports them)

A trial = one input action with one expected photon event. Scenario A decomposes into actions:

| action kind | Input | Expected photon | Reset |
|---|---|---|---|
| `A.first_key` | One character into empty focused palette | marker flip | followed by `A.clear` |
| `A.clear` | Backspace returning query to empty | marker flip (it is also a measured event, tagged separately) | none needed |
| `A.seq_key` | k-th key of a 20-char held-out query, jittered ~100 ms cadence | marker flip per key | sequence ends with backspaces |
| `A.seq_backspace` | backspaces unwinding the query | marker flip per key | returns to empty |

Headline Scenario A number: proposed to be `A.first_key` (cleanest, one state change from a known state); sequence keys reported secondarily. **Open decision.** Characters for `A.first_key` are drawn from the held-out query set's first-character distribution, because result count (and so work) depends on the character; the drawn char is recorded per trial.

Reset for A is inherent (backspace), avoiding Escape/reopen, which changes more state than the thing being measured. After each trial the firmware waits for detection + a fixed 50 ms settle before starting the next random delay.

Floors: `floor.display` and `floor.browser` use the same `first_key`/`clear` alternation so their polarity mix matches Scenario A.

B-D (later phases): B = mouse path script id + photodiode position; C = scroll script id, frame-drop metrics from camera/PresentMon in separate sessions; D = launch-then-key, timeout in seconds. The schema below carries `scenario`, `action_kind`, `script_id` so these slot in without migration.

### 2.5 Detection, timeouts and failure handling

- **Polarity tracking:** the marker alternates; the device knows the expected direction (dark→light or light→dark) and records it per trial. LCD rise/fall times differ, so `polarity` is a first-class column and reports show both.
- **Thresholds:** `ladder rig threshold` measures the black and white marker levels at block start (device reports ADC levels while the rung holds each state); threshold at 50% with hysteresis. Levels stored in block metadata; re-checked in the end-of-block preflight to catch mount slip.
- **Timeouts:** per-scenario (A: 1,000 ms for first key; D: 30 s). On timeout: status `timeout`, the device performs a **resync** (wait 500 ms, read current marker level, set expected polarity from it) and continues. Not dropped from counts.
- **Spurious edges** (edge without a pending input, or two edges for one input): status `spurious` / `double_edge`, with timestamps.
- **Block health rules:** block is marked `invalid` if timeouts > 1% or spurious > 0.5% or thresholds drifted > 20% between start and end. Invalid blocks are kept, excluded from stats, and flagged in reports. A 1% timeout rate on a slow rung could itself be a finding (e.g. R1 taking > 1 s), so timeouts are also reported as a censored tail: p99 is reported as "> 1000 ms" if the censored fraction exceeds 1%.
- **Link loss:** if the serial link drops, the device keeps running the block and buffers records (ring buffer sized for a full block); the orchestrator reconnects and requests replay from last acked seq number. If the buffer overflows, block `invalid`.
- **Focus probe (preflight):** before warm-up, send one key and require a photon edge within 2 s. Catches "app not focused / not launched / wrong window" without any software on the SUT.

### 2.6 Optional waveform capture

Recommend the device stream a short photodiode window per trial (e.g. -5 ms to +150 ms around the detected edge at 20 kHz, u16 → ~6 KB/trial, ~3.3 MB/block). Stored raw. This lets anyone re-run detection offline with a different threshold, audit outliers, and measure LCD transition shape. The orchestrator implements an offline reference detector; disagreement rate with the on-device detector (> 0.1 ms) is reported per block.

---

## 3. Switching rungs and capturing the environment without perturbing the SUT

### 3.1 Control channel options

| Option | Perturbation during trials | Automation | Verdict |
|---|---|---|---|
| **A. SSH, used only between blocks** (OpenSSH server on SUT; connection closed before quiesce) | sshd idle, listening socket only | Full | **Recommended.** Verify with an A/A experiment: browser floor with sshd running vs stopped, must be within noise. |
| B. Manual (human launches each rung, presses "ready") | none | None; kills the agent loop | Fallback and for macOS if SSH causes issues. |
| C. HID-typed launch (the rig types `Win+R`, command, Enter) | none, no extra software at all | Brittle (focus, IME, locale) | Keep as a curiosity; not primary. |
| D. Custom resident agent on SUT | constant (a process polling) | Full | Rejected. |

Rules for option A:
- SUT is on wired Ethernet; Wi-Fi off. No network activity is initiated by the orchestrator during a block.
- Launch recipe per rung in `targets/*.toml`: e.g. Chrome for Testing pinned binary, fresh `--user-data-dir` per block, fixed flag list, **no `--remote-debugging-port`** in measured blocks (CDP changes browser behaviour), window mode fixed (see open decision on fullscreen vs windowed, which changes the compositor path on Windows).
- Web rungs are served from a single fixed static server on the SUT (same binary, started once per session) or `file://` where the rung allows. Same server for all web rungs so it is a constant. R1's `next start` Node server vs static export is an open decision.
- After launch: poll SUT CPU idle (< 3% for 5 s) over SSH, then close the SSH session, wait 10 s quiesce, run preflight focus probe via the rig.
- Build artifacts are copied to SUT before the session and hashed; hash recorded.

### 3.2 Environment capture (session metadata)

Captured over SSH at **session start and session end**, plus a short capture between blocks; any diff between start and end marks the session `env_changed` and lists the changed keys.

| Field | Windows | macOS | Linux |
|---|---|---|---|
| OS build | `Get-ComputerInfo` (OsBuildNumber, UBR) | `sw_vers` | `uname -a`, `/etc/os-release` |
| Chrome version + flags | binary `--version`, launch recipe | same | same |
| GPU + driver | `Win32_VideoController` | `system_profiler SPDisplaysDataType` | `lspci`, `glxinfo -B` / `vulkaninfo --summary` |
| Refresh rate, resolution | `Win32_VideoController.CurrentRefreshRate` + small PowerShell/C# `EnumDisplaySettings` helper | `system_profiler SPDisplaysDataType` | `wlr-randr` / `kmsprint` / `xrandr` |
| Scaling | registry `LogPixels` / `GetDpiForMonitor` helper | `system_profiler` (UI looks like) | compositor scale |
| VRR / HDR | Settings registry keys, `dxdiag /t` | `system_profiler` (ProMotion) | `drm_info` VRR_ENABLED |
| Power | `powercfg /getactivescheme`, `Win32_Battery` (AC) | `pmset -g`, `pmset -g batt` | `powerprofilesctl get`, `/sys/class/power_supply` |
| Notifications / focus assist | registry | `defaults read com.apple.ncprefs` (best effort) | n/a |
| Uptime / last boot | `Win32_OperatingSystem.LastBootUpTime` | `sysctl kern.boottime` | `/proc/uptime` |
| Process list (hash + top 20 by CPU) | `Get-Process` | `ps` | `ps` |
| CPU, RAM, thermals if available | WMI | `sysctl`, `powermetrics` (skip if needs sudo) | `/proc/cpuinfo`, `sensors` |

Also recorded by the rig itself, independent of the OS: **measured refresh period** (see 5.5) and marker ADC levels (proxy for brightness). If the OS reports 60 Hz but the photon-phase estimate says 59.94 Hz or 120 Hz, the block is flagged.

Human-asserted fields (entered once per session via `--notes` or prompt): monitor model, brightness setting, room lighting, mount position id, operator.

---

## 4. Data model and storage

### 4.1 Layers

- **L0 raw (immutable):** verbatim device byte stream (`device.cobs.bin`), plus decoded-but-unmodified `device.jsonl`; environment snapshots; plan; orchestrator log. Written append-only, fsynced, hashed at block close; files set read-only.
- **L1 trials (derived, regenerable):** `trials.parquet` per session, produced by `ladder analyze` from L0. CSV export for outsiders.
- **L2 stats (derived):** `stats.json` per block and per session, report HTML/PNGs. Each carries the analysis code git SHA and the L0 hashes it was built from.

### 4.2 File layout

```
results/                                   # NOT in the main git repo (see open decisions)
  calibration/
    cal_01JB.../                           # rig LED calibration runs
      manifest.json  device.cobs.bin  device.jsonl  trials.parquet  stats.json
  sessions/
    2026-10-06_win-ref_60hz_d1_01JB.../
      manifest.json                        # session manifest (below)
      plan.json                            # exact uploaded schedules + seeds
      env/start.json env/end.json env/block-<n>.json
      blocks/
        b03_r1_A_01JB.../
          device.cobs.bin                  # L0
          device.jsonl                     # L0 decoded
          waveforms.bin                    # L0 optional
          block.json                       # thresholds, launch recipe, artifact hashes, health
      orchestrator.log
      derived/                             # L1 + L2, deletable and regenerable
        trials.parquet  trials.csv  stats.json  report.html
  index.parquet                            # one row per block, for scorecards
```

IDs are ULIDs (sortable, unique without coordination).

### 4.3 `trials.parquet` schema (one row per action)

| column | type | notes |
|---|---|---|
| session_id, block_id | str | ULIDs |
| machine_id, display_hz_nominal | str, f32 | from target |
| rung, rung_variant | str | e.g. `r4`, `mirror_off` |
| rung_build_id | str | artifact sha256 / git SHA |
| scenario, action_kind, script_id | str | `A`, `A.first_key`, `q-heldout-v1` |
| trial_idx, seq_no | u32 | plan index; device sequence number |
| warmup | bool | |
| key_code, char | u16, str | what was sent |
| polarity | enum `d2l`/`l2d` | expected marker transition |
| planned_pre_delay_us, actual_pre_delay_us | u32 | executed vs planned |
| t_input_us | u64 | device clock, HID report committed (SOF-aligned) |
| t_photon_us | u64 nullable | device clock, threshold crossing (interpolated) |
| latency_raw_us | i64 nullable | t_photon - t_input |
| latency_corr_us | i64 nullable | minus rig latency median for the referenced calibration |
| status | enum | `ok`, `timeout`, `spurious`, `double_edge`, `resync`, `link_gap` |
| edge_amplitude, rise_time_us | f32 | from device / offline detector |
| offline_latency_us | i64 nullable | reference detector on waveform |
| trigger_phase, photon_phase | f32 | 0..1 within estimated frame (filled by analysis) |

### 4.4 Session manifest (`manifest.json`, pydantic-validated, `schema_version`)

```json
{
  "schema_version": "1.0",
  "session_id": "01JB...",
  "pair_id": "01JA...", "day_index": 1,
  "machine_id": "win-ref", "display": {"nominal_hz": 60, "measured_hz": 59.998, "mode": "windowed"},
  "operator": "...", "started_at": "...", "ended_at": "...",
  "seeds": {"session": 1234, "rung_order": 99812, "blocks": {"b01": 5531}},
  "block_order": ["floor-browser", "r3", "r1", "floor-display", "r5", "floor-browser"],
  "provenance": {
    "orchestrator_git_sha": "...", "orchestrator_dirty": false, "uv_lock_sha256": "...",
    "firmware_version": "0.3.1", "firmware_build_sha": "...", "protocol_version": 2,
    "rig_hw_revision": "rev-a", "sensor_id": "pd-01", "mount_position": "top-right-v1",
    "calibration_id": "cal_01JB...", "calibration_rig_latency_us": {"p50": 212, "p95": 240},
    "dataset_generator_sha": "...", "query_set_id": "heldout-v1"
  },
  "env": {"start": "env/start.json", "end": "env/end.json", "changed_keys": []},
  "blocks": [{"block_id": "...", "rung": "r1", "rung_build_id": "...", "health": "valid",
              "counts": {"ok": 500, "timeout": 0, "spurious": 0}}],
  "files": {"blocks/b03.../device.cobs.bin": "sha256:..."}
}
```

Rules: a session refuses to start if the orchestrator tree is dirty (override flag records `orchestrator_dirty: true` and reports watermark it), if no calibration exists younger than N days (proposed 7) for this rig revision, or if firmware protocol version mismatches.

### 4.5 Protocol boundary with the firmware workstream (proposed, to be co-signed)

Messages (COBS-framed, CRC32, little-endian, every message has `seq`):
- host→device: `HELLO`, `GET_INFO`, `SET_THRESHOLDS`, `LOAD_PLAN(chunked)`, `START_BLOCK`, `ABORT`, `ACK(seq)`, `REPLAY_FROM(seq)`, `MEASURE_LEVELS`, `LED_CAL(n)`.
- device→host: `INFO{fw_version, build_sha, protocol_version, clock_hz}`, `TRIAL{...fields above...}`, `WAVEFORM{trial_idx, t0_us, dt_us, samples[u16]}`, `LEVELS`, `EVENT{type: resync|spurious|overflow}`, `BLOCK_DONE{counts}`, `HEARTBEAT` (only between blocks).
- Hardware note for that workstream: the MCU's native USB port is the HID device on the SUT, so the orchestrator link must be a **separate** port: hardware UART to a USB-UART adapter (ideally galvanically isolated, to keep ground-loop noise out of the photodiode amplifier), or a second USB port (RP2040 PIO-USB, Teensy 4.1 host pins are host-only). The HID device must not expose a CDC/serial interface to the SUT.

---

## 5. Statistics

All in `orchestrator/ladder/stats/`, pure functions over `trials.parquet`, unit-tested with synthetic distributions whose true quantiles are known.

### 5.1 Percentiles and CIs
- Quantile definition fixed once: Hyndman-Fan type 7 (numpy `linear`) , documented in the report footer.
- CIs: bootstrap, 10,000 resamples, BCa for p50/p95, percentile method for p99. Seed recorded.
- **Autocorrelation check:** lag-1..10 ACF of latency within a block. If significant (thermal, GC cadence), switch to moving-block bootstrap (block length ~ sqrt(n)) and flag. Report which was used.
- **p99 honesty:** with n = 500, only 5 samples sit above p99, so its CI is wide. Reports show the CI; recommend 1,000+ trials for any block whose p99 is quoted in the headline (open decision).

### 5.2 Rig-latency subtraction
- `latency_corr = latency_raw - rig_latency_p50` for the referenced calibration id. Subtract a constant, never subtract distributions per trial. Rig spread (LED calibration p5-p95) is reported as an additive uncertainty and must be < 0.5 ms or calibration fails.
- USB polling (0-1 ms at 1 kHz) is part of the real keystroke-to-photon path and is **not** subtracted; documented.
- Raw and corrected numbers are both stored; reports show corrected with raw available.

### 5.3 Noise band for the agent optimization loop
- **Estimate:** `ladder aa` runs K ≥ 10 A/A pairs (same build, 200 trials each, interleaved) per rung per machine. Noise band `N95` = 95th percentile of |Δp95| across pairs (also compute via bootstrap of a single long run as a cross-check). Refreshed weekly and after any env change.
- **Decision rule for `ladder eval`:** run baseline and candidate **interleaved** in ABAB blocks (e.g. 4 x 50 trials each) to cancel drift. Accept iff (a) Δp95 = p95_base - p95_cand > N95, **and** (b) the bootstrap 95% CI of Δp95 (resampling within-arm, paired by interleave block) excludes 0, **and** (c) no health flags. Output JSON: `{verdict, delta_p95_ms, ci, noise_band_ms, n, block_ids}`.
- Multiple-comparisons guard: agents will run many evals; report the cumulative accept rate on A/A pairs to show the false-accept rate stays near 5%. Accepted changes require second-machine confirmation anyway (spec step 5).

### 5.4 Two-day agreement check
- For each (machine, rung, scenario) with both days: |p50_d1 - p50_d2| ≤ 2 ms passes. Also report the bootstrap CI of the difference and p95 difference for information.
- **Caveat worth raising early:** keystroke-to-photon distributions on a fixed-refresh display are multimodal with modes spaced one frame apart. If ~50% of the mass sits near a mode boundary, p50 can jump by ~16.7 ms between days from a tiny shift in the mode weights, failing the 2 ms criterion while nothing meaningful changed. The report therefore also shows the mode weights, mean, and a 10%-trimmed mean, and flags "p50 on a mode boundary" cases. Whether the success criterion should use p50 or a smoother statistic is an open decision.

### 5.5 Refresh-phase aliasing detection
1. **Estimate the frame period and phase from photon timestamps.** Photon edges occur at scanout of the marker row, so `t_photon mod T` concentrates for the true T. Grid-search T around nominal (±0.5%) maximizing the circular mean resultant length R of `2π t_photon / T`; refine. Gives measured Hz (e.g. 59.998) and vsync phase. Device clock ppm error is negligible at this scale.
2. **Trigger-phase uniformity:** compute `trigger_phase = (t_input - phase0) mod T / T`. Under correct randomization this is uniform. Rayleigh test and Kuiper test; flag if p < 0.01 or R > 0.1.
3. **Diagnostics plotted:** histogram of latency mod T; scatter of latency vs trigger phase (expected sawtooth: latency falls linearly with phase then jumps by one frame); histogram of latency with frame-boundary gridlines.
4. The sawtooth fit also yields a useful decomposition: intercept ≈ fixed pipeline cost, number of frames queued, which directly supports the "every frame queued adds T" narrative.

### 5.6 Deltas for the waterfall
- Layer delta = p95(rung N) - p95(rung N+1), CI from independent bootstrap of both arms. Documented clearly that a difference of quantiles is not the quantile of differences.
- Software-controllable share = rung p95 - display floor p95 (spec risk mitigation), reported alongside.

---

## 6. Reporting

### 6.1 Phase 0 calibration report (`ladder report calibration`)
Single static HTML + PNGs per machine/refresh rate:
1. **Rig latency** (LED): distribution, p50/p95, spread; pass if spread < 0.5 ms and day-to-day p50 within 0.2 ms.
2. **Display floor** and **browser floor**: p50/p95/p99 with CIs, histogram with frame gridlines, sawtooth plot, polarity split (d2l vs l2d), measured refresh rate.
3. **Browser tax at floor** = browser floor - display floor (first real finding of the project).
4. **Day 1 vs day 2:** table of p50/p95 per block with differences, pass/fail against 2 ms, mode-boundary flag, env diffs between the days.
5. **Within-session drift:** sentinel browser-floor blocks at session start vs end.
6. **Health:** counts of timeout/spurious/resync, detector disagreement rate, aliasing test results, sshd on/off A/A result.
7. **Provenance footer:** all IDs, SHAs, seeds, quantile method, bootstrap method.

### 6.2 Future scorecard and waterfall (schema support now)
- `index.parquet` (one row per valid block) has all keys needed: machine, display_hz, rung, variant, scenario, action_kind, parity status (joined from parity suite output by `rung_build_id`), stats. The scorecard is a group-by; waterfall is ordered deltas R1→R6→display floor.
- Non-metric columns in the scorecard (peak memory, bundle size, cold start, dropped frames, parity) come from other tools; they join on `rung_build_id` + `machine_id`. Reserve `metrics_ext.parquet` keyed the same way.
- Non-parity rungs rendered with a hatch pattern and a list of failing checks, as the spec requires.

---

## 7. Mock rig (develop and CI without hardware)

`orchestrator/ladder/sim/` implements the **same wire protocol** as the firmware, exposed as:
- an in-process transport (fast unit tests), and
- a pseudo-terminal (`pty.openpty`, Linux/macOS) so `ladder run --port /dev/pts/N` exercises the real serial code path end to end.

**Latency model** (per trial, all configurable per "virtual rung"):
`t_photon = t_input + usb_poll~U(0,1ms) + app_work~LogNormal(μ,σ) + wait_to_next_vsync(phase, T) + k_frames_queued*T + scanout_row_offset + lcd_response(polarity)`
plus occasional long-tail events (GC pause with probability p). This naturally produces multimodal, frame-quantized distributions, so aliasing detection, period estimation and the p50-mode-boundary caveat are all testable.

**Fault injection:** missed edges, spurious edges, double edges, serial disconnect mid-block (tests replay), CRC corruption, buffer overflow, clock drift, threshold drift, protocol-version mismatch, and a deliberately non-random schedule (tests that the aliasing detector catches it).

**Mock SUT:** a fake `targets/mock.toml` whose SSH commands run locally against canned env JSON, so env capture, diffing and launch recipes are testable.

**CI (GitHub Actions):** `uv run pytest` on Linux + macOS runners; golden-file tests for manifest/plan/trials schemas; a full simulated session (2 days, 5 blocks, 550 trials each) through `run → analyze → report` asserting the known ground-truth quantiles fall inside CIs. Once real hardware exists, a recorded real `device.cobs.bin` becomes a golden fixture too. Protocol test vectors (`protocol/vectors/*.bin` + expected JSON) are shared with firmware CI so both sides are checked against the same bytes.

---

## 8. Proposed monorepo layout and ownership

```
latency-ladder/
  docs/                 spec, protocol spec, rig build guide, methodology    (humans)
  protocol/             wire protocol spec, JSON Schemas, test vectors       (HARNESS, humans; firmware+orch co-own)
  firmware/             rig MCU firmware (Teensy/RP2040)                      (HARNESS, humans)
  hardware/             schematics, BOM, mount CAD                            (HARNESS, humans)
  orchestrator/         `ladder` Python package, sim, stats, reports          (HARNESS, humans)
    ladder/{cli,device,protocol,plan,run,sut,env,store,stats,report,sim}/
    targets/*.toml      machine definitions and launch recipes
    tests/
  calibration/
    display-floor/      native minimal inverter app per OS                    (HARNESS, humans)
    browser-floor/      blank page flipping marker on keydown                 (HARNESS, humans)
  dataset/              seeded generator, held-out query sets (encrypted or private) (HARNESS, humans)
  parity/               parity suite: scripts, reference ranking, diff tools  (HARNESS, humans)
  rungs/
    r1-typical/         frozen; humans only, never agent-optimized
    r2-diligent/        agent-writable (allow-listed techniques)
    r3-no-framework/    agent-writable
    r4-no-dom/          agent-writable
    r5-native/          agent-writable
    r6-floor/           agent-writable, human review on present-mode changes
    shared-marker/      marker spec + reference implementation (HARNESS)
  results/              NOT committed; pointer file to object store + index
  .github/CODEOWNERS
```

**Enforcing "agents can't touch the harness":**
- CODEOWNERS: everything except `rungs/r2..r6/` requires human harness-owner review; branch protection requires CODEOWNERS approval.
- CI job `scope-guard`: agent-authored PRs (identified by bot account / label) fail if the diff touches any path outside their assigned `rungs/<rung>/`.
- Agents invoke `ladder eval` only through a **service boundary** (a job queue on the orchestrator host taking a build artifact + rung id), never by running the orchestrator from their own checkout. The orchestrator host runs a pinned, clean orchestrator SHA.
- Held-out queries and dataset seeds are not in agents' checkout (separate private repo or encrypted blob decrypted only on the orchestrator host).
- Rig is a single shared resource: Phase 0 uses a lock file; Phase 1+ a simple FIFO job queue.

---

## 9. Task breakdown, estimates, risks, open decisions

### 9.1 Tasks (human-owned harness work; agents may draft code, a human reviews and owns)

| # | Task | Est. (person-days) | Depends on |
|---|---|---|---|
| 1 | Repo scaffolding, uv project, CI, CODEOWNERS, scope-guard | 1 | - |
| 2 | Protocol spec v1 + JSON Schemas + test vectors (with firmware owner) | 1.5 | firmware input |
| 3 | Device layer: framing, reader thread, ACK/replay, INFO/version checks | 2 | 2 |
| 4 | Mock rig (sim transport + pty + latency model + faults) | 2 | 2 |
| 5 | Plan generation: seeds, rung-order permutation, delay tables, day pairs, jittered cadence | 1 | - |
| 6 | Run loop: preflight, thresholds, warm-up, block execution, health rules, storage writes, hashing | 2.5 | 3, 5 |
| 7 | SUT control over SSH: targets TOML, launch recipes (Chrome for Testing, floors), idle check, quiesce | 2 | - |
| 8 | Env capture scripts for Win/macOS/Linux + diffing | 2 | 7 |
| 9 | Data model: manifest/trials schemas, analyze (L0→L1), CSV export, verify | 1.5 | 3 |
| 10 | Stats: quantiles, bootstrap (BCa + block), ACF, period/phase estimation, aliasing tests, two-day check | 3 | 9 |
| 11 | Noise band (`aa`) + `eval` decision rule with interleaving | 1.5 | 6, 10 |
| 12 | Calibration report (HTML + plots) | 2 | 10 |
| 13 | Offline waveform detector + disagreement metric | 1 | 9 |
| 14 | Hardware bring-up and integration with real rig, fix-ups | 3 | firmware + hardware |
| 15 | Run calibration day 1 and day 2 on reference machine, write up | 2 | all |
| | **Total** | **~28 person-days** (≈ 12-15 with agents drafting code under review) | |

Tasks 1, 4, 5, 9, 10 can start immediately with no hardware. Critical path to Gate "calibration done": 2 → 3 → 6 → 14 → 15.

### 9.2 Risks

| Risk | Effect | Mitigation |
|---|---|---|
| SSH/sshd or network perturbs SUT | Biased numbers | Close sessions, quiesce, wired only; A/A test sshd on vs off in calibration |
| Fixed 100 ms key cadence phase-locks to refresh | Aliased sequence-key results | Jitter cadence (open decision) |
| p50 jumps a frame between days (multimodal) | Fails 2 ms criterion spuriously | Mode-boundary flag, trimmed mean, discuss criterion |
| Single USB port on MCU | No orchestrator link without extra hardware | Isolated UART adapter; raise with hardware workstream now |
| Ground loop between two computers via rig | Photodiode noise, false edges | Galvanic isolation on orchestrator link |
| Window mode / fullscreen changes compositor path | Unfair or unrepresentative web rung numbers | Fix and record mode; test both on floor pages |
| Agent eval loop false accepts from many comparisons | Noise accepted as gains | A/A false-accept tracking; second-machine confirmation |
| Raw data volume with waveforms (~3 MB/block, thousands of blocks) | Storage/publishing cost | Object store, zstd; fine at this scale (low GBs) |
| macOS SSH/env capture needs sudo (powermetrics) | Missing metadata | Best-effort fields, marked absent not guessed |
| Drift within a session (thermal) | Order effects | Randomized order + sentinel blocks + ACF check |

### 9.3 Open decisions for a human

1. **Headline Scenario A action:** single first keystroke into empty palette (recommended) vs all keys pooled vs sequence keys only.
2. **Key cadence jitter** for the 20-char sequence: accept jitter (e.g. U[90,117] ms) vs keep exact 100 ms as spec'd.
3. **Two-day criterion statistic:** keep p50 ≤ 2 ms, or use mean/trimmed mean given frame-quantized multimodality.
4. **Trials for p99:** is 500 enough for a headline p99, or raise to 1,000 for headline blocks?
5. **Browser window mode:** fullscreen vs windowed (and kiosk/app mode); changes the Windows presentation path.
6. **Serving web rungs:** local static server on SUT vs `file://` vs R1's `next start` server.
7. **SUT control:** approve SSH-between-blocks approach, or require manual launching for headline sessions.
8. **Results storage:** object store (S3/R2) + index in git vs git LFS vs DVC; and whether raw data is public from day one (ties to spec open question on publishing).
9. **Calibration validity window:** how many days a rig LED calibration stays valid, and whether a mount repositioning forces recalibration.
10. **Protocol ownership:** confirm `protocol/` is co-owned by firmware and orchestrator owners, with CI on both sides against shared vectors.
11. **Waveform capture by default:** yes (recommended) vs only on demand.
