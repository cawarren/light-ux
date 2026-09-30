# playground/: feel the rungs, blind 2AFC, latency calibration (Phase A §5)

The Phase A playground from [`docs/phase-a/README.md`](../docs/phase-a/README.md) §5. One command,
Python 3 standard library only, no install step. It serves the rungs' static production builds
from one cross-origin-isolated origin and offers three modes:

| Mode | What it is for | Writes results |
| --- | --- | --- |
| **Open** | Pick a rung, dataset size (1k / 10k / 50k) and optionally an added delay; type freely. The rung name and a latency HUD are shown. | no |
| **Blind test** (primary) | 2AFC "which felt faster": R1–R3, R1–R2, R2–R3 and the R3–R3 placebo, 40 trials per pair by default, counterbalanced and interleaved, server-side assignment. Latency marker covered, backspace off. | yes |
| **Latency calibration (JND)** | R3 vs R3 + N ms (N ∈ 0, 8, 17, 25, 33, 50, 67, 100), to find the smallest added latency you can feel. Latency marker covered, backspace allowed. | yes |

`python3 playground/play.py report playground/results` turns the results into the numbers Gate A
needs (§7.3): per-pair correct rate, one-sided exact binomial p, 95% Clopper–Pearson CI, d′, the
placebo test, the JND threshold with a bootstrap CI, and the **perceptibility verdict**.

## For the owner

### Build once (needs Node 22 and npm; takes a few minutes)

```sh
npm install                                          # repo root
(cd rungs/r1-vite && npm ci && npm run build)        # R1 (Vite variant, see "Design choices")
(cd rungs/r2-diligent && npm ci && npm run build)    # R2
(cd rungs/r3-no-framework && npm ci && npm run build)# R3
node rungs/scripts/prepare.mjs                       # dataset (1k/10k/50k) + probe into each dist/
python3 playground/play.py check                     # should say "ok" for r1, r2, r3 and the dataset
```

Run `prepare.mjs` **after** the builds (it refreshes `dist/`). Node is only needed for building;
the playground itself needs only `python3` (3.9 or later; macOS's system Python is fine).

### Run

```sh
python3 playground/play.py                 # opens your default browser at http://localhost:8765/
python3 playground/play.py --no-browser    # just print the URL
python3 playground/play.py --chrome /path/to/chrome   # recommended: the pinned Chrome ≥ 145, fresh profile
```

Use the pinned Chrome (≥ 145, phase-a §4.2) if you can: it reports Element Timing `presentationTime`,
which the latency figures use. Other Chromium builds fall back to `renderTime`; Firefox and Safari
work but only measure to the next animation frame. Fullscreen the window (F11) for blind sessions,
close other tabs of the playground, turn notifications off, and do not open DevTools during a
blind session (see "Tells").

Defaults: forced choice, dataset 10k, latency marker covered in blind and calibration trials,
backspace off in blind trials. Useful options: `--size 50k` (dataset for new sessions; the home
screen also toggles it with `S`), `--trials-per-pair 40`, `--jnd-method staircase`,
`--jnd-reps 20`, `--delay-mode block`, `--allow-no-difference`, `--allow-backspace`,
`--show-marker`, `--break-every 20`, `--seed …`, `--port`, `--results DIR`.
`python3 playground/play.py serve --help` lists all.

### A blind session

Press `B` on the home screen. The page first loads each palette once, unseen ("Preparing"), to
fix the get-ready time. Then each trial is:

1. **Interval 1.** A "get ready" screen with a progress bar, a fixed length for the whole sitting.
   The word to type is shown at the top. When the screen clears, the palette has focus: type the
   word (5–8 characters from the dataset's typeable queries) **in one go** and press `Enter`.
   Backspace and Delete do nothing in blind trials (a hint flashes). If you mistype, press `Esc`:
   the interval starts again from its get-ready screen with an empty palette, and nothing from the
   aborted attempt is kept (only a `restarts` count).
2. **Interval 2.** The same, with the other condition.
3. Press `1` or `2`: **which felt faster, more responsive?** Then `1`–`3`: how sure
   (guessing / fairly sure / sure). `Backspace` goes back to the first question.

There is no "no difference" answer unless the server was started with `--allow-no-difference`
(then `0`; logged and analysed separately). `Esc` on the get-ready, answer or break screens
pauses (the current trial restarts from its first interval); the home screen lists unfinished
sessions to resume (`1`–`9`). A break screen appears every 20 trials.

**Run a second blind session at 50k** (`python3 playground/play.py --size 50k`, or `S` on the
home screen) after the 10k one: §8.4 recommends measuring both sizes, and R1's gap is larger at
50k. The report keeps sizes apart and picks the size with the most R1–R3 trials for the verdict
(override with `--gate-size`).

**How long.** About 25–30 s per trial at 10k (two get-ready screens of ~3.5–5 s, two typing
intervals of ~3–5 s, a 1.5 s pause after each, the answer), so the default 160 trials
(4 pairs × 40) take **about 70–80 minutes**. Do it in **two sittings of ~40 minutes** (stop at the
break after trial 80 and resume later, ideally on another day). A 50k session is slower, mostly
because R1 takes seconds to load: the get-ready screen grows to hide that. The calibration
session (160 trials, R3 only) takes about the same; run it once at 60 Hz and once at 120 Hz
(§5.3), in a separate sitting from the blind test.

### What to send back

The files in `playground/results/`: one `<session-id>.jsonl` per session, plus the
`summary.json` that `python3 playground/play.py report playground/results` writes there. They
contain the schedule seed, answers, timings, the typed prompts and latency figures; nothing
personal (no user or host names, no IP, no user-agent string, only the browser's name and major
version). Please do not open the `.jsonl` files before you finish all sessions: they record which
rung was in which interval.

## How it works

### Serving (`playlib/server.py`)

- `ThreadingHTTPServer` on `127.0.0.1:8765`. Every response carries
  `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`,
  `Cross-Origin-Resource-Policy: same-origin` (decided for all rungs) and `Cache-Control: no-store`
  (every interval loads the same way, whatever was loaded before). The parent page and the rung
  frame both report `crossOriginIsolated === true`.
- The playground UI is `/` plus `/__play/*`. The rung runs in a same-origin `<iframe>` that fills
  the interval area.
- **Neutral paths.** Each interval gets a fresh random token: the rung page is `/t/<16 hex>/`
  (`?items=/dataset/<size>/items.json`). The server keeps token → (rung, added delay) in memory
  only. The rung's `index.html` is rewritten on the way out: `<title>Palette</title>`, HTML
  comments and the favicon link removed, `r3-list`-style ids renamed, and the playground helper
  (`/t/<token>/__i.js`) inserted as the first script. The rung builds use root-absolute asset paths
  (`/assets/…`, `/worker.js`), so those resolve against the rung of the token in the `Referer`, or
  else the most recently loaded rung page (only one is live at a time, hence "one tab").
- `/ladder/probe.js` and `/ladder/marker.json` come from `rungs/shared/` (harness-owned, identical
  for all rungs); `/dataset/…` from one rung's prepared copy (`check` warns if the copies differ).
- The JSON API (`/api/state`, `/api/open`, `/api/sessions/<id>/{open,warmup,next,answer}`) returns
  only tokens, prompts and progress in blind and calibration sessions. Correctness, the chosen rung
  and the assignment are added to the record server-side from the schedule.

### Blind 2AFC (`playlib/schedule.py`, `static/app.js`)

- The schedule is a pure function of `(seed, trials per pair, pairs, prompts)`, recorded in the
  results file header, so a session resumes by regenerating it and skipping answered trials. The
  seed is random unless `--seed` is given.
- Pairs (slower, faster by rung number): R1–R3, R1–R2, R2–R3 and R3–R3 (placebo). In each pair
  exactly half the trials put the slower rung first (`order` `AB`) and half second (`BA`); all
  160 trials are shuffled together, so pairs are interleaved (§5.2). Each trial draws one prompt,
  used in both intervals.
- Prompts: typeable queries of 5–8 characters from `queries.json` in the classes `word-prefix`
  and `multi-word-prefix` (72 in dev-1); the typo classes are awkward to type on purpose and
  `no-match` shows an empty list. The list is stored in the header.
- **Forced choice by default.** §5.2 makes "no difference" optional and §8.4 lists it as an open
  decision, so the default is forced choice; `--allow-no-difference` enables it. Confidence 1–3
  and response time (answer screen shown → key) are recorded as §5.2 asks.
- The "faster" rung of a pair is the higher rung number (the one with lower measured latency, the
  premise Gate A tests). Each record also has `chose_lower_measured_p50`: whether the answer
  picked the interval whose own measured p50 was lower.

**Tells and how they are handled**

| Possible tell | Mitigation |
| --- | --- |
| URL, title | Per-interval random token path; title "Palette" for every rung; parent title "Playground" |
| Load time (R1 at 50k takes seconds) | §5.2's fixed hold after load, made load-independent: a warm-up loads each rung once per sitting and the get-ready screen lasts `max(3 s, 1.25 × slowest load + 3 s)` (rounded up to 0.5 s) from the start of the interval, never less than 3 s after load completes. If a load still overruns, the trial records `ready_overrun: true` and the get-ready time grows for the rest of the sitting. |
| Visual differences | The rungs are built to match R1 pixel for pixel (R2, R3 READMEs); every interval is a fresh load, scrolled to the top, focused, same fonts, same dataset bytes, theme from the OS for all |
| Browser cache | `no-store` everywhere |
| **R1 list order after a backspace** | Stock cmdk re-inserts re-appearing items without re-sorting (rungs/README.md, "Known R1 deviation"), so after a backspace R1's list can be ordered differently from R2/R3 (seen in the e2e test): a content cue, not latency. **Removed by default:** in blind trials the helper swallows Backspace, Delete, select-all, shifted caret moves, cut and paste before the rung sees them (typing only ever narrows the query, so no item re-appears); `Esc` restarts the interval instead. `--allow-backspace` turns editing back on; the report still splits every pair `by_backspace`. |
| DevTools-only | Asset file names differ per rung (hashed `index-*.js` vs R3's `main.js` + `worker.js`); R3 renders `id="r3-opt-N"` rows and sets `window.__r3`. None is visible on the page; do not open DevTools during a blind session. |
| The latency marker square | **Covered by default** in blind and calibration trials (`--show-marker` to show it; open mode always shows it). The rung's code path is unchanged: the probe's marker is still created and flipped in the same frame as the list. The helper lays an opaque square of the page's background colour (sampled under the marker while the get-ready screen is up) over it, same geometry, later in the DOM at the same maximal z-index. `opacity: 0` or `visibility: hidden` on the marker was measured to stop its Element Timing entries (0 of 6); the cover keeps them (6 of 6). |

### Latency calibration / JND (`static/inject.js`)

§5.3: one rung (R3) against itself plus N ms, so latency is the only difference. The reference is
N = 0; the comparisons are 8, 17, 25, 33, 50, 67 and 100 ms, and N = 0 against 0 is included as a
catch (placebo) level: 8 levels × 20 = 160 trials (method of constant stimuli, order
counterbalanced per level). `--jnd-method staircase` runs a 1-up-3-down staircase instead (starts at
100 ms, converges on 79.4%, stops after 12 reversals or 60 trials).

Backspace stays allowed in calibration trials: both intervals are R3, so there is no list-order tell,
and it keeps typing natural (the added delay applies to backspace too). The marker is covered as in
blind trials; `Esc` restarts an interval the same way.

The delay is added **without touching rung code**, by the harness-owned helper the server injects
as the first script of the rung page:

- **defer (default, §5.3 "defer"):** a capture-phase `keydown` listener on `document` (it runs after
  the probe's window-capture listeners, which log the real keydown, and before any rung listener)
  calls `preventDefault()` + `stopPropagation()` on every key aimed at the rung's input and queues
  it. At the first animation frame where `now ≥ keydown.timeStamp + N` it re-applies the edit
  itself: new value through the native `HTMLInputElement` value setter (so React-controlled inputs
  see it), caret via `setSelectionRange`, then an `input` event with the matching `inputType`.
  Supported: characters (incl. Shift), Backspace, Delete, Ctrl/Alt+Backspace (word), Cmd+Backspace,
  ArrowLeft/Right, Ctrl/Cmd+A. ArrowUp/Down, PageUp/Down, Home, End, Escape and Enter are
  re-dispatched to the input as synthetic `keydown`s after the same delay, in order. Both the echo
  in the input and the list update are therefore delayed, as on a slower system, and the delay is
  frame-quantized (N to N + 1 frame). With **N = 0 the edit is applied synchronously** inside the
  keydown handler: same code path as the comparison, no waiting.
- **block (`--delay-mode block`, §5.3 "block"):** busy-waits N ms in a capture `keydown` handler,
  like main-thread cost; it also delays the following input.

**Verified** (headless Chromium 141, `tests/e2e.mjs`, `tests/out/e2e-delay.json`): the same key
sequence (`conffig`, 3 × ArrowLeft, Backspace, 3 × ArrowRight, `u`, Backspace) gives the value
`config` and the identical result list (same flip digest) natively and in defer N = 0, defer
N = 50 and block N = 50 on R3, and in defer N = 50 on R1 and R2. At N = 0 the keydown → value
applied time equals native editing (median 0.45 ms vs 0.48 ms native; keydown timestamp → value
0.84 vs 0.85 ms), and the key → marker-frame p50 is within a frame of native. At N = 50 every key
was applied 50.6–65.9 ms after its keydown (60 Hz frames) and the p50 latency grew by ~50 ms.
Block N = 50: input handled ≥ 50.8 ms after keydown. In interval mode `Enter` ends the interval
and never reaches the rung.

**Caveats of the defer mode.** IME composition cannot be re-timed (Chrome does not let it be
cancelled): intervals that used it are flagged `ime: true`. Paste, cut, drop, undo/redo, shifted
or word-wise caret moves are blocked (counted in `blocked`). The re-applied `input` and the
re-dispatched key events are untrusted (`isTrusted === false`); none of the rungs checks that, but
a rung that did would break. The browser's own undo history does not see the edits. Key repeat
works. The owner's typing speed matters: at large N, typed keys queue up just as on a slow system.

### Measurement in the page

Per interval the helper attributes every value-changing key to the first latency-marker flip whose
query includes it (so coalesced keys count, phase-a §4.5) and reports `key event.timeStamp →
frame of that flip`, with the end point Element Timing `presentationTime` > `paintTime` >
`renderTime` > next rAF. As §5.1 says, this leaves out OS delivery (the page cannot see the key
before the browser reads it); the harness M sessions remain the headline numbers. After `Enter`
the page waits a fixed 1.5 s (`--post-ms`) before reading, so the wait carries no information.

### Statistics (`playlib/stats.py`, `playlib/report.py`)

- Blind pairs: correct = chose the higher-numbered rung; one-sided exact binomial (H1: rate > 0.5);
  95% Clopper–Pearson CI; d′ = √2 Φ⁻¹(Pc) (rates of 0 or 1 moved by 1/2n); breakdowns by order,
  sitting, confidence and backspace use; a logistic fit of P(correct) against the trial's measured
  Δp50 (§5.2). Only forced-choice answers count.
- Placebo (R3–R3): rate of choosing interval 1, exact two-sided binomial against 0.5.
- JND: per level rate, CI, one-sided p, median applied delay and measured Δp50. Psychometric
  function p(x) = 0.5 + (0.5 − λ)·σ(a + b·x): guess rate fixed at 0.5, **lapse rate λ free in
  [0, 0.06]** (profile likelihood over λ: a 13-point grid, then golden-section search; a and b by
  Fisher scoring). The threshold is the x where p = 0.75. Reported **with the lapse term (`fit`,
  primary, used for the Gate A comparison) and without it (`fit_no_lapse`, λ = 0)**, each with a
  95% percentile bootstrap CI (1,000 resamples within levels, `--boot`; about 5 s).
  `lapse_at_bound` flags a fit that wanted more than 0.06. Grouped by delay mode and, when both
  occur, by refresh rate (from the measured frame period). Staircase sessions also report the mean
  of the last 6 reversals.
- Pooled across sessions of the same dataset size, with per-session breakdowns (§5.2 allows pooling
  or per-day reporting).

**Gate A perceptibility verdict** (§7.3, hard requirement), per the dataset size with the most
R1–R3 trials (or `--gate-size`):
`pass` if R1–R3 has ≥ 40 forced-choice trials with one-sided p < 0.05 **and** the R3–R3 placebo has
≥ 40 trials with two-sided p ≥ 0.05; `fail` if either condition has ≥ 40 trials and fails;
otherwise `incomplete`. The supporting check "measured Δ above the owner's JND" is reported as
`delta_vs_jnd.above` (playground Δp50 R1 − R3 vs the fitted JND) and does not change the verdict;
the harness should substitute its own M-session Δ.

## Data formats

### Results: `playground/results/<session-id>.jsonl`

Line 1, the session header:

```json
{"schema": "ladder.playground/1", "record": "session", "session_id": "blind-20261001T091500-3fa2",
 "kind": "blind", "seed": "9c1e44d0", "created_at": "2026-10-01T09:15:00.123+00:00",
 "total_trials": 160,
 "config": {"dataset_size": "10k", "trials_per_pair": 40, "pairs": [["r1","r3"],["r1","r2"],["r2","r3"],["r3","r3"]],
            "allow_no_difference": false, "allow_editing": false, "marker_hidden": true,
            "break_every": 20, "prompts": ["…"], "prompt_classes": ["…"],
            "timing": {"ready_ms": 3000, "settle_ms": 3000, "post_ms": 1500}},
 "rungs": {"r1": {"label": "…", "dist": "rungs/r1-vite/dist", "build_id": "<sha256(index.html)[:16]>"}, "…": {}}}
```

JND headers have `kind: "jnd"` and `config.method` (`constant` | `staircase`), `delay_mode`
(`defer` | `block`), `levels`, `reps` (or `max_trials`, `max_reversals`), `rung: "r3"`, and
`allow_editing: true`.

Then one line per answered trial:

| Field | Meaning |
| --- | --- |
| `schema`, `record` | `"ladder.playground/1"`, `"trial"` |
| `session_id`, `kind`, `seed`, `trial_index`, `sitting` | identity; `sitting` counts resumes |
| `pair_name` | `R1-R3`, `R1-R2`, `R2-R3`, `R3-R3` (blind) or `JND-<N>` |
| `pair` (blind) / `level_ms`, `delay_mode` (jnd) | condition |
| `order` | blind: `AB` = the pair's first (slower) rung in interval 1, `BA` otherwise; jnd: `RC` = reference (no delay) first, `CR` otherwise |
| `rungs`, `added_ms` | per interval (index 0 = interval 1) |
| `dataset_size`, `prompt`, `typed` | size; the shown prompt; the final text of each interval |
| `answer` | 1 or 2 (interval judged faster); `null` only with `no_difference: true` |
| `confidence` | 1 guessing, 2 fairly sure, 3 sure |
| `rt_ms` | answer screen shown → answer key |
| `expected_faster_interval` | interval with the higher rung number (blind) or without the delay (jnd); `null` for R3–R3 and N = 0 |
| `correct`, `chosen_rung` | derived server-side; `correct` is `null` when there is no right answer |
| `chose_lower_measured_p50` | whether the answer picked the interval with the lower measured p50 |
| `intervals[]` | `interval`, `rung`, `added_ms`, `load_ms`, `hold_ms`, `ready_ms`, `ready_overrun`, `typing_ms`, `restarts` (Esc restarts before the recorded attempt), and `latency`: `p50`, `p95`, `mean`, `max`, `lat_ms[]` (per measured key), `endpoint` (`presentation`/`paint`/`render`/`raf`), `n_keys`, `n_value_keys`, `n_measured`, `n_coalesced`, `n_noop`, `n_pending`, `n_backspace`, `applied_delay_ms[]`/`applied_delay_p50` (keydown → value applied), `handler_to_apply_ms[]`, `echo_ms[]`/`echo_p50` (keydown → next frame after the value changed), `et_input_delay_p50`, `frame_ms`, `blocked`, `edit_blocked` (swallowed edit keys), `marker_hidden`, `ime`, `mode`, `cross_origin_isolated` |
| `t_trial_start`, `t_answer` | ISO 8601 timestamps |
| `client` | `browser` (name + major version), `cross_origin_isolated`, `frame_ms`, `dpr`, `viewport` |

### Summary: `summary.json` (`"schema": "ladder.playground.summary/1"`), for `ladder soft report`

```text
schema, generated_at, inputs[], n_trials {blind, jnd}
sessions[]            {session_id, kind, seed, created_at, dataset_size, method, delay_mode, n_trials, total_trials, rungs}
blind.by_size.<size>.pairs.<pair_name>
  non-placebo         {n, correct, rate, ci95 [lo, hi], p_one_sided, significant, d_prime, d_prime_corrected,
                       faster_rung, n_total, n_no_difference, confidence_mean, rt_ms_median,
                       measured {delta_p50_ms_median, delta_p50_ms_iqr, n_with_latency, interval_p50_ms_median_by_rung},
                       by_order, by_sitting, by_confidence, by_backspace, chose_lower_measured_p50  (each {n, k, rate, ci95}),
                       fit_vs_measured_delta {threshold_75_ms, a, b, converged, reason, n, model}}
  placebo (R3-R3)     {placebo: true, n, chose_interval_1, rate_interval_1, ci95, p_two_sided, at_chance, measured, …}
blind.by_size.<size>.by_session.<session_id>.<pair_name>   compact {n, correct, rate, p_one_sided | chose_interval_1, p_two_sided}
jnd.by_mode.<defer|block>
  all                 {n, levels.<N> {n, k, correct, rate, ci95, p_one_sided, measured_delta_p50_ms_median, applied_delay_ms_median}
                         (level "0": {catch: true, chose_interval_1, rate, ci95, p_two_sided, at_chance}),
                       fit {threshold_75_ms, ci95, n_boot, n_failed, a, b, lapse, lapse_at_bound, converged, reason, model, note?},
                       fit_no_lapse {same, lapse = 0}, staircase?}
  by_hz.<hz>          same as "all", when sessions ran at more than one refresh rate
  methods[]
gate_a_perceptibility {rule, dataset_size, alpha, min_trials, verdict: "pass"|"fail"|"incomplete",
                       r1_r3_above_chance {n, correct, p_one_sided, enough_trials, pass},
                       placebo_at_chance {n, chose_interval_1, p_two_sided, enough_trials, pass},
                       delta_vs_jnd {delta_p50_ms, delta_source, jnd_ms, jnd_ci95, jnd_mode, above}}
validation_problems[] schema problems found in the input (empty when clean)
```

Rates and p-values are rounded to 4 and 6 decimals.

## Tests

```sh
python3 -m unittest discover -s playground/tests -t playground   # 28 tests, ~3 s
node playground/tests/e2e.mjs                                     # headless, ~1–2 min
```

- `test_schedule.py`: seeded and reproducible, exact order counterbalancing per pair (odd counts
  within one), pairs interleaved, JND levels × reps balanced, staircase rules, and over HTTP: `next`
  and `warmup` responses contain only tokens (no rung, delay, level or order), COOP/COEP headers,
  rewritten rung page (neutral title, helper before the probe, no rung names), helper config
  per mode (blind: no editing, marker covered; JND: editing, covered; open: shown, editing).
- `test_stats.py`: exact binomial tails (vs `Fraction` arithmetic and R's `binom.test`: 26/40 →
  p = 0.0403; the critical count at n = 40 is 26), two-sided test, Clopper–Pearson (7/10 →
  [0.3475, 0.9333]; 26/40 → [0.4832, 0.7937]), d′, type-7 quantiles, the psychometric fit recovering
  exact parameters (threshold 37.5 ms), the lapse fit recovering λ = 0, 0.021, 0.045 and the 75%
  point, λ held at its 0.06 bound, aggregation preserving the likelihood, bootstrap CI, degenerate
  fits, staircase reversals.
- `test_results.py`: record schema and validation, nothing personal kept, idempotent answers,
  only the pending trial accepted, resume from disk with a new sitting, the defaults (forced choice,
  10k, blind editing off, JND editing on, marker hidden) and their overrides, no-difference gating,
  staircase sessions, and the report: Gate A `pass` / `fail` (R1–R3 25/40, or a biased placebo) /
  `incomplete` (< 40 trials), the JND threshold with and without lapse and the Δ-vs-JND check,
  `summary.json` written.
- `e2e.mjs` (Playwright from the root `node_modules`, Chromium from `/opt/pw-browsers`, never
  `playwright install`; override with `LADDER_CHROME`): 3 blind trials and 1 calibration trial
  driven by keyboard only (typing with a typo and a backspace), leak checks in every interval
  (parent and frame URL, title, visible text outside the result list, resource paths, every
  request path the page made; frame title `Palette`; both documents cross-origin isolated);
  Backspace swallowed in blind intervals with the hint shown; `Esc` mid-interval restarts it with an
  empty input and the record keeps only `restarts: 1` and the clean attempt's keys; the covered
  marker's pixels stay identical across flips while Element Timing entries keep arriving (and in
  open mode the marker is visible and changes); `Esc` on the get-ready screen pauses; JND intervals
  keep backspace; the results file and `report` output; and the delay measurements above.

Result on 2026-09-30 (headless Chromium 141, 4 vCPU): 28/28 unit tests; 74/74 e2e checks, three
runs in a row.

## Caveats

- **R1 is the Vite variant** (`rungs/r1-vite`): the Next.js R1 needs `next start` and cannot be
  served statically. Its React/cmdk code is identical after first render (rungs/r1-vite/README.md);
  Next adds cold-start cost only, which the blind protocol hides anyway.
- Playground latencies start at `event.timeStamp` (on Wayland: when Chrome read the key), not at the
  key press, and on Chrome < 145 end at `renderTime` rather than presentation. They are for relating
  answers to latency within a trial, not for Gate A condition 1.
- The playground runs on the machine it measures; the page's own work (the HUD in open mode,
  nothing during blind intervals) is small but non-zero.
- One playground tab at a time (root-absolute rung assets resolve against the last loaded rung).
- "Placebo at chance" means "not significantly different from 0.5" with 40 trials, which is weak
  evidence of no bias; look at `rate_interval_1` too.
- With the marker covered, a 128-device-px square at the top-left of the palette area shows the
  page background instead of the rung's content under it (the marker hid that content anyway).
  The cover's colour is sampled once per interval, before typing; switching the OS theme in the
  middle of an interval re-samples it.

## Decided defaults (2026-09-30)

1. **Forced choice**; "no difference" only with `--allow-no-difference` (answers excluded from the tests).
2. **Dataset 10k**, plus a second blind session at 50k.
3. **Latency marker covered** in blind and calibration trials (`--show-marker` to show it); visible in open mode.
4. **Backspace/Delete off in blind trials** (`--allow-backspace` to allow); `Esc` restarts the
   interval. Allowed in calibration trials (R3 vs R3, no tell) and in open mode.
5. **JND fit with a bounded lapse term** (λ ∈ [0, 0.06], guess rate 0.5), reported with and without it.
