#!/usr/bin/env python3
"""Unit tests for the A1 check that run without a display, uinput or macOS.

    python3 tools/a1-check/test_a1.py
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import threading
import types
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a1lib import analysis, cdp, chrome, clock, injector, keymap, quartz, uinput  # noqa: E402
from a1lib.server import PageChannel, make_server  # noqa: E402


class TestUinputAbi(unittest.TestCase):
    """Values checked against <linux/uinput.h> / <linux/input.h> on x86_64 and arm64."""

    def test_struct_sizes(self):
        self.assertEqual(uinput.UINPUT_SETUP_SIZE, 92)
        if struct.calcsize("l") == 8:
            self.assertEqual(uinput.INPUT_EVENT_SIZE, 24)

    def test_ioctl_numbers(self):
        self.assertEqual(uinput.UI_SET_EVBIT, 0x40045564)
        self.assertEqual(uinput.UI_SET_KEYBIT, 0x40045565)
        self.assertEqual(uinput.UI_DEV_SETUP, 0x405C5503)
        self.assertEqual(uinput.UI_DEV_CREATE, 0x5501)
        self.assertEqual(uinput.UI_DEV_DESTROY, 0x5502)
        self.assertEqual(uinput.UI_GET_SYSNAME(64), 0x8040552C)

    def test_key_bytes(self):
        b = uinput.key_bytes(30, True)
        self.assertEqual(len(b), 2 * uinput.INPUT_EVENT_SIZE)
        e1 = struct.unpack(uinput.INPUT_EVENT_FMT, b[:uinput.INPUT_EVENT_SIZE])
        e2 = struct.unpack(uinput.INPUT_EVENT_FMT, b[uinput.INPUT_EVENT_SIZE:])
        self.assertEqual(e1[2:], (uinput.EV_KEY, 30, 1))
        self.assertEqual(e2[2:], (uinput.EV_SYN, uinput.SYN_REPORT, 0))
        self.assertEqual(struct.unpack(uinput.INPUT_EVENT_FMT, uinput.key_bytes(30, False)[:uinput.INPUT_EVENT_SIZE])[4], 0)

    def test_setup_packing(self):
        b = uinput.pack_setup()
        self.assertEqual(len(b), 92)
        bus, vendor, product, version = struct.unpack("HHHH", b[:8])
        self.assertEqual(bus, uinput.BUS_VIRTUAL)
        self.assertTrue(b[8:88].startswith(uinput.DEVICE_NAME + b"\0"))

    def test_device_call_sequence_with_fake_kernel(self):
        calls, writes = [], []
        import fcntl
        real = (os.open, os.write, os.close, fcntl.ioctl)
        os.open = lambda p, fl: calls.append(("open", p)) or 99
        os.write = lambda fd, data: writes.append(data) or len(data)
        os.close = lambda fd: calls.append(("close", fd))
        fcntl.ioctl = lambda fd, req, arg=0, mut=False: calls.append(("ioctl", req, arg if isinstance(arg, int) else len(arg))) or 0
        try:
            with uinput.UInputKeyboard(keymap.EVDEV.values()) as kb:
                kb.key(30, True)
                kb.key(30, False)
        finally:
            os.open, os.write, os.close, fcntl.ioctl = real
        reqs = [c[1] for c in calls if c[0] == "ioctl"]
        self.assertEqual(calls[0], ("open", "/dev/uinput"))
        self.assertIn(uinput.UI_SET_EVBIT, reqs)
        keybits = {c[2] for c in calls if c[0] == "ioctl" and c[1] == uinput.UI_SET_KEYBIT}
        self.assertTrue(set(range(1, 32)) <= keybits, "keys 1..31 needed for ID_INPUT_KEYBOARD")
        self.assertTrue(set(keymap.EVDEV.values()) <= keybits)
        self.assertLess(reqs.index(uinput.UI_DEV_SETUP), reqs.index(uinput.UI_DEV_CREATE))
        self.assertEqual(reqs[-1], uinput.UI_DEV_DESTROY)
        self.assertEqual(len(writes), 2)
        self.assertEqual(calls[-1][0], "close")

    def test_fix_instructions(self):
        self.assertIn("setfacl", uinput.fix_instructions("not_writable"))
        self.assertIn("modprobe uinput", uinput.fix_instructions("missing"))


class TestQuartzSignatures(unittest.TestCase):
    def test_bind_sets_types(self):
        class Fn:
            pass
        libs = {k: types.SimpleNamespace(**{n: Fn() for n, (lib, _, _) in quartz.SIGNATURES.items() if lib == k})
                for k in ("cg", "cf", "as", "sys")}
        fns = quartz.bind(libs)
        self.assertEqual(set(fns), set(quartz.SIGNATURES))
        f = fns["CGEventCreateKeyboardEvent"]
        self.assertEqual(f.argtypes, [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool])
        self.assertEqual(f.restype, ctypes.c_void_p)
        self.assertEqual(fns["CGEventPost"].argtypes, [ctypes.c_uint32, ctypes.c_void_p])
        self.assertEqual(fns["CGEventGetTimestamp"].restype, ctypes.c_uint64)
        self.assertEqual(fns["AXIsProcessTrusted"].restype, ctypes.c_uint8)

    def test_bind_tolerates_missing_symbols(self):
        libs = {k: types.SimpleNamespace() for k in ("cg", "cf", "as", "sys")}
        self.assertEqual(quartz.bind(libs), {})

    def test_c_type_widths(self):
        self.assertEqual(ctypes.sizeof(quartz.mach_timebase_info_data_t), 8)
        self.assertEqual(ctypes.sizeof(ctypes.c_uint16), 2)   # CGKeyCode
        self.assertEqual(ctypes.sizeof(ctypes.c_uint32), 4)   # CGEventTapLocation
        self.assertEqual(ctypes.sizeof(ctypes.c_uint64), 8)   # CGEventFlags, CGEventTimestamp

    def test_instructions_text(self):
        self.assertIn("Accessibility", quartz.FIX_INSTRUCTIONS)

    @unittest.skipUnless(sys.platform == "darwin", "macOS only")
    def test_real_frameworks_load(self):  # pragma: no cover
        f = quartz.fns()
        self.assertIn("CGEventPost", f)
        numer, denom = quartz.mach_timebase()
        self.assertGreater(numer, 0)
        self.assertIsInstance(quartz.post_access()["ax_trusted"], bool)


class TestKeymap(unittest.TestCase):
    def test_maps(self):
        self.assertEqual(len(keymap.LETTERS), 26)
        self.assertEqual(set(keymap.EVDEV), set(keymap.MAC_VK))
        self.assertEqual(len(set(keymap.EVDEV.values())), 26)
        self.assertEqual(len(set(keymap.MAC_VK.values())), 26)
        self.assertEqual(keymap.dom_code("q"), "KeyQ")


class FakeLines:
    def __init__(self, stop_after=None):
        self.calls, self.stop_after = 0, stop_after

    def poll_line(self, timeout):
        self.calls += 1
        return "stop" if self.stop_after is not None and self.calls > self.stop_after else None


class FakeLinuxDev:
    def __init__(self):
        self.events = []

    def key(self, code, down):
        self.events.append((clock.now_ns(), code, down))


class TestInjectorSchedule(unittest.TestCase):
    def sched(self, n=8):
        import a1_check
        return a1_check.make_schedule(n, 1, mac=False)

    def test_runs_schedule(self):
        dev = FakeLinuxDev()
        sch = self.sched()
        for s in sch:
            s["gap_ms"], s["hold_ms"] = 5, 3
        recs, aborted = injector._run_schedule(dev, "linux", sch, 5, FakeLines())
        self.assertFalse(aborted)
        self.assertEqual(len(recs), len(sch))
        self.assertEqual(len(dev.events), 2 * len(sch))
        for r in recs:
            self.assertGreaterEqual(r["t_wake_ns"], r["planned_down_ns"])
            self.assertGreaterEqual(r["t_up_ns"] - r["t_down_ns"], 3e6)
        for a, b in zip(recs, recs[1:]):
            self.assertGreaterEqual(b["t_down_ns"] - a["t_up_ns"], 5e6)

    def test_stop(self):
        recs, aborted = injector._run_schedule(FakeLinuxDev(), "linux", self.sched(), 1, FakeLines(stop_after=3))
        self.assertTrue(aborted)
        self.assertEqual(len(recs), 3)

    def test_schedule_ranges(self):
        import a1_check
        s = a1_check.make_schedule(500, 7, mac=True)
        self.assertTrue(all(50 <= x["gap_ms"] <= 250 and 30 <= x["hold_ms"] <= 60 for x in s))
        self.assertEqual({x["post_delay_ms"] for x in s}, {0, 4})
        self.assertEqual(s, a1_check.make_schedule(500, 7, mac=True))


FAKE_CHILD = r"""
import sys
sys.path.insert(0, %r)
from a1lib import uinput, injector, clock
class Fake:
    def __init__(self, codes): self.sysname = "fake0"
    def open(self): return self
    def key(self, code, down): pass
    def close(self): pass
uinput.UInputKeyboard = Fake
clock.IS_LINUX, clock.IS_MAC = True, False
sys.exit(injector.child_main())
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux child path")
class TestInjectorProcessProtocol(unittest.TestCase):
    def test_parent_child_roundtrip(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(FAKE_CHILD % os.path.dirname(os.path.abspath(__file__)))
        try:
            p = injector.InjectorProcess(f.name)
            self.assertTrue(p.start(), p.error)
            self.assertEqual(p.info["device"]["sysname"], "fake0")
            import a1_check
            sch = a1_check.make_schedule(5, 2, mac=False)
            res = p.run(sch, lead_in_ms=10)
            self.assertTrue(res["done"])
            self.assertEqual(len(res["records"]), 5)
            self.assertIn("CLOCK_BOOTTIME", res["clock_start"])
            p.close()
            self.assertEqual(p.proc.returncode, 0)
        finally:
            p.proc.stdin.close(); p.proc.stdout.close()
            os.unlink(f.name)


class TestServerAndWebsocket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ch = PageChannel()
        cls.srv = make_server(cls.ch)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_isolation_headers(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.headers["Cross-Origin-Opener-Policy"], "same-origin")
            self.assertEqual(r.headers["Cross-Origin-Embedder-Policy"], "require-corp")
            self.assertIn(b"elementtiming", r.read())

    def test_fetch_sync(self):
        t0 = clock.now_ns()
        req = urllib.request.Request(self.base + "/sync", data=b"", method="POST")
        with urllib.request.urlopen(req) as r:
            t = int(json.loads(r.read())["t_ns"])
        self.assertTrue(t0 <= t <= clock.now_ns())

    def test_websocket_sync_with_our_client(self):
        """Our websocket client (cdp.py) against our websocket server (server.py)."""
        c = cdp.CDP("ws://127.0.0.1:%d/ws" % self.srv.server_address[1])
        for _ in range(3):
            t0 = clock.now_ns()
            c.sock.sendall(cdp.ws_frame(b"s"))
            msg = c._recv_message()
            t3 = clock.now_ns()
            self.assertTrue(t0 <= int(msg["t1"]) <= int(msg["t2"]) <= t3)
        c.close()

    def test_page_channel_roundtrip(self):
        def page():
            with urllib.request.urlopen(self.base + "/cmd") as r:
                cmd = json.loads(r.read())
            body = json.dumps({"id": cmd["id"], "result": {"echo": cmd["op"]}}).encode()
            with urllib.request.urlopen(urllib.request.Request(self.base + "/result", data=body, method="POST")) as r:
                r.read()
        threading.Thread(target=page, daemon=True).start()
        self.assertEqual(self.ch.call("hello", timeout=10), {"echo": "hello"})


class TestWsFrame(unittest.TestCase):
    def test_lengths_and_mask(self):
        for n in (0, 5, 125, 126, 300, 70000):
            payload = bytes(range(256)) * (n // 256) + bytes(range(n % 256))
            f = cdp.ws_frame(payload, mask_key=b"\x01\x02\x03\x04")
            self.assertEqual(f[0], 0x81)
            self.assertTrue(f[1] & 0x80)
            hdr = 2 + (2 if 126 <= n < 65536 else 8 if n >= 65536 else 0)
            mask = f[hdr:hdr + 4]
            body = bytes(b ^ mask[i & 3] for i, b in enumerate(f[hdr + 4:]))
            self.assertEqual(body, payload)


class TestChromeFlags(unittest.TestCase):
    def test_lint(self):
        with self.assertRaises(ValueError):
            chrome.flag_lint(["--no-sandbox"])
        with self.assertRaises(ValueError):
            chrome.flag_lint(["--disable-gpu-vsync"])
        chrome.flag_lint(["--no-sandbox"], allow_no_sandbox=True)
        chrome.flag_lint(["--user-data-dir=/tmp/x", "--remote-debugging-port=0"])


def synthetic_report(os_name="linux", delivery=0.4, present=True, mac_os_ts=False, whole_ms=False):
    """A plausible report: 60 Hz frames, flips paint ~2 ms after rAF, present ~1 frame later."""
    period = 1000 / 60
    raf = [[i, 1000 + i * period, 1000 + i * period + 0.3] for i in range(2000)]
    flips, entries = [], []
    t = 1100.0
    for v in "ABCD":
        for i in range(30):
            t += 110
            fr = int((t - 1000) // period) + 1
            f = {"id": "et-%s-%d" % (v, i), "variant": v, "t_flip": t, "raf_cb": raf[fr][2], "raf_ts": raf[fr][1]}
            flips.append(f)
            if v in "AC":
                e = {"identifier": f["id"], "paintTime": f["raf_cb"] + 2, "renderTime": f["raf_cb"] + 2 + period}
                if present:
                    e["presentationTime"] = f["raf_cb"] + 2 + period
                entries.append(e)
    offset_ms = 5_000_000.0
    keys, kents, records, evs = [], [], [], []
    import random
    rng = random.Random(3)
    tk = 6000.0
    for i in range(150):
        tk += 150 if whole_ms else 150 + rng.random()
        post_delay = 4 if (os_name == "darwin" and i % 2) else 0
        t_inject_page = tk
        ts = t_inject_page + delivery - (post_delay if mac_os_ts else 0) + (i % 7) * 0.013
        fr = int((ts - 1000) // period) + 1
        flip = {"id": "k-%d" % i, "raf_cb": raf[fr][2], "raf_ts": raf[fr][1]}
        k = {"i": i, "ts": ts, "t_handler": ts + 0.1, "code": "KeyA", "flip": flip}
        if i % 5 == 4:
            k["busy_ms"] = 20
            evs.append({"name": "keydown", "startTime": ts, "duration": 24})
        keys.append(k)
        e = {"identifier": flip["id"], "paintTime": flip["raf_cb"] + 2, "renderTime": flip["raf_cb"] + 2 + period}
        if present:
            e["presentationTime"] = e["renderTime"]
        kents.append(e)
        rec = {"i": i, "letter": "a", "planned_down_ns": int((t_inject_page + offset_ms) * 1e6) - 1000,
               "t_wake_ns": int((t_inject_page + offset_ms) * 1e6) - 900, "t_down_ns": int((t_inject_page + offset_ms) * 1e6),
               "post_delay_ms": post_delay}
        if os_name == "darwin":
            rec["mach_at_create"] = 10**12
            rec["cg_timestamp"] = 10**12
        records.append(rec)

    def sync(base_page):
        return {"samples": [[base_page, str(int((base_page + 0.1 + offset_ms) * 1e6)),
                             str(int((base_page + 0.1 + offset_ms) * 1e6)), base_page + 0.2]] * 5,
                "transport": "websocket"}
    return {
        "meta": {"os": os_name, "simulate": False},
        "page_env": {"crossOriginIsolated": True, "dpr": 2,
                     "proto": {"PerformanceElementTiming": ["renderTime", "identifier"] + (["paintTime", "presentationTime"] if present else []),
                               "PerformanceEventTiming": ["processingStart", "interactionId"]}},
        "timer": {"min": 0.005, "p50": 0.005, "samples": [1000.005 + 0.005 * i for i in range(50)]},
        "raf": {"raf": raf[:120]},
        "et_test": {"flips": flips, "entries": entries},
        "chrome": {"child_switches": {"--ozone-platform": ["wayland [gpu-process]"]}},
        "host": {"linux": {"XDG_SESSION_TYPE": "wayland"}},
        "sync_before": sync(5000.0), "sync_after": sync(40000.0),
        "injector": {"records": records, "info": {"sched": "SCHED_FIFO/50", "mach_timebase": [125, 3]}},
        "page_input": {"keys": keys, "entries": kents, "events": evs},
    }


class TestAnalysis(unittest.TestCase):
    def status(self, rows):
        return {r["id"]: r["status"] for r in rows}

    def test_helpers(self):
        self.assertEqual(analysis.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(analysis.quantile(list(range(101)), 0.95), 95)
        self.assertEqual(analysis.grid_ms([4, 8, 12, 16, 20, 400]), 4)
        self.assertEqual(analysis.grid_ms([1.005, 2.01, 3.015, 4.02, 5.1]), 0.005)
        self.assertIsNone(analysis.grid_ms([1.0001, 2.0003, 3.0007, 4.0011, 5.0013]))
        self.assertLess(analysis.circular_R([i / 100 for i in range(100)]), 0.01)
        self.assertGreater(analysis.circular_R([0.01] * 50), 0.99)

    def test_ntp_formula(self):
        # page clock = host - 1000 ms; symmetric 0.1 ms legs; 0.05 ms server processing
        s = analysis.clock_sync([[10.0, str(int(1010.1e6)), str(int(1010.15e6)), 10.25]])
        self.assertAlmostEqual(s["offset_ms"], 1000.0, places=6)
        self.assertAlmostEqual(s["min_rtt_ms"], 0.2, places=6)

    def test_good_linux_report_passes(self):
        rows, derived = analysis.analyze(synthetic_report())
        st = self.status(rows)
        for rid in ("coi", "timer_res", "wayland", "et_every_flip", "et_presentation", "et_present_minus_paint",
                    "et_same_frame", "et_resolution", "et_same_colour", "et_inline_span", "evt_start_eq_ts",
                    "evt_duration_8ms", "evt_no_presentation", "clock_sync", "keys_delivered", "os_delivery",
                    "ts_semantics", "floor", "injector_jitter"):
            self.assertEqual(st.get(rid), "PASS", (rid, [r for r in rows if r["id"] == rid]))
        self.assertAlmostEqual(derived["os_delivery_ms"]["min"], 0.4, places=2)

    def test_whole_ms_timestamps_flagged_as_compositor_time(self):
        st = self.status(analysis.analyze(synthetic_report(whole_ms=True))[0])
        self.assertEqual(st["ts_semantics"], "FAIL")

    def test_old_chrome_without_presentation(self):
        st = self.status(analysis.analyze(synthetic_report(present=False))[0])
        self.assertEqual(st["et_presentation"], "FAIL")
        self.assertEqual(st["et_present_minus_paint"], "UNKNOWN")
        self.assertEqual(st["floor"], "UNKNOWN")

    def test_preempted_stamp_is_dropped(self):
        rep = synthetic_report()
        r0 = rep["injector"]["records"][0]
        r0["t_down_ns"] += 17_000_000          # stamped 17 ms late
        rows, derived = analysis.analyze(rep)
        self.assertEqual(derived["n_dropped_stamp_window"], 1)
        self.assertEqual(self.status(rows)["os_delivery"], "PASS")

    def test_negative_delivery_fails(self):
        st = self.status(analysis.analyze(synthetic_report(delivery=-2.0))[0])
        self.assertEqual(st["os_delivery"], "FAIL")

    def test_mac_os_timestamp_detected(self):
        rows, _ = analysis.analyze(synthetic_report("darwin", delivery=0.6, mac_os_ts=True))
        r = [r for r in rows if r["id"] == "ts_semantics"][0]
        self.assertTrue(r["numbers"]["uses_os_timestamp"], r)
        rows, _ = analysis.analyze(synthetic_report("darwin", delivery=0.6, mac_os_ts=False))
        r = [r for r in rows if r["id"] == "ts_semantics"][0]
        self.assertFalse(r["numbers"]["uses_os_timestamp"], r)

    def test_empty_report_is_all_unknown_not_crash(self):
        rows, _ = analysis.analyze({"meta": {"os": "linux"}})
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if r["status"] == "PASS"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
