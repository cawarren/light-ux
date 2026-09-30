"""`ladder soft run`: one Phase A software measurement session (phase-a §4).

Runs on the device under test. Standard library only.

  plan (seeded) -> servers (one origin per rung, COOP/COEP, collector injected)
  -> injector (uinput / Quartz child process; CDP only with --simulate)
  -> per block: fresh Chrome + profile, wait for the dataset, health checks, focus,
     then per segment: clock sync, arm, inject (settle-aware), read buffers back, clock sync
  -> raw files (read-only) + manifest.json

M sessions: no DevTools port, no CDP client, no tracing. T sessions (--trace): CDP attached,
Chrome tracing per block, numbers for attribution only. --simulate: CDP keys into headless
Chromium, every output labelled NOT REAL.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import stat
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone

from .. import manifest as manifest_mod
from .. import timing
from . import chrome as chromelib
from . import clock, envinfo, injector, schedule, server, trace
from .cdp import CDP

NOT_REAL = ("SIMULATED - NOT REAL: keys were injected through CDP Input.dispatchKeyEvent into headless "
            "Chromium in a container. This skips the kernel, compositor and Chrome's platform input path; "
            "no number from this session is a measurement of anything.")
SIZES = {"1k": 1000, "10k": 10000, "50k": 50000}
HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REPO = os.path.abspath(os.path.join(ORCH, ".."))


class Refuse(Exception):
    """A precondition failed; the runner refuses to measure (never silently degrades)."""


def say(msg=""):
    print(msg, flush=True)


# ------------------------------------------------------------------ small utilities

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid():
    """ULID: 48-bit ms timestamp + 80 random bits, Crockford base32 (sortable, 26 chars)."""
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_B32[(n >> (5 * k)) & 31] for k in reversed(range(26)))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root, skip=("dataset", "ladder")):
    """sha256 over (relative path, file sha256) of a build tree, skipping harness-served dirs."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d not in skip]
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, root).encode() + b"\0" + sha256_file(p).encode() + b"\n")
    return h.hexdigest()


def git_info():
    def run(*a):
        try:
            return subprocess.run(["git"] + list(a), cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return None
    return {"sha": run("rev-parse", "HEAD"),
            "dirty_paths": [l[3:] for l in (run("status", "--porcelain", "--", "orchestrator", "rungs/shared") or "").splitlines()]}


def write_json(path, obj, readonly=True, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), default=str)
        else:
            json.dump(obj, f, indent=1, default=str)
    if readonly:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def cpu_snapshot():
    """06 §6: CPU frequency / governor summary, best effort (Linux)."""
    if not clock.IS_LINUX:
        return None
    freqs, govs = [], set()
    import glob
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq"):
        try:
            freqs.append(int(open(p + "/scaling_cur_freq").read()) / 1000.0)
            govs.add(open(p + "/scaling_governor").read().strip())
        except (OSError, ValueError):
            pass
    try:
        load = open("/proc/loadavg").read().split()[:3]
    except OSError:
        load = None
    freqs.sort()
    return {"cur_mhz_min": freqs[0] if freqs else None, "cur_mhz_median": timing.quantile(freqs, .5),
            "cur_mhz_max": freqs[-1] if freqs else None, "governors": sorted(govs), "loadavg": load,
            "t_ns": clock.now_ns()}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------------------ rungs

def rung_site(rung, seed_id, sizes_needed):
    """Where a rung's production build lives, and how it is served."""
    ds = os.path.join(REPO, "dataset", "out", seed_id)
    files = {}
    for name, n in SIZES.items():
        p = os.path.join(ds, str(n), "items.json")
        if os.path.exists(p):
            files["/dataset/%s/items.json" % name] = p
    if os.path.exists(os.path.join(ds, "50000", "items.json")):
        files["/dataset/items.json"] = os.path.join(ds, "50000", "items.json")
    if os.path.exists(os.path.join(ds, "queries.json")):
        files["/dataset/queries.json"] = os.path.join(ds, "queries.json")
    for s in sizes_needed:
        if "/dataset/%s/items.json" % s not in files:
            raise Refuse("dataset %s/%s missing: run `node rungs/scripts/prepare.mjs` first" % (seed_id, s))
    probe = os.path.join(REPO, "rungs", "shared", "ladder-probe.js")
    marker = os.path.join(REPO, "rungs", "shared", "marker.json")
    rdir = os.path.join(REPO, "rungs", rung)
    if rung == "r1-typical":
        nxt = os.path.join(rdir, ".next")
        if not os.path.exists(os.path.join(nxt, "BUILD_ID")):
            raise Refuse("r1-typical is not built: (cd rungs/r1-typical && npm run build)")
        build_id = "next:" + open(os.path.join(nxt, "BUILD_ID")).read().strip() + ":" + tree_hash(os.path.join(nxt, "static"))[:16]
        site = server.RungSite(rung, proxy=None, probe_path=probe, marker_path=marker, dataset_files=files)
        site.next_dir = rdir
        return site, build_id
    dist = os.path.join(rdir, "dist")
    if not os.path.exists(os.path.join(dist, "index.html")):
        raise Refuse("%s is not built: (cd rungs/%s && npm run build)" % (rung, rung))
    site = server.RungSite(rung, root=dist, probe_path=probe, marker_path=marker, dataset_files=files)
    return site, "sha256:" + tree_hash(dist)


def start_next(site):
    """r1-typical: `next start` on a private port; the harness server proxies it."""
    port = free_port()
    nb = os.path.join(site.next_dir, "node_modules", "next", "dist", "bin", "next")
    cmd = ["node", nb, "start", "-p", str(port), "-H", "127.0.0.1"]
    proc = subprocess.Popen(cmd, cwd=site.next_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    end = time.time() + 60
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/ladder/marker.json" % port, timeout=2).read()
            site.proxy = ("127.0.0.1", port)
            return proc
        except Exception:
            time.sleep(0.3)
    proc.kill()
    raise Refuse("next start did not come up for r1-typical")


# ------------------------------------------------------------------ the session

class Session:
    def __init__(self, args):
        self.a = args
        self.sim = bool(args.simulate)
        self.kind = "T" if args.trace else "M"
        self.link = server.PageLink()
        self.servers, self.sites, self.procs = {}, {}, []
        self.inj = None
        self.manifest = None

    # -------------------------------------------------------------- preconditions
    def check_host(self):
        a = self.a
        path = chromelib.find_chrome(a.chrome)
        if self.sim and not a.chrome:
            path = chromelib.find_test_chromium() or path
        if not path:
            raise Refuse("Chrome not found; pass --chrome PATH")
        ver = chromelib.version_of(path)
        major = chromelib.major_of(ver)
        self.chrome = {"path": path, "version": ver, "major": major}
        if not self.sim:
            if os.geteuid() == 0:
                raise Refuse("do not run as root: Chrome must keep its sandbox (use --sudo-injector on Linux)")
            if major is None or major < chromelib.MIN_MAJOR:
                if not a.allow_old_chrome:
                    raise Refuse("Chrome %s < %d: Element Timing has no presentationTime (phase-a §4.2). "
                                 "Install current Chrome stable, or pass --allow-old-chrome to measure with the "
                                 "renderTime fallback (flagged per trial)." % (ver, chromelib.MIN_MAJOR))
            if clock.IS_LINUX:
                from . import uinput
                prob = uinput.access_problem()
                if prob and not a.sudo_injector:
                    raise Refuse(uinput.fix_instructions(prob))
                if os.environ.get("XDG_SESSION_TYPE") != "wayland":
                    say("   ! XDG_SESSION_TYPE is %r, not wayland; the plan assumes KWin on Wayland (recorded)."
                        % os.environ.get("XDG_SESSION_TYPE"))
            elif not clock.IS_MAC:
                raise Refuse("real runs support Linux (uinput) and macOS (Quartz) only")

    # -------------------------------------------------------------- setup
    def setup(self):
        a = self.a
        self.pools = schedule.load_query_pools(a.queries or os.path.join(REPO, "dataset", "out", a.seed_id, "queries.json"))
        self.seed = a.seed if a.seed is not None else random.randrange(1 << 62)
        self.plan = schedule.plan_session(self.seed, a.rungs, a.sizes, self.pools, reps=a.reps, first_key=a.first_key,
                                          seq=a.seq, warmup=a.warmup, max_keys=a.max_keys)
        sid = ulid()
        date = datetime.now().strftime("%Y-%m-%d")
        name = "%s%s_%s_%s_%shz_%s_%s" % ("SIMULATED-" if self.sim else "", date, a.machine, self.kind,
                                            int(a.hz) if float(a.hz).is_integer() else a.hz, a.display, sid)
        self.dir = os.path.join(os.path.abspath(a.out), name)
        os.makedirs(os.path.join(self.dir, "blocks"))
        os.makedirs(os.path.join(self.dir, "env"))
        self.sid = sid
        write_json(os.path.join(self.dir, "plan.json"), self.plan, compact=True)
        self.log = open(os.path.join(self.dir, "runner.log"), "a")
        self.logmsg("session %s dir %s seed %d" % (sid, self.dir, self.seed))
        if self.sim:
            say("*** SIMULATE MODE: CDP keys into headless Chromium. NOT A REAL MEASUREMENT. ***")
        # servers
        self.build_ids = {}
        for r in a.rungs:
            site, bid = rung_site(r, a.seed_id, a.sizes)
            if r == "r1-typical":
                self.procs.append(start_next(site))
            srv = server.make_server(site, self.link)
            self.servers[r], self.sites[r], self.build_ids[r] = srv, site, bid
            self.logmsg("rung %s on http://127.0.0.1:%d (%s)" % (r, srv.server_address[1], bid))
        # injector
        if not self.sim:
            say("== Preparing the key injector")
            self.inj = injector.InjectorProcess(use_sudo=a.sudo_injector and clock.IS_LINUX, cpu=a.injector_cpu)
            if not self.inj.start():
                if self.inj.error == "no_accessibility":
                    from . import quartz
                    raise Refuse(quartz.FIX_INSTRUCTIONS)
                raise Refuse("injector failed: %s" % self.inj.error)
            self.inj_info = self.inj.info
            say("   injector ready: %s" % json.dumps(self.inj.info))
            self.jitter = self.inj.selftest(a.jitter_n)
        else:
            self.inj_info = {"backend": "cdp-simulated", "NOT_REAL": True}
            self.jitter = injector.jitter_selftest(a.jitter_n)
        say("   injector scheduling self-test: p50 %.1f us, p99 %.1f us, max %.1f us" % (
            self.jitter["p50_us"], self.jitter["p99_us"], self.jitter["max_us"]))
        if self.jitter["p99_us"] > 200:
            say("   ! injector jitter p99 > 0.2 ms (phase-a §3.3 target); recorded. A C injector may be needed.")
        # environment
        self.env_start = envinfo.collect()
        self.env_start["cpu"] = cpu_snapshot()
        write_json(os.path.join(self.dir, "env", "start.json"), self.env_start)
        if not self.sim:
            self.chrome["info"] = self.capture_chrome_info()

    def logmsg(self, msg):
        self.log.write("%s %s\n" % (datetime.now().isoformat(timespec="milliseconds"), msg))
        self.log.flush()

    def capture_chrome_info(self):
        """A separate, short-lived Chrome with a DevTools port (never a measured block) to record
        the version, command line and GPU info (phase-a §4.2)."""
        cp = chromelib.ChromeProc(self.chrome["path"], "about:blank", session_kind="P", display_mode=self.a.display)
        try:
            c = CDP(cp.devtools_ws())
            info = {"version": c.send("Browser.getVersion")}
            try:
                si = c.send("SystemInfo.getInfo", timeout=20)
                gpu = si.get("gpu", {})
                for k in ("videoDecoding", "videoEncoding", "imageDecoding"):
                    gpu.pop(k, None)
                for k in list(gpu.get("auxAttributes", {})):
                    if "Extensions" in k:
                        gpu["auxAttributes"].pop(k)
                info["system_info"] = si
            except Exception as e:
                info["system_info_error"] = str(e)
            info["child_switches"] = cp.child_switches()
            c.close()
            return info
        except Exception as e:
            return {"error": str(e)}
        finally:
            cp.stop()

    # -------------------------------------------------------------- focus
    def wait_for_focus(self, cp=None, timeout=300):
        if self.sim:
            return True
        if clock.IS_MAC and cp is not None:
            try:
                from . import quartz
                quartz.activate_pid(cp.proc.pid)
            except Exception:
                pass
        streak, end, noted = 0, time.time() + timeout, 0
        while time.time() < end:
            st = self.link.call("focus_state", timeout=15)
            ok = st.get("hasFocus") and st.get("visibility") == "visible" and st.get("activeIsInput")
            streak = streak + 1 if ok else 0
            if streak >= 4:
                return True
            if not ok and time.time() - noted > 15:
                say("   >> Click once inside the new Chrome window (the palette), then hands off.")
                noted = time.time()
            time.sleep(0.25)
        return False

    # -------------------------------------------------------------- one block
    def run_block(self, b):
        a = self.a
        bid, rung, size = b["block_id"], b["rung"], b["size"]
        bdir = os.path.join(self.dir, "blocks", bid)
        os.makedirs(bdir)
        port = self.servers[rung].server_address[1]
        url = "http://127.0.0.1:%d/?items=/dataset/%s/items.json" % (port, size)
        headless = self.sim and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        rec = {"block_id": bid, "rung": rung, "size": size, "rep": b["rep"], "rung_build_id": self.build_ids[rung],
               "health": "error", "counts": {}, "segments": [], "flags": []}
        meta = {"url": url, "t_start": datetime.now(timezone.utc).isoformat(), "cpu_start": cpu_snapshot()}
        settle_ms = a.settle_timeout_ms or (5000 if rung.startswith("r1") else 2000)
        meta["settle_timeout_ms"] = settle_ms
        say("\n== Block %s: %s @ %s (%d keys in %d segments)" % (bid, rung, size, b["counts"]["keys"], len(b["segments"])))
        self.link.reset()
        cp = chromelib.ChromeProc(self.chrome["path"], url, session_kind=self.kind, display_mode=a.display,
                                  simulate=self.sim, headless=headless)
        meta["chrome_cmd"] = cp.cmd
        meta["sandbox_weakened"] = cp.sandbox_weakened
        cdp = page_session = None
        inj = self.inj
        tracing = None
        try:
            if self.sim or self.kind == "T":
                cdp = CDP(cp.devtools_ws())
                meta["cdp_version"] = cdp.send("Browser.getVersion")
            if self.sim:
                time.sleep(0.5)
                page_session, _ = cdp.attach_page()
                cdp.send("Page.bringToFront", session=page_session)
                cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=page_session)
                inj = injector.InProcessInjector(injector.CdpBackend(cdp, page_session))
                if "system_info" not in self.chrome:
                    try:
                        self.chrome["system_info_cmdline"] = cdp.send("SystemInfo.getInfo", timeout=20).get("commandLine")
                    except Exception as e:
                        self.chrome["system_info_error"] = str(e)
            if not self.link.hello_evt.wait(90):
                raise Refuse("page never connected (collector); chrome stderr: " + cp.stderr_tail())
            t0 = time.time()
            meta["ready"] = self.link.call("wait_ready", {"count": SIZES[size], "timeout_ms": 600000}, timeout=620)
            meta["load_s"] = round(time.time() - t0, 2)
            env = self.link.call("env")
            meta["page_env"] = env
            meta["child_switches"] = cp.child_switches()
            # health checks that refuse the block (phase-a §1.3, §4.4)
            if not env.get("crossOriginIsolated"):
                raise Refuse("page is not crossOriginIsolated")
            if not env.get("probe") or not env.get("marker"):
                raise Refuse("probe or marker missing (probe=%s marker=%s)" % (env.get("probe"), env.get("marker")))
            if (env.get("dataset") or {}).get("count") != SIZES[size]:
                raise Refuse("dataset count %s != %d" % (env.get("dataset"), SIZES[size]))
            if env.get("inputValue"):
                raise Refuse("palette input not empty at start")
            if not self.wait_for_focus(cp):
                raise Refuse("Chrome window never had keyboard focus")
            raf = self.link.call("raf", {"ms": 1000})
            fr = [{"frame": f[0], "raf": f[1]} for f in raf["frames"]]
            per = timing.frame_period_ms(fr)
            meta["measured_hz"] = round(1000.0 / per, 2) if per else None
            if meta["measured_hz"] and abs(meta["measured_hz"] - a.hz) / a.hz > 0.05:
                rec["flags"].append("hz_mismatch(%s vs nominal %s)" % (meta["measured_hz"], a.hz))
            if self.kind == "T":
                meta["trace"] = trace.start(cdp, a.trace_config)
            time.sleep(a.quiet_s)
            meta["segments"] = []
            for seg in b["segments"]:
                segrec = self.run_segment(b, seg, bdir, inj, cdp, settle_ms, rec)
                rec["segments"].append(segrec)
                if segrec.get("aborted"):
                    rec["health"] = "aborted"
                    rec["flags"].append("focus_lost(seg %d)" % seg["seg"])
                    say("   ! Focus was lost; block stopped. Remaining segments skipped.")
                    break
            if self.kind == "T":
                meta["trace"].update(trace.stop(cdp, os.path.join(bdir, "trace.pftrace")))
                os.chmod(os.path.join(bdir, "trace.pftrace"), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            if rec["health"] != "aborted":
                rec["health"] = "valid"
        except Refuse as e:
            rec["health"] = "refused"
            rec["flags"].append("refused: %s" % e)
            say("   ! block refused: %s" % e)
            if isinstance(e, PreflightRefuse):
                e.rec = rec
                raise
        except Exception as e:
            rec["health"] = "error"
            rec["flags"].append("error: %s: %s" % (type(e).__name__, e))
            meta["traceback"] = traceback.format_exc()
            say("   ! block error: %s" % e)
        finally:
            if cdp:
                try:
                    cdp.close()
                except Exception:
                    pass
            cp.stop()
            meta["t_end"] = datetime.now(timezone.utc).isoformat()
            meta["cpu_end"] = cpu_snapshot()
            meta["link_focus_log"] = self.link.focus_log
            write_json(os.path.join(bdir, "block.json"), meta)
        # health from the quick extraction (03 §2.5 rules, adapted)
        counts = {}
        for s in rec["segments"]:
            for k, v in s.get("status_counts", {}).items():
                counts[k] = counts.get(k, 0) + v
        rec["counts"] = counts
        n = sum(counts.values())
        if rec["health"] == "valid" and n:
            bad = counts.get("no_event", 0) + counts.get("clock_flag", 0) + counts.get("no_probe_entry", 0)
            if bad / n > 0.01 or any(s.get("frames_buffer_full") for s in rec["segments"]):
                rec["health"] = "unhealthy"
        rec["measured_hz"] = meta.get("measured_hz")
        rec["load_s"] = meta.get("load_s")
        say("   block %s: %s %s" % (bid, rec["health"], json.dumps(counts)))
        return rec

    def run_segment(self, b, seg, bdir, inj, cdp, settle_ms, rec):
        a = self.a
        link = self.link
        if not self.sim:
            st = link.call("focus_state")
            if not (st.get("hasFocus") and st.get("activeIsInput")):
                if not self.wait_for_focus(None, timeout=120):
                    return {"seg": seg["seg"], "aborted": True}
        raw = {"block_id": b["block_id"], "seg": seg["seg"], "attempt": 0, "rung": b["rung"], "size": b["size"],
               "simulated": self.sim, "keys": seg["keys"]}
        raw["sync_before"] = link.call("sync", {"n": a.sync_n})
        raw["arm"] = link.call("arm")
        if raw["arm"].get("inputValue"):
            rec["flags"].append("input_not_empty_at_arm(seg %d)" % seg["seg"])
        link.on_settled = lambda t, n, s: inj.settled(t, n, s)
        link.on_focus = lambda m: (not m.get("focused")) and m.get("armed") and inj.stop()
        try:
            res = inj.run(seg["keys"], lead_in_ms=a.lead_in_ms, settle_timeout_ms=settle_ms, final_settle=True)
        finally:
            link.on_settled = link.on_focus = None
        raw["injector"] = res
        raw["collect"] = link.call("collect", {}, timeout=120)
        raw["sync_after"] = link.call("sync", {"n": a.sync_n})
        raw["server_settles"] = list(link.settle_log)
        link.settle_log = []
        if cdp is not None and self.sim:
            cdp.drain(0.05)
        path = os.path.join(bdir, "seg-%03d.json" % seg["seg"])
        write_json(path, raw, compact=True)
        rows, _, info = timing.extract_segment(raw)
        sc = {}
        for r in rows:
            sc[r["status"]] = sc.get(r["status"], 0) + 1
        segrec = {"seg": seg["seg"], "file": os.path.relpath(path, self.dir), "n_keys": len(seg["keys"]),
                  "n_injected": info["n_injected"], "n_keydowns": info["n_keydowns"], "status_counts": sc,
                  "aborted": info["aborted"], "clock_flag": info["clock_flag"],
                  "sync": info["sync"], "present_sources": info["present_sources"],
                  "frames_buffer_full": info["frames_buffer_full"]}
        lat = sorted(r["lat_present_ms"] for r in rows if r["lat_present_ms"] is not None)
        os_d = sorted(r["lat_os_delivery_ms"] for r in rows if r["lat_os_delivery_ms"] is not None)
        say("   seg %2d: %2d keys, events %d, %s, lat p50 %s ms, os-delivery p50 %s ms, sync rtt %s ms%s" % (
            seg["seg"], info["n_injected"], info["n_keydowns"], ",".join("%s=%d" % kv for kv in sorted(sc.items())),
            _f(timing.quantile(lat, .5)), _f(timing.quantile(os_d, .5)),
            _f(info["sync_before"] and info["sync_before"]["min_rtt_ms"], 3),
            "  [NOT REAL]" if self.sim else ""))
        self.preflight(b, seg, rows, info, rec)
        return segrec

    def preflight(self, b, seg, rows, info, rec):
        """Adapt to or refuse on the A1-dependent assumptions (see orchestrator/README.md)."""
        a = self.a
        if seg["seg"] != 0:
            return
        srcs = info["present_sources"]
        if "presentationTime" not in srcs:
            msg = "Element Timing presentationTime missing (sources seen: %s)" % (srcs or "none")
            rec["flags"].append("fallback_timing:" + ",".join(srcs))
            if not self.sim and not a.allow_fallback_timing:
                raise PreflightRefuse(msg + ". Needs Chrome >= 145 (A1 row et_presentation). "
                                      "--allow-fallback-timing measures to paint/render time instead (flagged).")
            say("   ! %s; using fallback %s (flagged per trial)%s" % (msg, srcs[:1] or ["none"], " [simulate]" if self.sim else ""))
        if info["n_keydowns"] < info["n_injected"]:
            msg = "%d of %d injected keys reached the page" % (info["n_keydowns"], info["n_injected"])
            rec["flags"].append("keys_lost")
            if not self.sim:
                raise PreflightRefuse(msg + " (A1 row keys_delivered): focus, uinput permission or compositor.")
        os_d = [r["lat_os_delivery_ms"] for r in rows if r["lat_os_delivery_ms"] is not None]
        p50 = timing.quantile(os_d, .5)
        if p50 is not None and not (-1.5 <= p50 <= 20.0):
            msg = "OS delivery p50 %.3f ms outside [-1.5, 20] ms (A1 rows os_delivery / ts_semantics)" % p50
            rec["flags"].append("odd_os_delivery")
            if not self.sim and not a.allow_odd_timestamps:
                raise PreflightRefuse(msg + ". --allow-odd-timestamps to continue anyway.")

    # -------------------------------------------------------------- the whole run
    def run(self):
        a = self.a
        self.check_host()
        self.setup()
        started = datetime.now(timezone.utc).isoformat()
        blocks = []
        refused = None
        try:
            if not self.sim and not a.yes:
                say("\nBefore starting: power adapter in, other apps closed, notifications off, mouse pointer")
                say("parked at the screen edge (hover changes cmdk's selection). Chrome opens a fresh window per")
                say("block; if asked, click once inside it, then keep hands off the keyboard and mouse.")
                try:
                    input("Press Enter to start (Ctrl-C to cancel)... ")
                except EOFError:
                    pass
            for k, b in enumerate(self.plan["blocks"]):
                try:
                    blocks.append(self.run_block(b))
                except PreflightRefuse as e:
                    refused = str(e)
                    blocks.append(getattr(e, "rec", None) or {
                        "block_id": b["block_id"], "rung": b["rung"], "size": b["size"], "rep": b["rep"],
                        "rung_build_id": self.build_ids[b["rung"]], "health": "refused", "counts": {},
                        "segments": [], "flags": ["preflight: " + refused]})
                    say("\n!! Session refused: %s" % refused)
                    break
                self.write_manifest(started, blocks, None)
                if k + 1 < len(self.plan["blocks"]):
                    time.sleep(a.idle_s)
        except KeyboardInterrupt:
            say("\nInterrupted; writing the manifest for the blocks so far.")
        finally:
            self.shutdown()
        self.env_end = envinfo.collect()
        self.env_end["cpu"] = cpu_snapshot()
        write_json(os.path.join(self.dir, "env", "end.json"), self.env_end)
        m = self.write_manifest(started, blocks, datetime.now(timezone.utc).isoformat(), refused=refused)
        errs = manifest_mod.validate(m)
        if errs:
            say("manifest problems: %s" % errs)
        say("\nSession directory:\n    %s" % self.dir)
        if self.sim:
            say("*** SIMULATED - NOT REAL. ***")
        return self.dir

    def shutdown(self):
        if self.inj is not None:
            self.inj.close()
        for srv in self.servers.values():
            srv.shutdown()
        for p in self.procs:
            try:
                os.killpg(p.pid, 15)
            except Exception:
                p.terminate()

    def write_manifest(self, started, blocks, ended, refused=None):
        a = self.a
        env_keys = []
        if ended and getattr(self, "env_end", None):
            env_keys = sorted(k for k in set(self.env_start) | set(self.env_end)
                              if k != "cpu" and self.env_start.get(k) != self.env_end.get(k))
        files = {}
        for dirpath, _, fns in os.walk(self.dir):
            for fn in sorted(fns):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, self.dir)
                if rel in ("manifest.json", "manifest.partial.json", "runner.log"):
                    continue
                files[rel] = "sha256:" + sha256_file(p)
        hz_meas = [b.get("measured_hz") for b in blocks if b.get("measured_hz")]
        ds = os.path.join(REPO, "dataset", "out", a.seed_id)
        m = {
            "schema_version": manifest_mod.SCHEMA_VERSION, "session_id": self.sid, "session_kind": self.kind,
            "rig": "none",
            "input_source": "cdp-simulated" if self.sim else ("uinput" if clock.IS_LINUX else "quartz"),
            "simulated": self.sim, "not_real_warning": NOT_REAL if self.sim else None,
            "machine_id": a.machine,
            "display": {"nominal_hz": a.hz, "mode": a.display, "measured_hz": timing.quantile(hz_meas, .5)},
            "started_at": started, "ended_at": ended,
            "seeds": {"session": self.seed, "rung_order": self.plan["order_seed"],
                      "blocks": {b["block_id"]: b["seed"] for b in self.plan["blocks"]}},
            "block_order": self.plan["block_order"],
            "provenance": {
                "orchestrator_git": git_info(), "clock": clock.CLOCK_NAME,
                "probe_sha256": sha256_file(os.path.join(REPO, "rungs", "shared", "ladder-probe.js")),
                "marker_json_sha256": sha256_file(os.path.join(REPO, "rungs", "shared", "marker.json")),
                "collector_sha256": sha256_file(server.COLLECTOR_PATH),
                "dataset_seed_id": a.seed_id,
                "dataset_sha256": {s: sha256_file(os.path.join(ds, str(SIZES[s]), "items.json")) for s in a.sizes},
                "queries_sha256": self.pools["sha256"], "rung_build_ids": self.build_ids,
                "python": sys.version.split()[0],
            },
            "chrome": self.chrome, "injector": {"info": self.inj_info, "jitter_selftest": self.jitter},
            "env": {"start": "env/start.json", "end": "env/end.json" if ended else None, "changed_keys": env_keys},
            "params": {k: v for k, v in vars(a).items() if k not in ("func",)},
            "blocks": blocks, "files": files, "notes": a.notes,
            "refused": refused,
        }
        path = os.path.join(self.dir, "manifest.json" if ended else "manifest.partial.json")
        if os.path.exists(path):
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        write_json(path, m, readonly=bool(ended))
        if ended and os.path.exists(os.path.join(self.dir, "manifest.partial.json")):
            os.remove(os.path.join(self.dir, "manifest.partial.json"))
        self.manifest = m
        return m


class PreflightRefuse(Refuse):
    """Refusal that stops the whole session (the assumption fails for every block)."""


def _f(v, nd=1):
    return "-" if v is None else ("%." + str(nd) + "f") % v
