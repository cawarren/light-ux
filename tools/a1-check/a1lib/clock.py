"""The host clock that everything on the Python side stamps with.

Chrome's base::TimeTicks (and so performance.now() and event.timeStamp, up to a
constant per-page offset) is:
  - Linux: CLOCK_MONOTONIC
  - macOS: mach_absolute_time(), which in ns is CLOCK_UPTIME_RAW
We stamp the injector, the clock-sync server and every log line with the same
clock, so a single offset maps injector time into page time (phase-a §2.5, §3.4).
"""
import sys
import time

IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

if IS_MAC and hasattr(time, "CLOCK_UPTIME_RAW"):
    CLOCK_ID = time.CLOCK_UPTIME_RAW
    CLOCK_NAME = "CLOCK_UPTIME_RAW"
else:
    CLOCK_ID = time.CLOCK_MONOTONIC
    CLOCK_NAME = "CLOCK_MONOTONIC"


def now_ns() -> int:
    return time.clock_gettime_ns(CLOCK_ID)


def clock_snapshot() -> dict:
    """Read the related clocks back to back (logged at block start/end)."""
    snap = {"clock": CLOCK_NAME, "now_ns": now_ns()}
    for name in ("CLOCK_MONOTONIC", "CLOCK_BOOTTIME", "CLOCK_MONOTONIC_RAW",
                 "CLOCK_UPTIME_RAW", "CLOCK_REALTIME"):
        cid = getattr(time, name, None)
        if cid is not None:
            try:
                snap[name] = time.clock_gettime_ns(cid)
            except OSError:
                pass
    if IS_MAC:
        try:
            from . import quartz
            snap["mach_absolute_time"] = quartz.mach_absolute_time()
            snap["mach_timebase"] = list(quartz.mach_timebase())
        except Exception as e:  # pragma: no cover - macOS only
            snap["mach_error"] = repr(e)
    return snap


def sleep_until_ns(deadline_ns: int, spin_ns: int = 1_000_000) -> None:
    """Sleep to an absolute deadline on our clock, spinning for the last ~1 ms.

    Python has no clock_nanosleep(TIMER_ABSTIME); time.sleep plus a short spin
    is what phase-a §3.3 suggests. Jitter is measured, not assumed.
    """
    while True:
        remaining = deadline_ns - now_ns()
        if remaining <= 0:
            return
        if remaining > spin_ns:
            time.sleep((remaining - spin_ns) / 1e9)
        # else: spin
