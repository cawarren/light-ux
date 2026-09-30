#!/usr/bin/env python3
"""Latency Ladder, Phase A task A1: on-device check of the [verify] assumptions
in docs/phase-a/README.md, on the owner's Linux laptop or MacBook.

    python3 tools/a1-check/a1_check.py                 # the real check
    python3 tools/a1-check/a1_check.py --label 120hz   # tag a second run
    python3 tools/a1-check/a1_check.py --simulate      # dev only: CDP keys, headless

Standard library only. See tools/a1-check/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from a1lib import analysis, chrome as chromelib, clock, envinfo, injector, keymap  # noqa: E402
from a1lib.cdp import CDP  # noqa: E402
from a1lib.server import PageChannel, make_server  # noqa: E402

VERSION = "a1-check 1"


def say(msg=""):
    print(msg, flush=True)


def banner(msg):
    say("\n== " + msg)


def ask_yes(prompt, default=False, assume=None):
    if assume is not None:
        return assume
    try:
        ans = input(prompt + (" [Y/n] " if default else " [y/N] ")).strip().lower()
    except EOFError:
        print(flush=True)
        return default
    return default if not ans else ans.startswith("y")


def make_schedule(n, seed, mac):
    rng = random.Random(seed)
    sched = []
    for i in range(n):
        sched.append({
            "i": i,
            "letter": rng.choice(keymap.LETTERS),
            "gap_ms": round(rng.uniform(50, 250), 3),   # key-up -> next key-down (spec U[50,250])
            "hold_ms": round(rng.uniform(30, 60), 3),   # D-B 17: shorter than auto-repeat delay
            # macOS only: create the event, wait, then post it (tests which timestamp Chrome uses)
            "post_delay_ms": (4 if rng.random() < 0.5 else 0) if mac else 0,
        })
    return sched


def step(rep, name, fn):
    """Run one independent step; record failures and carry on."""
    try:
        return fn()
    except Exception as e:
        rep["errors"][name] = "%s: %s" % (type(e).__name__, e)
        rep["errors"][name + "_trace"] = traceback.format_exc(limit=6)
        say("   ! %s failed: %s" % (name, e))
        return None


def wait_for_focus(ch, simulate, timeout=180):
    say("   Click once inside the Chrome window titled 'A1 check' so it has keyboard focus.")
    say("   Then keep your hands off the keyboard and mouse until the table is printed.")
    streak, end, last_note = 0, time.time() + timeout, 0
    while time.time() < end:
        st = ch.call("focus_state", timeout=15)
        streak = streak + 1 if st.get("hasFocus") and st.get("visibility") == "visible" else 0
        if streak >= 4:
            return True
        if time.time() - last_note > 10 and not streak:
            say("   ...waiting for the Chrome window to be focused (click inside it).")
            last_note = time.time()
        time.sleep(0.3)
    return False


def run(args, rep):
    is_linux, is_mac = clock.IS_LINUX, clock.IS_MAC
    simulate = args.simulate
    rep["host"] = step(rep, "host_env", envinfo.collect)

    # ---- Chrome
    path = chromelib.find_chrome(args.chrome)
    if simulate and not path:
        path = chromelib.find_test_chromium()
    if not path:
        say("Could not find Google Chrome. Pass its path with --chrome PATH.")
        rep["errors"]["chrome"] = "not found"
        return
    rep["chrome"] = {"path": path, "version": chromelib.version_of(path)}
    say("Chrome: %s  (%s)" % (rep["chrome"]["version"], path))

    if os.geteuid() == 0 and not simulate:
        say("Do not run this script as root/sudo: Chrome must run as you, with its sandbox.")
        say("On Linux use --sudo-injector (only the key injector gets sudo) or the setfacl fix in the README.")
        rep["errors"]["root"] = "refused to run as root"
        return

    # ---- Injector (created early so the compositor has registered the device)
    inj = None
    if not simulate and not args.no_inject:
        banner("Preparing the key injector")
        if is_linux:
            from a1lib import uinput
            prob = uinput.access_problem()
            if prob and not args.sudo_injector:
                say(uinput.fix_instructions(prob))
                rep["errors"]["injector"] = "uinput " + prob
        if "injector" not in rep["errors"]:
            proc = injector.InjectorProcess(os.path.abspath(__file__), use_sudo=args.sudo_injector and is_linux)
            ok = step(rep, "injector_start", proc.start)
            rep["injector"] = {"info": proc.info, "error": proc.error}
            if ok:
                inj = proc
                say("   injector ready: %s" % json.dumps(proc.info))
            else:
                if proc.error == "no_accessibility":
                    from a1lib import quartz
                    say(quartz.FIX_INSTRUCTIONS)
                else:
                    say("   injector failed: %s" % proc.error)
                rep["errors"]["injector"] = proc.error
                proc.close()
        if inj is None:
            if not ask_yes("Continue WITHOUT injected keys? Checks 4-6 will be UNKNOWN. "
                           "(Better: fix the permission above and rerun.)", False):
                rep["errors"]["stopped"] = "owner chose to fix injector permission first"
                return
    rep.setdefault("injector", {})

    # ---- Server + Chrome
    ch = PageChannel()
    srv = make_server(ch, args.port)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    rep["meta"]["url"] = url
    no_sandbox = simulate and os.geteuid() == 0
    if no_sandbox:
        rep["meta"]["sandbox_weakened"] = "--no-sandbox (simulate mode as root only)"
    banner("Launching Chrome with a fresh temporary profile")
    cp = chromelib.ChromeProc(path, url, headless=args.headless or (simulate and not os.environ.get("DISPLAY")
                                                                   and not os.environ.get("WAYLAND_DISPLAY")),
                              no_sandbox=no_sandbox)
    rep["chrome"]["flags"] = cp.flags
    cdp = session = None
    try:
        def cdp_setup():
            nonlocal cdp, session
            cdp = CDP(cp.devtools_ws())
            rep["chrome"]["cdp_version"] = cdp.send("Browser.getVersion")
            try:
                si = cdp.send("SystemInfo.getInfo", timeout=20)
                gpu = si.get("gpu", {})
                for k in ("videoDecoding", "videoEncoding", "imageDecoding"):
                    gpu.pop(k, None)
                for k in list(gpu.get("auxAttributes", {})):
                    if "Extensions" in k:   # multi-KB GL extension strings
                        gpu["auxAttributes"].pop(k)
                rep["chrome"]["system_info"] = si
            except Exception as e:
                rep["chrome"]["system_info_error"] = str(e)
            session, _ = cdp.attach_page(url)
            cdp.send("Page.bringToFront", session=session)
            if simulate:
                cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=session)
        step(rep, "cdp", cdp_setup)
        if not ch.page_seen.wait(45):
            rep["errors"]["page"] = "page never connected; chrome stderr: " + cp.stderr_tail()
            say("The test page did not load in Chrome. Giving up.")
            return
        time.sleep(1.0)
        sw = step(rep, "child_switches", cp.child_switches) or {}
        if "--ozone-platform" not in sw:   # fall back to the browser's own command line
            cl = (rep["chrome"].get("system_info") or {}).get("commandLine", "")
            oz = [a.split("=", 1)[1] + " [browser]" for a in cl.split() if a.startswith("--ozone-platform=")]
            if oz:
                sw["--ozone-platform"] = oz
        rep["chrome"]["child_switches"] = sw
        if is_mac and not simulate:
            from a1lib import quartz
            rep["chrome"]["activated"] = step(rep, "activate", lambda: quartz.activate_pid(cp.proc.pid))

        # From here on no CDP client stays attached in a real run (phase-a §2.6: M-session rule).
        if not simulate and cdp:
            cdp.close()
            cdp = None

        banner("Focus")
        if not wait_for_focus(ch, simulate):
            rep["errors"]["focus"] = "window never got focus"
            say("   The Chrome window never had focus; continuing, but results may be UNKNOWN.")

        banner("Page checks (about 20 s): isolation, timer, refresh rate, Element Timing")
        ch.call("show", {"title": "Measuring - hands off", "text": "Timer, refresh rate and Element Timing (about 20 s). The black square top-left will flicker."})
        rep["page_env"] = step(rep, "page_env", lambda: ch.call("env"))
        rep["timer"] = step(rep, "timer", lambda: ch.call("timer"))
        rep["raf"] = step(rep, "raf", lambda: ch.call("raf", {"ms": 2000}))
        rep["et_test"] = step(rep, "et_test", lambda: ch.call("et_test", {"per_variant": args.et_flips}, timeout=120))

        # ---- Input block
        if simulate:
            inj = injector.CdpInjector(cdp, session)
            rep["injector"] = {"info": inj.info}
        if inj is not None:
            banner("Key injection: %d keys, about %d s. HANDS OFF." % (args.keys, args.keys * 0.2 + 5))
            if not simulate:
                st = step(rep, "focus_recheck", lambda: ch.call("focus_state"))
                if not (st or {}).get("hasFocus"):
                    say("   Chrome lost focus. Click inside the Chrome window again.")
                    if not wait_for_focus(ch, simulate, timeout=120):
                        rep["errors"]["focus_injection"] = "no focus; injection skipped"
                        raise RuntimeError("no focus for injection")
            rep["sync_before"] = step(rep, "sync_before", lambda: ch.call("sync", {"n": 40}))
            armed = ch.call("arm")
            rep["arm"] = armed
            ch.call("countdown", {"seconds": 3}, timeout=15)
            sched = make_schedule(args.keys, args.seed, is_mac and not simulate)
            rep["schedule"] = {"seed": args.seed, "n": len(sched)}
            aborted_by_blur = []

            def on_blur(msg):
                aborted_by_blur.append(clock.now_ns())
                inj.stop()
            ch.on_blur = on_blur
            res = step(rep, "inject", lambda: inj.run(sched))
            ch.on_blur = None
            if res:
                rep["injector"].update({"records": res.get("records"), "aborted": res.get("aborted"),
                                        "clock_start": res.get("clock_start"), "clock_end": res.get("clock_end")})
                if aborted_by_blur:
                    say("   ! The Chrome window lost focus during typing; injection stopped early.")
            rep["page_input"] = step(rep, "collect", lambda: ch.call("collect", {"t_from": armed.get("t_armed", 0)}))
            rep["sync_after"] = step(rep, "sync_after", lambda: ch.call("sync", {"n": 40}))
        ch.call("show", {"title": "Done", "text": "You can use the keyboard again. Results are in the terminal."})
    finally:
        if cdp:
            try:
                cdp.close()
            except Exception:
                pass
        if inj is not None:
            inj.close()
        time.sleep(0.3)
        cp.stop()
        srv.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chrome", help="path to the Chrome binary (default: auto-detect)")
    ap.add_argument("--label", default="", help="free-text tag for this run, e.g. 60hz or 120hz")
    ap.add_argument("--keys", type=int, default=150, help="number of injected keystrokes (default 150)")
    ap.add_argument("--et-flips", type=int, default=30, help="Element Timing flips per variant (default 30)")
    ap.add_argument("--seed", type=int, default=None, help="schedule seed (default: random, recorded)")
    ap.add_argument("--sudo-injector", action="store_true", help="Linux: run only the uinput injector under sudo")
    ap.add_argument("--no-inject", action="store_true", help="skip key injection (page checks only)")
    ap.add_argument("--simulate", action="store_true",
                    help="DEV ONLY: inject via CDP instead of the OS (NOT a real test); headless if no display")
    ap.add_argument("--headless", action="store_true", help="DEV ONLY: run Chrome headless")
    ap.add_argument("--port", type=int, default=0, help="local server port (default: any free port)")
    ap.add_argument("--out", help="report path (default: tools/a1-check/reports/...)")
    ap.add_argument("--yes", action="store_true", help="do not wait for Enter at the start")
    ap.add_argument("--injector-child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.injector_child:
        sys.exit(injector.child_main())
    if args.seed is None:
        args.seed = random.randrange(1 << 30)

    os_name = sys.platform if sys.platform == "darwin" else ("linux" if clock.IS_LINUX else sys.platform)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "-" + "".join(c for c in args.label if c.isalnum() or c in "-_") if args.label else ""
    out = args.out or os.path.join(HERE, "reports", "%sa1-%s%s-%s.json" % (
        "SIMULATED-" if args.simulate else "", "mac" if os_name == "darwin" else os_name, label, stamp))
    rep = {"meta": {"tool": VERSION, "os": os_name, "label": args.label, "simulate": args.simulate,
                    "started": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "clock": clock.CLOCK_NAME, "args": {k: v for k, v in vars(args).items() if k != "injector_child"}},
           "errors": {}}

    say("Latency Ladder - Phase A1 on-device check (%s)" % VERSION)
    if args.simulate:
        say("*** SIMULATE MODE: keys go through CDP, not the OS. NOT A REAL TEST. ***")
    else:
        say("Before starting: plug in power, close other apps, turn off notifications.")
        say("The check takes about 2 minutes. Chrome will open a separate window with a fresh profile.")
        if not args.yes:
            try:
                input("Press Enter to start (Ctrl-C to cancel)... ")
            except EOFError:
                pass
    t0 = time.time()
    try:
        run(args, rep)
    except KeyboardInterrupt:
        rep["errors"]["interrupted"] = True
        say("\nInterrupted; writing a partial report.")
    except Exception as e:
        rep["errors"]["fatal"] = "%s: %s" % (type(e).__name__, e)
        rep["errors"]["fatal_trace"] = traceback.format_exc()
        say("Error: %s (writing a partial report)" % e)
    rep["meta"]["duration_s"] = round(time.time() - t0, 1)

    try:
        rows, derived = analysis.analyze(rep)
    except Exception as e:
        rows, derived = [], {}
        rep["errors"]["analysis"] = traceback.format_exc()
        say("Analysis failed: %s" % e)
    rep["rows"], rep["derived"] = rows, derived

    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    except OSError as e:
        import tempfile
        say("Could not write %s (%s); using the temp directory instead." % (out, e))
        out = os.path.join(tempfile.gettempdir(), os.path.basename(out))
        with open(out, "w") as f:
            json.dump(rep, f, indent=1, default=str)

    banner("Results%s" % ("  (SIMULATED - not a real test)" if args.simulate else ""))
    say(analysis.render_table(rows))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("PASS", "FAIL", "UNKNOWN", "INFO")}
    say("\n%d PASS, %d FAIL, %d UNKNOWN, %d INFO" % (counts["PASS"], counts["FAIL"], counts["UNKNOWN"], counts["INFO"]))
    errs = [k for k in rep["errors"] if not k.endswith("_trace")]
    if errs:
        say("Steps with problems: %s" % ", ".join(errs))
    say("\nReport written to:\n    %s\nPlease send that file back (it contains no personal files, only timings and system info)." % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
