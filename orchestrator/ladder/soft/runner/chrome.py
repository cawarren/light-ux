"""Find, check, launch and stop Chrome for a block (phase-a §4.2).

Adapted from tools/a1-check/a1lib/chrome.py (discovery, DevToolsActivePort, /proc switch
walk). Changes: an allow-list plus the §4.2 denylist lint, a version floor (Chrome >= 145,
the first with presentationTime on Element Timing), display mode flags, and no
--remote-debugging-port at all in M (measure) sessions, so no CDP client can attach.
Standard library only.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

MIN_MAJOR = 145

DENYLIST = ("--no-sandbox", "--disable-gpu-vsync", "--disable-frame-rate-limit", "--disable-web-security",
            "--enable-automation", "--disable-renderer-backgrounding", "--enable-unsafe",
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-gpu-sandbox", "--disable-setuid-sandbox", "--single-process", "--in-process-gpu",
            "--disable-site-isolation", "--js-flags", "--enable-benchmarking", "--run-all-compositor-stages")
ALLOW_PREFIXES = ("--user-data-dir=", "--no-first-run", "--no-default-browser-check", "--ozone-platform=",
                  "--start-fullscreen", "--start-maximized", "--new-window", "--window-size=",
                  "--window-position=", "--remote-debugging-port=")


def candidates():
    if sys.platform == "darwin":
        home = os.path.expanduser("~")
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                home + "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    names = ["google-chrome-stable", "google-chrome", "chrome", "chromium", "chromium-browser"]
    found = [shutil.which(n) for n in names]
    return [p for p in found if p] + ["/opt/google/chrome/chrome"]


def find_chrome(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for p in candidates():
        if p and os.path.exists(p):
            return p
    return None


def find_test_chromium():
    """The Playwright Chromium in the dev container (--simulate only)."""
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return hits[-1] if hits else None


def version_of(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as e:
        return "unknown (%s)" % e


def major_of(version_string):
    m = re.search(r"(\d+)\.\d+\.\d+\.\d+", version_string or "")
    return int(m.group(1)) if m else None


def flag_lint(flags, session_kind="M", simulate=False):
    """Raise ValueError on any flag outside the §4.2 allow-list or on the denylist.
    --simulate may add --headless=new and, as root in a container only, --no-sandbox."""
    bad = []
    for f in flags:
        if simulate and (f == "--headless=new" or (f == "--no-sandbox" and os.geteuid() == 0)):
            continue
        if any(f.startswith(d) for d in DENYLIST) or ("Throttling" in f and f.startswith("--disable-features")):
            bad.append(f)
        elif not f.startswith(ALLOW_PREFIXES):
            bad.append(f)
        elif f.startswith("--remote-debugging-port") and session_kind == "M" and not simulate:
            bad.append(f + " (no DevTools port in M sessions)")
    if bad:
        raise ValueError("refusing Chrome flags: %s" % bad)


def build_flags(profile, session_kind="M", display_mode="maximized", simulate=False, headless=False,
                wayland=None):
    flags = ["--user-data-dir=" + profile, "--no-first-run", "--no-default-browser-check"]
    if wayland is None:
        wayland = sys.platform.startswith("linux") and bool(os.environ.get("WAYLAND_DISPLAY"))
    if wayland and not headless:
        flags.append("--ozone-platform=wayland")
    if headless:
        flags += ["--headless=new", "--window-size=1280,800"]
    elif display_mode == "fullscreen":
        flags.append("--start-fullscreen")
    else:
        flags.append("--start-maximized")
    if session_kind != "M" or simulate:
        flags.append("--remote-debugging-port=0")
    if simulate and os.geteuid() == 0:
        flags.append("--no-sandbox")  # container only; recorded as sandbox_weakened, never in real runs
    flag_lint(flags, session_kind, simulate)
    return flags


class ChromeProc:
    def __init__(self, path, url, session_kind="M", display_mode="maximized", simulate=False, headless=False):
        self.profile = tempfile.mkdtemp(prefix="ladder-chrome-")
        self.flags = build_flags(self.profile, session_kind, display_mode, simulate, headless)
        self.cmd = [path] + self.flags + ["--new-window", url]
        self.t_launch = time.time()
        self._err = open(os.path.join(self.profile, "chrome-stderr.log"), "wb")
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.DEVNULL, stderr=self._err, start_new_session=True)

    @property
    def sandbox_weakened(self):
        return "--no-sandbox" in self.flags

    def devtools_ws(self, timeout=30):
        f = os.path.join(self.profile, "DevToolsActivePort")
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("Chrome exited early (code %s): %s" % (self.proc.returncode, self.stderr_tail()))
            if os.path.exists(f):
                lines = open(f).read().split()
                if len(lines) >= 2:
                    return "ws://127.0.0.1:%s%s" % (lines[0], lines[1])
            time.sleep(0.1)
        raise RuntimeError("Chrome did not open its DevTools port in %ss" % timeout)

    def stderr_tail(self, n=2000):
        try:
            return open(os.path.join(self.profile, "chrome-stderr.log"), "rb").read()[-n:].decode("utf-8", "replace")
        except OSError:
            return ""

    def child_switches(self):
        """Linux: switches Chrome passes to its children (e.g. --ozone-platform on the GPU process)."""
        if not sys.platform.startswith("linux"):
            return {}
        parent = {}
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    stat = open("/proc/%s/stat" % d).read()
                    parent[int(d)] = int(stat.rsplit(")", 1)[1].split()[1])
                except (OSError, IndexError, ValueError):
                    pass
        tree, frontier = set(), {self.proc.pid}
        while frontier:
            tree |= frontier
            frontier = {p for p, pp in parent.items() if pp in frontier} - tree
        found = {}
        for pid in tree:
            try:
                args = open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0")
            except OSError:
                continue
            args = [a.decode("utf-8", "replace") for a in args]
            ptype = next((a.split("=", 1)[1] for a in args if a.startswith("--type=")), "browser")
            for a in args:
                if a.startswith(("--ozone-platform", "--use-gl", "--use-angle")):
                    found.setdefault(a.split("=", 1)[0], set()).add(
                        (a.split("=", 1)[1] if "=" in a else "") + " [" + ptype + "]")
        return {k: sorted(v) for k, v in found.items()}

    def stop(self, wait_s=10):
        """Kill the process group and wait for the whole tree to exit (phase-a §4.2)."""
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 15)
            except (ProcessLookupError, PermissionError, AttributeError):
                self.proc.terminate()
            try:
                self.proc.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, 9)
                except (ProcessLookupError, PermissionError, AttributeError):
                    self.proc.kill()
                self.proc.wait(timeout=5)
        # children in the same session/group
        end = time.time() + wait_s
        while time.time() < end:
            try:
                os.killpg(self.proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.2)
        self._err.close()
        shutil.rmtree(self.profile, ignore_errors=True)
