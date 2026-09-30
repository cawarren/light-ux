# Vendored from tools/a1-check/a1lib/envinfo.py at commit 6ca4bc9 (A1 on-device check).
# Keep in sync by hand; local changes are marked 'ladder:'.
"""Best-effort host environment capture. Every probe is optional."""
from __future__ import annotations

import glob
import os
import platform
import re
import subprocess
import sys


def _run(cmd, timeout=5):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return re.sub(r"\x1b\[[0-9;]*m", "", out.stdout).strip()
    except Exception:
        return None


def _read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def collect():
    env = {
        "os": sys.platform,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }
    if sys.platform.startswith("linux"):
        env["linux"] = {
            "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
            "XDG_CURRENT_DESKTOP": os.environ.get("XDG_CURRENT_DESKTOP"),
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
            "DISPLAY": os.environ.get("DISPLAY"),
            "KDE_SESSION_VERSION": os.environ.get("KDE_SESSION_VERSION"),
            "os_release": dict(
                l.split("=", 1) for l in (_read("/etc/os-release") or "").splitlines() if "=" in l
            ).get("PRETTY_NAME", "").strip('"'),
            "kernel": platform.release(),
            "cmdline_psr": [a for a in (_read("/proc/cmdline") or "").split()
                            if "psr" in a or "panel_replay" in a],
            "ac_online": [_read(p) for p in glob.glob("/sys/class/power_supply/A*/online")],
            "governors": sorted({_read(p) for p in
                                 glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")} - {None}),
            "power_profile": _run(["powerprofilesctl", "get"]),
            "kscreen": _run(["kscreen-doctor", "-o"]),
        }
    elif sys.platform == "darwin":
        env["mac"] = {
            "sw_vers": _run(["sw_vers"]),
            "model": _run(["sysctl", "-n", "hw.model"]),
            "cpu": _run(["sysctl", "-n", "machdep.cpu.brand_string"]),
            "power": _run(["pmset", "-g", "batt"]),
            "low_power_mode": _run(["pmset", "-g"]),
        }
        if env["mac"]["low_power_mode"]:
            m = re.search(r"lowpowermode\s+(\d)", env["mac"]["low_power_mode"])
            env["mac"]["low_power_mode"] = m.group(1) if m else None
    return env
