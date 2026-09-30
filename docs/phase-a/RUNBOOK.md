# Phase A runbook: what to run on your laptop

Everything below runs on the X1 (Linux, KDE on Wayland) or the MacBook Air. The X1 is the reference machine. Detail lives in each tool's README; this page is the order to do things in. Budget about half a day in total, most of it hands-off.

## 0. Get the code and build once (~10 min, needs Node 22)

```sh
git clone -b ccr-5269ff1c-5ip7qi https://github.com/cawarren/light-ux
cd light-ux
npm install
(cd rungs/r1-typical && npm ci && npm run build)
(cd rungs/r1-vite && npm ci && npm run build)
(cd rungs/r2-diligent && npm ci && npm run build)
(cd rungs/r3-no-framework && npm ci && npm run build)
node rungs/scripts/prepare.mjs          # after the builds: dataset + probe into each rung
python3 playground/play.py check        # should say "ok" for r1, r2, r3 and the dataset
```

Install current Google Chrome stable (version 145 or later).

## 1. A1 check (~5 min) → send the report

Confirms the measurement assumptions on this machine. See [`tools/a1-check/README.md`](../../tools/a1-check/README.md).

- **X1:** once, `sudo modprobe uinput; sudo setfacl -m u:$USER:rw /dev/uinput`. Then `python3 tools/a1-check/a1_check.py --label 120hz`, and again at 60 Hz with `--label 60hz`.
- **Mac:** `python3 tools/a1-check/a1_check.py --label mac`; grant your terminal Accessibility access when asked, restart the terminal, run again.
- Click once in the "A1 check" Chrome window, then hands off.
- **Send back:** `tools/a1-check/reports/a1-*.json`.

If a row fails, stop here and send the report; the harness refuses to run on the assumptions that matter.

## 2. Measurement session (~1–2 h, mostly hands-off) → send the tarball

See [`orchestrator/README.md`](../../orchestrator/README.md) ("A real session").

Prepare the laptop: AC power, performance profile, apps closed, Do Not Disturb, whole-number display scaling, Night Light off, cold boot on the X1. Park the mouse at the screen edge.

```sh
cd orchestrator
# rehearsal, ~3 min
python3 -m ladder soft run --machine x1 --hz 60 --rungs r3-no-framework --sizes 10k --first-key 20 --seq 1 --warmup 5 --reps 1
# headline, 45–90 min
python3 -m ladder soft run --machine x1 --hz 60 --display maximized --rungs r1-typical,r1-vite,r2-diligent,r3-no-framework --sizes 10k,50k
# optional, same again at 120 Hz after switching the panel
python3 -m ladder soft run --machine x1 --hz 120 --display maximized --rungs r1-typical,r1-vite,r2-diligent,r3-no-framework --sizes 10k,50k
tar czf ladder-x1-sessions.tgz -C results sessions
```

On the Mac use `--machine mba`. **Send back** the tarball (timings and system info only; no hostname or personal files).

## 3. Blind playground (~45 min per session) → send the results

See [`playground/README.md`](../../playground/README.md).

```sh
python3 playground/play.py              # opens http://localhost:8765
```

1. **Open mode** first, to get a feel for each rung (optional, any length).
2. **Latency calibration (JND):** R3 against R3 plus a hidden delay. Finds the smallest delay you can notice.
3. **Blind mode:** four pairs × 40 trials, forced choice. Keep DevTools closed. Take the breaks.
4. Optionally a second blind session at 50k (`--size 50k`).

```sh
python3 playground/play.py report playground/results
```

**Send back** `playground/results/` (trial answers and timings only).

## What happens next

With the A1 report, session tarball and playground results, the harness report fills in the Gate A table: the gap between R1 and R3 at p95, and whether you could tell them apart blind (both required). Then the first Performance Olympics runs on R2 and R3 ([`docs/olympics.md`](../olympics.md)), and after it, the Gate A review.
