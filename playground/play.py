#!/usr/bin/env python3
"""Latency Ladder Phase A playground (docs/phase-a/README.md §5). Python 3 stdlib only.

  python3 playground/play.py                      serve and open the default browser
  python3 playground/play.py serve --no-browser   serve and print the URL
  python3 playground/play.py serve --chrome /path/to/chrome   launch that Chrome, fresh profile
  python3 playground/play.py report playground/results [--out summary.json]
  python3 playground/play.py check                what is built / missing

See playground/README.md.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playlib import config as C  # noqa: E402

MIN_PY = (3, 9)


def check() -> int:
    ok = True
    print(f"repo: {C.REPO}")
    for r in C.RUNGS.values():
        state = "ok" if r.available else "MISSING (build it, see playground/README.md)"
        print(f"  {r.id}: {r.label:40} {r.dist.relative_to(C.REPO)}  {state}")
        ok &= r.available
    d = C.dataset_dir()
    print(f"  dataset: {d.relative_to(C.REPO) if d else 'MISSING: node rungs/scripts/prepare.mjs'}")
    ok &= d is not None
    for w in C.dataset_consistency():
        print(f"  WARNING: {w}")
    print(f"  probe: {'ok' if (C.SHARED / 'ladder-probe.js').is_file() else 'MISSING'}")
    print(f"  prompts: {len(C.load_prompts())} typeable 5–8 character queries")
    return 0 if ok else 1


def launch_chrome(binary: str, url: str) -> subprocess.Popen:
    """Launch a Chrome with a fresh profile and only the flags phase-a §4.2 allows."""
    exe = shutil.which(binary) or binary
    profile = tempfile.mkdtemp(prefix="ladder-play-")
    args = [exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_serve(a) -> int:
    from playlib.server import serve

    for w in C.dataset_consistency():
        print(f"WARNING: {w}")
    missing = [r.id for r in C.RUNGS.values() if not r.available]
    if missing:
        print(f"WARNING: not built: {', '.join(missing)} (open mode works for the others; see README)")
    if C.dataset_dir() is None:
        print("WARNING: no dataset in the rung builds: run `node rungs/scripts/prepare.mjs`")
    defaults = {
        "size": a.size, "trials_per_pair": a.trials_per_pair, "jnd_method": a.jnd_method, "jnd_reps": a.jnd_reps,
        "jnd_levels": [int(x) for x in a.jnd_levels.split(",")] if a.jnd_levels else None,
        "delay_mode": a.delay_mode, "allow_no_difference": a.allow_no_difference, "break_every": a.break_every,
        "allow_backspace": a.allow_backspace, "show_marker": a.show_marker,
        "ready_ms": a.ready_ms, "settle_ms": a.settle_ms, "post_ms": a.post_ms, "seed": a.seed,
    }
    httpd = serve(a.host, a.port, Path(a.results), defaults, a.verbose)
    port = httpd.server_address[1]
    url = f"http://{'localhost' if a.host in ('127.0.0.1', 'localhost') else a.host}:{port}/"
    print(f"Playground at {url}   (results in {a.results}; Ctrl+C to stop)", flush=True)
    if a.chrome:
        launch_chrome(a.chrome, url)
    elif not a.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_report(a) -> int:
    from playlib.report import main

    return main(a.results, a.out, a.boot, a.min_trials, a.gate_size)


def main(argv=None) -> int:
    if sys.version_info < MIN_PY:
        print(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ required")
        return 2
    ap = argparse.ArgumentParser(prog="play.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sv = sub.add_parser("serve", help="serve the playground (default)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765, help="0 = any free port")
    sv.add_argument("--no-browser", action="store_true", help="only print the URL")
    sv.add_argument("--chrome", metavar="BINARY", help="launch this Chrome with a fresh profile instead of the default browser")
    sv.add_argument("--results", default=str(C.DEFAULT_RESULTS), help="directory for session JSONL files")
    sv.add_argument("--size", default="10k", choices=C.SIZES, help="dataset size for new sessions")
    sv.add_argument("--trials-per-pair", type=int, default=40)
    sv.add_argument("--jnd-method", default="constant", choices=("constant", "staircase"))
    sv.add_argument("--jnd-reps", type=int, default=20, help="trials per level (constant stimuli)")
    sv.add_argument("--jnd-levels", default=None, help="comma-separated ms, default 0,8,17,25,33,50,67,100")
    sv.add_argument("--delay-mode", default="defer", choices=("defer", "block"))
    sv.add_argument("--allow-no-difference", action="store_true", help="offer a logged 'no difference' answer (key 0)")
    sv.add_argument("--allow-backspace", action="store_true",
                    help="allow backspace/delete in blind trials (default: disabled, R1 list-order tell)")
    sv.add_argument("--show-marker", action="store_true",
                    help="show the latency marker in blind and calibration trials (default: covered)")
    sv.add_argument("--break-every", type=int, default=20)
    sv.add_argument("--ready-ms", type=int, default=3000, help="minimum get-ready screen per interval")
    sv.add_argument("--settle-ms", type=int, default=3000, help="hold after load completes (§5.2: 3 s)")
    sv.add_argument("--post-ms", type=int, default=1500, help="fixed pause after Enter before measuring")
    sv.add_argument("--seed", default=None, help="schedule seed for new sessions (default random, recorded)")
    sv.add_argument("--verbose", action="store_true")
    rp = sub.add_parser("report", help="summarize results JSONL files or directories")
    rp.add_argument("results", nargs="+")
    rp.add_argument("--out", default=None, help="summary JSON path (default <results dir>/summary.json)")
    rp.add_argument("--boot", type=int, default=1000, help="bootstrap resamples for the JND CI")
    rp.add_argument("--min-trials", type=int, default=40)
    rp.add_argument("--gate-size", default=None, choices=C.SIZES)
    sub.add_parser("check", help="list built rungs and dataset")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["serve", *argv]
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        return cmd_serve(a)
    if a.cmd == "report":
        return cmd_report(a)
    return check()


if __name__ == "__main__":
    sys.exit(main())
