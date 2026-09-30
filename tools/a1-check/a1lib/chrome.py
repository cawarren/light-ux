"""Find, launch and stop Chrome with a fresh temporary profile.

Flags are limited to the phase-a §4.2 allow-list. The one exception is
--simulate as root in a container (headless test runs), where Chrome cannot
start without --no-sandbox; that is recorded in the report and never used for
a real run.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

DENYLIST = ("--no-sandbox", "--disable-gpu-vsync", "--disable-frame-rate-limit",
            "--disable-web-security", "--enable-automation",
            "--disable-renderer-backgrounding", "--enable-unsafe")


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
    """The Playwright Chromium in this dev container (for --simulate)."""
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return hits[-1] if hits else None


def version_of(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as e:
        return "unknown (%s)" % e


def flag_lint(flags, allow_no_sandbox=False):
    bad = [f for f in flags if any(f.startswith(d) for d in DENYLIST)
           and not (allow_no_sandbox and f == "--no-sandbox")]
    if bad:
        raise ValueError("refusing denylisted Chrome flags: %s" % bad)


class ChromeProc:
    def __init__(self, path, url, headless=False, no_sandbox=False, window=(1100, 800)):
        self.profile = tempfile.mkdtemp(prefix="a1-chrome-")
        self.flags = [
            "--user-data-dir=" + self.profile,
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            "--window-size=%d,%d" % window,
            "--window-position=40,40",
        ]
        if headless:
            self.flags.append("--headless=new")
        if no_sandbox:
            self.flags.append("--no-sandbox")
        flag_lint(self.flags, allow_no_sandbox=no_sandbox)
        self.cmd = [path] + self.flags + ["--new-window", url]
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.DEVNULL,
                                     stderr=open(os.path.join(self.profile, "chrome-stderr.log"), "wb"),
                                     start_new_session=True)

    def devtools_ws(self, timeout=30):
        f = os.path.join(self.profile, "DevToolsActivePort")
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("Chrome exited early (code %s). See %s" % (
                    self.proc.returncode, os.path.join(self.profile, "chrome-stderr.log")))
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
        """Linux: the switches Chrome passes to its child processes, e.g.
        --ozone-platform=wayland|x11 on the GPU process. Walks /proc from our pid."""
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

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        shutil.rmtree(self.profile, ignore_errors=True)
