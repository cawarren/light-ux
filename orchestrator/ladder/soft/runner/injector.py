"""Key injector: runs one segment's schedule against a key backend, settle-aware.

Adapted from tools/a1-check/a1lib/injector.py (child-process protocol, realtime setup,
sleep-then-spin deadlines). Changes for the harness:
  - schedule items wait either for the page-reported settle of the previous key
    (`after: "settle"`) or for the previous key-down (`after: "prev_down"`, A.seq cadence);
  - settle notices reach the injector as lines "settled <t_ns> <n_keys> <flip_seq>", where
    t_ns is the host-clock time the harness server received the page's message and n_keys the
    number of keydowns the page has seen since the segment was armed (stale notices are
    skipped by count);
  - the backend is uinput (Linux), Quartz (macOS) or, in --simulate only, CDP.

Real injectors run in a child process (python3 injector_main.py) so the HTTP/WebSocket
threads of the runner never hold the GIL when a key is due, and so that on Linux only this
process can be started with sudo. Protocol (JSON lines):
  child  -> {"ready": true, "info": {...}}  |  {"ready": false, "error": "..."}
  parent -> {"cmd": "run", "keys": [...], "lead_in_ms": 300, "settle_timeout_ms": 2000,
             "final_settle": true}
  parent -> "settled <t_ns> <n> <seq>"  |  "stop"
  child  -> {"done": true, "records": [...], "aborted": bool, "final": {...}, "clock_start", "clock_end"}
  parent -> {"cmd": "selftest", "n": 2000}  -> {"selftest": {...}}
  parent -> {"cmd": "quit"}
Standard library only.
"""
from __future__ import annotations

import gc
import json
import os
import queue
import random
import select
import subprocess
import sys
import threading

from . import clock, keymap

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ line sources

class RawLines:
    """Line reader on a raw fd, so select() tells the truth about buffered data."""

    def __init__(self, fd):
        self.fd, self.buf = fd, b""

    def poll(self, timeout):
        if b"\n" not in self.buf:
            r, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
            if r:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    return "__eof__"
                self.buf += chunk
        if b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            return line.decode().strip()
        return None


class QueueLines:
    """In-process equivalent (simulate mode): the server thread puts lines on a queue."""

    def __init__(self):
        self.q = queue.Queue()

    def put(self, line):
        self.q.put(line)

    def poll(self, timeout):
        try:
            return self.q.get(timeout=max(0.0, timeout)) if timeout > 0 else self.q.get_nowait()
        except queue.Empty:
            return None


# ------------------------------------------------------------------ executor

class Executor:
    """Runs a key list. backend.key(k, down) must write the key and return nothing;
    the executor stamps clock.now_ns() right after it returns (t_inject)."""

    def __init__(self, backend, lines):
        self.backend, self.lines = backend, lines
        self.aborted = False

    def _wait_settle(self, expected_n, timeout_ns):
        deadline = clock.now_ns() + timeout_ns
        while True:
            left = deadline - clock.now_ns()
            if left <= 0:
                return None
            line = self.lines.poll(left / 1e9)
            if line is None:
                continue
            if line in ("stop", "__eof__"):
                self.aborted = True
                return None
            if line.startswith("settled "):
                parts = line.split()
                t_ns, n, seq = int(parts[1]), int(parts[2]), int(parts[3])
                if n == expected_n:
                    return {"t_ns": t_ns, "seq": seq, "t_rx_ns": clock.now_ns()}

    def _check_stop(self):
        line = self.lines.poll(0)
        while line is not None:
            if line in ("stop", "__eof__"):
                self.aborted = True
                return True
            line = self.lines.poll(0)
        return False

    def run(self, keys, lead_in_ms=300, settle_timeout_ms=2000, final_settle=True):
        records, count, prev_down = [], 0, None
        timeout_ns = int(settle_timeout_ms * 1e6)
        t_start = clock.now_ns() + int(lead_in_ms * 1e6)
        gc.disable()
        try:
            for item in keys:
                rec = {"i": item["i"], "key": item["key"]}
                if item["after"] == "settle":
                    if count == 0:
                        t_ref = t_start
                        rec["ref"] = "lead_in"
                    else:
                        s = self._wait_settle(count, timeout_ns)
                        if self.aborted:
                            break
                        if s is None:
                            t_ref = clock.now_ns()
                            rec["ref"] = "settle_timeout"
                        else:
                            t_ref = s["t_ns"]
                            rec["ref"] = "settle"
                            rec["settle_flip_seq"] = s["seq"]
                            rec["t_settle_rx_ns"] = s["t_rx_ns"]
                else:
                    if self._check_stop():
                        break
                    t_ref = prev_down
                    rec["ref"] = "prev_down"
                planned = t_ref + int(item["gap_ms"] * 1e6)
                rec["t_ref_ns"] = t_ref
                rec["planned_down_ns"] = planned
                clock.sleep_until_ns(planned)
                rec["t_wake_ns"] = clock.now_ns()
                self.backend.key(item["key"], True)
                t_down = clock.now_ns()
                rec["t_down_ns"] = t_down
                up_deadline = t_down + int(item["hold_ms"] * 1e6)
                clock.sleep_until_ns(up_deadline)
                self.backend.key(item["key"], False)
                rec["t_up_ns"] = clock.now_ns()
                rec["planned_up_ns"] = up_deadline
                records.append(rec)
                prev_down = t_down
                count += 1
            final = None
            if final_settle and not self.aborted and count:
                s = self._wait_settle(count, timeout_ns)
                final = {"ref": "settle", "t_ns": s["t_ns"], "seq": s["seq"]} if s else {"ref": "settle_timeout",
                                                                                           "t_ns": clock.now_ns()}
        finally:
            gc.enable()
        return {"done": True, "records": records, "aborted": self.aborted, "final": final}


def jitter_selftest(n=2000, seed=1):
    """Scheduling error of sleep-then-spin to absolute deadlines (no key is written).

    phase-a §3.3 asks for 2,000 scheduled writes to a second, unfocused uinput device; any key
    device delivers to the focused window, so this writes nothing and times the wake-ups only.
    The in-block actual-vs-planned key-down error is recorded for every key as well.
    """
    r = random.Random(seed)
    errs = []
    gc.disable()
    try:
        t = clock.now_ns() + 20_000_000
        for _ in range(n):
            t += int(r.uniform(2.0, 6.0) * 1e6)
            clock.sleep_until_ns(t)
            errs.append((clock.now_ns() - t) / 1e3)  # us
    finally:
        gc.enable()
    errs.sort()
    q = lambda p: errs[min(len(errs) - 1, int(round(p * (len(errs) - 1))))]
    return {"n": n, "p50_us": q(.5), "p99_us": q(.99), "max_us": errs[-1], "unit": "us"}


# ------------------------------------------------------------------ backends

class UInputBackend:
    def __init__(self):
        from . import uinput
        self.dev = uinput.UInputKeyboard(keymap.EVDEV.values()).open()
        self.info = {"device": uinput.DEVICE_NAME.decode(), "sysname": self.dev.sysname}

    def key(self, k, down):
        self.dev.key(keymap.EVDEV[k], down)

    def close(self):
        self.dev.close()


class QuartzBackend:
    def __init__(self):
        from . import quartz
        self.q = quartz
        self.kb = quartz.QuartzKeyboard()
        self.info = {"mach_timebase": list(quartz.mach_timebase())}

    def key(self, k, down):
        ev = self.kb.make(keymap.MAC_VK[k], down)
        self.kb.post(ev)
        self.kb.release(ev)

    def close(self):
        self.kb.close()


class CdpBackend:
    """--simulate only. NOT A REAL MEASUREMENT: CDP input skips the kernel, compositor and
    Chrome's platform input path, and Chrome stamps the event when the DevTools message
    arrives (phase-a §0.6). Sent without waiting for the renderer ack (no ack pacing)."""

    def __init__(self, cdp, session):
        self.cdp, self.session = cdp, session
        self.info = {"backend": "cdp-simulated", "NOT_REAL": True}

    def key(self, k, down):
        self.cdp.send_nowait("Input.dispatchKeyEvent", keymap.cdp_params(k, down), session=self.session)

    def close(self):
        pass


# ------------------------------------------------------------------ child process

def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _pick_cpu():
    """Best effort: the highest-max-frequency CPU other than cpu0 (a P-core on hybrid parts)."""
    best = None
    try:
        cpus = sorted(os.sched_getaffinity(0))
    except AttributeError:
        return None
    for c in cpus:
        if c == 0 and len(cpus) > 1:
            continue
        try:
            f = int(open("/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq" % c).read())
        except (OSError, ValueError):
            f = 0
        if best is None or f > best[1]:
            best = (c, f)
    return best[0] if best else None


def _try_realtime(cpu=None):
    info = {}
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
        info["sched"] = "SCHED_FIFO/50"
    except (AttributeError, PermissionError, OSError) as e:
        info["sched"] = "default (%s)" % type(e).__name__
    try:
        c = cpu if cpu is not None else _pick_cpu()
        if c is not None:
            os.sched_setaffinity(0, {c})
            info["cpu"] = c
    except (AttributeError, OSError) as e:
        info["cpu"] = "unpinned (%s)" % type(e).__name__
    return info


def child_main(argv=None):
    argv = argv or []
    cpu = None
    if "--cpu" in argv:
        cpu = int(argv[argv.index("--cpu") + 1])
    platform = "linux" if clock.IS_LINUX else ("mac" if clock.IS_MAC else "other")
    info = {"platform": platform, "clock": clock.CLOCK_NAME, "pid": os.getpid(),
            "euid": os.geteuid(), "python": sys.version.split()[0]}
    backend = None
    try:
        if platform == "linux":
            backend = UInputBackend()
        elif platform == "mac":
            from . import quartz
            acc = quartz.post_access()
            info["access"] = acc
            if not acc["post_event_access"]:
                quartz.request_access()
                _emit({"ready": False, "error": "no_accessibility", "info": info})
                return 3
            backend = QuartzBackend()
        else:
            _emit({"ready": False, "error": "unsupported platform", "info": info})
            return 2
        info.update(backend.info)
    except Exception as e:
        _emit({"ready": False, "error": "%s: %s" % (type(e).__name__, e), "info": info})
        return 2
    info.update(_try_realtime(cpu))
    _emit({"ready": True, "info": info})
    lines = RawLines(sys.stdin.fileno())
    try:
        while True:
            line = lines.poll(1.0)
            if line is None or line == "stop" or (line and line.startswith("settled ")):
                continue
            if line == "__eof__":
                break
            msg = json.loads(line)
            if msg.get("cmd") == "quit":
                break
            if msg.get("cmd") == "run":
                snap0 = clock.clock_snapshot()
                res = Executor(backend, lines).run(msg["keys"], msg.get("lead_in_ms", 300),
                                                   msg.get("settle_timeout_ms", 2000), msg.get("final_settle", True))
                res.update({"clock_start": snap0, "clock_end": clock.clock_snapshot()})
                _emit(res)
            elif msg.get("cmd") == "selftest":
                _emit({"selftest": jitter_selftest(msg.get("n", 2000))})
            elif msg.get("cmd") == "clock":
                _emit({"clock": clock.clock_snapshot()})
    finally:
        backend.close()
    return 0


# ------------------------------------------------------------------ parent side

class InjectorProcess:
    """Parent-side handle on the injector child (real runs)."""

    def __init__(self, use_sudo=False, cpu=None):
        cmd = [sys.executable, os.path.join(HERE, "injector_main.py")]
        if cpu is not None:
            cmd += ["--cpu", str(cpu)]
        if use_sudo:
            cmd = ["sudo", "--"] + cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.info, self.error = None, None
        self._wlock = threading.Lock()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("injector exited (code %s)" % self.proc.poll())
        return json.loads(line)

    def _send(self, obj):
        with self._wlock:
            self.proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
            self.proc.stdin.flush()

    def start(self):
        msg = self._read()
        self.info = msg.get("info")
        if not msg.get("ready"):
            self.error = msg.get("error", "unknown")
            return False
        return True

    def run(self, keys, lead_in_ms=300, settle_timeout_ms=2000, final_settle=True):
        self._send({"cmd": "run", "keys": keys, "lead_in_ms": lead_in_ms,
                    "settle_timeout_ms": settle_timeout_ms, "final_settle": final_settle})
        return self._read()

    def settled(self, t_ns, n, seq):
        try:
            self._send("settled %d %d %d" % (t_ns, n, seq))
        except (BrokenPipeError, OSError, ValueError):
            pass

    def selftest(self, n=2000):
        self._send({"cmd": "selftest", "n": n})
        return self._read()["selftest"]

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


class InProcessInjector:
    """Simulate mode: same Executor, CDP backend, settle notices through a queue."""

    def __init__(self, backend):
        self.backend = backend
        self.lines = QueueLines()
        self.info = dict(backend.info)

    def start(self):
        return True

    def run(self, keys, lead_in_ms=300, settle_timeout_ms=2000, final_settle=True):
        # drop stale notices from a previous segment
        while self.lines.poll(0) is not None:
            pass
        snap0 = clock.clock_snapshot()
        res = Executor(self.backend, self.lines).run(keys, lead_in_ms, settle_timeout_ms, final_settle)
        res.update({"clock_start": snap0, "clock_end": clock.clock_snapshot()})
        return res

    def settled(self, t_ns, n, seq):
        self.lines.put("settled %d %d %d" % (t_ns, n, seq))

    def selftest(self, n=2000):
        return jitter_selftest(n)

    def stop(self):
        self.lines.put("stop")

    def close(self):
        pass
