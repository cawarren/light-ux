"""Key injector.

The real injectors (uinput on Linux, Quartz on macOS) run in a separate child
process so the HTTP server and CDP threads in the main process cannot hold the
GIL while a key is due, and so that on Linux just this process can be started
with sudo if /dev/uinput is not accessible.

Protocol (JSON lines over the child's stdin/stdout):
  child  -> {"ready": true, "info": {...}}   or {"ready": false, "error": "..."}
  parent -> {"cmd": "run", "schedule": [...], "lead_in_ms": 500}
  parent -> "stop"                            (any time during a run; aborts it)
  child  -> {"done": true, "records": [...], "aborted": bool, "info": {...}}
  parent -> {"cmd": "quit"}

A schedule item is {"i", "letter", "gap_ms", "hold_ms", "post_delay_ms"}:
  gap_ms        time from the previous key-up to this key-down (first: ignored)
  hold_ms       key-down to key-up
  post_delay_ms macOS only: create the CGEvent, wait this long, then post it.
                If Chrome uses the OS event timestamp, event.timeStamp moves
                earlier by this amount relative to the post time (check 5).
"""
from __future__ import annotations

import gc
import json
import os
import select
import subprocess
import sys
import threading
import time

from . import clock, keymap


# ---------------------------------------------------------------- child side

class _RawLines:
    """Line reader on a raw fd, so select() tells the truth about buffered data."""

    def __init__(self, fd):
        self.fd, self.buf = fd, b""

    def poll_line(self, timeout):
        if b"\n" not in self.buf:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if r:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    return "__eof__"
                self.buf += chunk
        if b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            return line.decode().strip()
        return None


def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _try_realtime():
    info = {}
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
        info["sched"] = "SCHED_FIFO/50"
    except (AttributeError, PermissionError, OSError) as e:
        info["sched"] = "default (%s)" % type(e).__name__
    return info


def _run_schedule(dev, platform, schedule, lead_in_ms, lines):
    records, aborted = [], False
    gc.disable()
    try:
        t_next = clock.now_ns() + int(lead_in_ms * 1e6)
        for idx, item in enumerate(schedule):
            cmd = lines.poll_line(0)
            if cmd in ("stop", "__eof__"):
                aborted = True
                break
            if idx > 0:
                t_next += int(item["gap_ms"] * 1e6)
            letter = item["letter"]
            rec = {"i": item["i"], "letter": letter, "planned_down_ns": t_next}
            clock.sleep_until_ns(t_next)
            rec["t_wake_ns"] = clock.now_ns()
            if platform == "linux":
                code = keymap.EVDEV[letter]
                dev.key(code, True)
                rec["t_down_ns"] = clock.now_ns()
            else:
                vk = keymap.MAC_VK[letter]
                ev = dev.make(vk, True)
                rec["t_create_ns"] = clock.now_ns()
                rec["cg_timestamp"] = dev.timestamp(ev)
                from . import quartz
                rec["mach_at_create"] = quartz.mach_absolute_time()
                d = item.get("post_delay_ms", 0)
                if d:
                    clock.sleep_until_ns(rec["t_create_ns"] + int(d * 1e6), spin_ns=10**7)
                rec["t_before_post_ns"] = clock.now_ns()
                dev.post(ev)
                rec["t_down_ns"] = clock.now_ns()
                dev.release(ev)
                rec["post_delay_ms"] = d
            up_deadline = rec["t_down_ns"] + int(item["hold_ms"] * 1e6)
            clock.sleep_until_ns(up_deadline)
            if platform == "linux":
                dev.key(keymap.EVDEV[letter], False)
            else:
                ev = dev.make(keymap.MAC_VK[letter], False)
                dev.post(ev)
                dev.release(ev)
            rec["t_up_ns"] = clock.now_ns()
            rec["planned_up_ns"] = up_deadline
            records.append(rec)
            t_next = rec["t_up_ns"]
    finally:
        gc.enable()
    return records, aborted


def child_main():
    """Entry point for `a1_check.py --injector-child`."""
    platform = "linux" if clock.IS_LINUX else ("mac" if clock.IS_MAC else "other")
    info = {"platform": platform, "clock": clock.CLOCK_NAME, "pid": os.getpid(),
            "euid": os.geteuid(), "python": sys.version.split()[0]}
    dev = None
    try:
        if platform == "linux":
            from . import uinput
            dev = uinput.UInputKeyboard(keymap.EVDEV.values()).open()
            info["device"] = {"name": uinput.DEVICE_NAME.decode(), "sysname": dev.sysname}
        elif platform == "mac":
            from . import quartz
            acc = quartz.post_access()
            info["access"] = acc
            if not acc["post_event_access"]:
                quartz.request_access()
                _emit({"ready": False, "error": "no_accessibility", "info": info})
                return 3
            dev = quartz.QuartzKeyboard()
            info["mach_timebase"] = list(quartz.mach_timebase())
        else:
            _emit({"ready": False, "error": "unsupported platform", "info": info})
            return 2
    except Exception as e:
        _emit({"ready": False, "error": "%s: %s" % (type(e).__name__, e), "info": info})
        return 2
    info.update(_try_realtime())
    _emit({"ready": True, "info": info})
    lines = _RawLines(sys.stdin.fileno())
    try:
        while True:
            line = lines.poll_line(1.0)
            if line is None or line == "stop":
                continue
            if line == "__eof__":
                break
            msg = json.loads(line)
            if msg.get("cmd") == "quit":
                break
            if msg.get("cmd") == "run":
                snap0 = clock.clock_snapshot()
                records, aborted = _run_schedule(dev, platform, msg["schedule"],
                                                 msg.get("lead_in_ms", 500), lines)
                _emit({"done": True, "records": records, "aborted": aborted,
                       "clock_start": snap0, "clock_end": clock.clock_snapshot()})
            elif msg.get("cmd") == "clock":
                _emit({"clock": clock.clock_snapshot()})
    finally:
        if dev is not None:
            dev.close()
    return 0


# --------------------------------------------------------------- parent side

class InjectorProcess:
    """Parent-side handle on the injector child."""

    def __init__(self, script_path, use_sudo=False):
        cmd = [sys.executable, script_path, "--injector-child"]
        if use_sudo:
            cmd = ["sudo", "--"] + cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.info = None
        self.error = None

    def _read(self, timeout=None):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("injector exited (code %s)" % self.proc.poll())
        return json.loads(line)

    def start(self):
        msg = self._read()
        self.info = msg.get("info")
        if not msg.get("ready"):
            self.error = msg.get("error", "unknown")
            return False
        return True

    def _send(self, obj):
        self.proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
        self.proc.stdin.flush()

    def run(self, schedule, lead_in_ms=500):
        self._send({"cmd": "run", "schedule": schedule, "lead_in_ms": lead_in_ms})
        return self._read()

    def stop(self):
        try:
            self._send("stop")
        except (BrokenPipeError, OSError, ValueError):
            pass

    def close(self):
        try:
            self._send({"cmd": "quit"})
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


class CdpInjector:
    """--simulate only: keys through CDP Input.dispatchKeyEvent.

    NOT a real test: this skips the kernel, compositor and Chrome's platform
    input path, and Chrome stamps the event when the DevTools message arrives
    (phase-a §0.6). It exists so the pipeline can be exercised without a display.
    We send without waiting for the renderer ack, to avoid ack pacing.
    """

    def __init__(self, cdp, session_id):
        self.cdp, self.session = cdp, session_id
        self.info = {"platform": "cdp-simulated", "clock": clock.CLOCK_NAME}
        self._stop = threading.Event()

    def start(self):
        return True

    def _key(self, letter, down):
        params = {"type": "keyDown" if down else "keyUp", "key": letter,
                  "code": keymap.dom_code(letter),
                  "windowsVirtualKeyCode": ord(letter.upper()),
                  "nativeVirtualKeyCode": ord(letter.upper())}
        if down:
            params["text"] = letter
        self.cdp.send_nowait("Input.dispatchKeyEvent", params, session=self.session)

    def run(self, schedule, lead_in_ms=500):
        records = []
        t_next = clock.now_ns() + int(lead_in_ms * 1e6)
        for idx, item in enumerate(schedule):
            if self._stop.is_set():
                break
            if idx > 0:
                t_next += int(item["gap_ms"] * 1e6)
            rec = {"i": item["i"], "letter": item["letter"], "planned_down_ns": t_next}
            clock.sleep_until_ns(t_next)
            rec["t_wake_ns"] = clock.now_ns()
            self._key(item["letter"], True)
            rec["t_down_ns"] = clock.now_ns()
            up = rec["t_down_ns"] + int(item["hold_ms"] * 1e6)
            clock.sleep_until_ns(up)
            self._key(item["letter"], False)
            rec["t_up_ns"] = clock.now_ns()
            rec["planned_up_ns"] = up
            records.append(rec)
            t_next = rec["t_up_ns"]
        self.cdp.drain(0.5)
        return {"done": True, "records": records, "aborted": self._stop.is_set()}

    def stop(self):
        self._stop.set()

    def close(self):
        pass
