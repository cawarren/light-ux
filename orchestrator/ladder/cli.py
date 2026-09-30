"""`ladder` command line (argparse, standard library only).

  python3 -m ladder soft run      [options]          measure (runs on the device under test)
  python3 -m ladder soft plan     [options]          print/write the seeded plan only (dry run)
  python3 -m ladder soft analyze  SESSION...         raw -> derived/trials.csv (+ .parquet)
  python3 -m ladder soft report   SESSION... --out F Gate A table, CIs, plots (one HTML file)
  python3 -m ladder soft stages   SESSION            T sessions: trace -> derived/stages.csv (needs perfetto)
  python3 -m ladder soft selftest                    injector scheduling jitter only
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def add_run_args(p, simulate_defaults=False):
    g = p.add_argument_group("session")
    g.add_argument("--machine", default="x1", help="machine id recorded in the manifest (default x1)")
    g.add_argument("--hz", type=float, default=60, help="nominal refresh rate you set for this session (60/120)")
    g.add_argument("--display", choices=["maximized", "fullscreen"], default="maximized")
    g.add_argument("--rungs", type=_csv, default=["r1-vite", "r2-diligent", "r3-no-framework"],
                   help="comma list (r1-typical proxies `next start`)")
    g.add_argument("--sizes", type=_csv, default=["10k", "50k"], help="comma list of 1k,10k,50k")
    g.add_argument("--first-key", type=int, default=300, help="A.first_key trials per rung x size (each + A.clear)")
    g.add_argument("--seq", type=int, default=5, help="A.seq sequences (40 keys each) per rung x size")
    g.add_argument("--warmup", type=int, default=50, help="warm-up first_key trials per block (excluded)")
    g.add_argument("--reps", type=int, default=2, help="blocks per rung x size (ABBA order)")
    g.add_argument("--max-keys", type=int, default=40, help="keys per segment (clock sync + read-back between)")
    g.add_argument("--seed", type=int, default=None, help="session seed (default random, recorded)")
    g.add_argument("--seed-id", default="dev-1", help="dataset seed id under dataset/out/")
    g.add_argument("--queries", default=None, help="queries.json (default dataset/out/<seed-id>/queries.json)")
    g.add_argument("--out", default=os.path.join(ORCH, "results", "sessions"))
    g.add_argument("--notes", default=None)
    g = p.add_argument_group("chrome and input")
    g.add_argument("--chrome", default=None, help="Chrome binary (default: auto-detect)")
    g.add_argument("--trace", action="store_true", help="T session: CDP attached + Chrome tracing (attribution only)")
    g.add_argument("--trace-config", choices=["perfetto", "json"], default="perfetto")
    g.add_argument("--sudo-injector", action="store_true", help="Linux: run only the uinput injector under sudo")
    g.add_argument("--injector-cpu", type=int, default=None, help="pin the injector to this CPU (default: fastest non-0)")
    g.add_argument("--simulate", action="store_true",
                   help="DEV ONLY: CDP keys into headless Chromium; every output labelled NOT REAL")
    g = p.add_argument_group("timing")
    g.add_argument("--settle-timeout-ms", type=float, default=None, help="default 2000 (5000 for r1*)")
    g.add_argument("--lead-in-ms", type=float, default=300)
    g.add_argument("--sync-n", type=int, default=20, help="clock-sync round trips before/after each segment")
    g.add_argument("--jitter-n", type=int, default=2000)
    g.add_argument("--quiet-s", type=float, default=2.0, help="idle after page load before the first segment")
    g.add_argument("--idle-s", type=float, default=10.0, help="idle between blocks (phase-a §4.2)")
    g = p.add_argument_group("overrides (recorded; see orchestrator/README.md)")
    g.add_argument("--allow-old-chrome", action="store_true")
    g.add_argument("--allow-fallback-timing", action="store_true")
    g.add_argument("--allow-odd-timestamps", action="store_true")
    g.add_argument("--yes", action="store_true", help="do not wait for Enter at the start")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ladder", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group")
    soft = sub.add_parser("soft", help="Phase A software harness")
    ss = soft.add_subparsers(dest="cmd")

    p = ss.add_parser("run", help="run a measurement session")
    add_run_args(p)
    p = ss.add_parser("plan", help="write the seeded plan without running it")
    add_run_args(p)
    p.add_argument("--plan-out", default="-")

    p = ss.add_parser("analyze", help="derive trials.csv from raw session data")
    p.add_argument("sessions", nargs="+")
    p.add_argument("--no-correctness", action="store_true")
    p = ss.add_parser("report", help="HTML report over one or more sessions")
    p.add_argument("sessions", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--perceptibility", default=None, help="blind 2AFC results JSON from the playground")
    p.add_argument("--headline-size", default="50k")
    p.add_argument("--headline-hz", type=float, default=60)
    p.add_argument("--resamples", type=int, default=10000)
    p.add_argument("--no-correctness", action="store_true")
    p = ss.add_parser("stages", help="T sessions: parse traces with trace_processor (optional dependency)")
    p.add_argument("session")
    p = ss.add_parser("selftest", help="injector scheduling jitter self-test")
    p.add_argument("--n", type=int, default=2000)

    a = ap.parse_args(argv)
    if a.group != "soft" or not a.cmd:
        ap.print_help()
        return 2
    if a.cmd == "run":
        from .soft.runner.session import Refuse, Session
        if a.simulate:
            a.idle_s = min(a.idle_s, 1.0)
            a.quiet_s = min(a.quiet_s, 1.0)
        try:
            Session(a).run()
        except Refuse as e:
            print("\nREFUSED: %s" % e, file=sys.stderr)
            return 3
        return 0
    if a.cmd == "plan":
        from .soft.runner import schedule
        pools = schedule.load_query_pools(a.queries or os.path.join(os.path.dirname(ORCH), "dataset", "out", a.seed_id, "queries.json"))
        seed = a.seed if a.seed is not None else 1
        plan = schedule.plan_session(seed, a.rungs, a.sizes, pools, a.reps, a.first_key, a.seq, a.warmup, a.max_keys)
        text = json.dumps(plan, indent=1)
        if a.plan_out == "-":
            summary = {"seed": seed, "block_order": plan["block_order"],
                       "keys_per_block": {b["block_id"]: b["counts"]["keys"] for b in plan["blocks"]}}
            print(json.dumps(summary, indent=1))
        else:
            open(a.plan_out, "w").write(text)
        return 0
    if a.cmd == "selftest":
        from .soft.runner.injector import jitter_selftest
        print(json.dumps(jitter_selftest(a.n)))
        return 0
    if a.cmd == "analyze":
        from .soft.analysis import build
        for s in a.sessions:
            build.analyze_session(s, check_correctness=not a.no_correctness, verbose=True)
        return 0
    if a.cmd == "report":
        from .soft.analysis import report
        report.build_report(a.sessions, a.out, perceptibility=a.perceptibility, headline_size=a.headline_size,
                            headline_hz=a.headline_hz, resamples=a.resamples, check_correctness=not a.no_correctness)
        return 0
    if a.cmd == "stages":
        from .soft.analysis import stages
        return stages.main(a.session)
    return 2
