"""Seeded, counterbalanced, interleaved and blind schedules."""
import json
import tempfile
import threading
import unittest
import urllib.request
from collections import Counter
from pathlib import Path

from . import _path  # noqa: F401
from playlib import config as C
from playlib.schedule import Staircase, blind_schedule, jnd_constant_schedule

PROMPTS = ["config", "install", "search", "format", "terminal"]


class Blind(unittest.TestCase):
    def test_seeded(self):
        a = blind_schedule("s1", 40, PROMPTS)
        self.assertEqual(a, blind_schedule("s1", 40, PROMPTS))
        self.assertNotEqual(a, blind_schedule("s2", 40, PROMPTS))

    def test_counts_and_counterbalance(self):
        s = blind_schedule("s1", 40, PROMPTS)
        self.assertEqual(len(s), 160)
        self.assertEqual([t["index"] for t in s], list(range(160)))
        per = Counter((t["pair_name"], t["order"]) for t in s)
        for pair in C.BLIND_PAIRS:
            name = C.pair_name(pair)
            self.assertEqual(per[(name, "AB")], 20, name)
            self.assertEqual(per[(name, "BA")], 20, name)
        for t in s:
            a, b = t["pair"]
            self.assertEqual(t["rungs"], [a, b] if t["order"] == "AB" else [b, a])
            self.assertIn(t["prompt"], PROMPTS)
            self.assertEqual(t["added_ms"], [0, 0])
        self.assertEqual({C.pair_name(p) for p in C.BLIND_PAIRS}, {"R1-R3", "R1-R2", "R2-R3", "R3-R3"})

    def test_odd_counts(self):
        s = blind_schedule("odd", 5, PROMPTS)
        for pair in C.BLIND_PAIRS:
            c = Counter(t["order"] for t in s if t["pair"] == list(pair))
            self.assertEqual(sum(c.values()), 5)
            self.assertLessEqual(abs(c["AB"] - c["BA"]), 1)

    def test_interleaved(self):
        s = blind_schedule("s1", 40, PROMPTS)
        runs = 1 + sum(1 for x, y in zip(s, s[1:]) if x["pair_name"] != y["pair_name"])
        self.assertGreater(runs, 60)  # pairs are mixed, not blocked (160 trials)
        first_half = Counter(t["pair_name"] for t in s[:80])
        self.assertTrue(all(10 <= v <= 30 for v in first_half.values()), first_half)


class Jnd(unittest.TestCase):
    def test_constant_stimuli(self):
        s = jnd_constant_schedule("j", 20, PROMPTS)
        self.assertEqual(len(s), 20 * len(C.JND_LEVELS))
        self.assertEqual(s, jnd_constant_schedule("j", 20, PROMPTS))
        for lv in C.JND_LEVELS:
            g = [t for t in s if t["level_ms"] == lv]
            self.assertEqual(Counter(t["order"] for t in g), Counter({"RC": 10, "CR": 10}))
            for t in g:
                self.assertEqual(t["rungs"], ["r3", "r3"])
                self.assertEqual(t["added_ms"], [0, lv] if t["order"] == "RC" else [lv, 0])

    def test_staircase(self):
        st = Staircase("x", PROMPTS, max_trials=60, max_reversals=12)
        top = len(st.levels) - 1
        self.assertEqual(st.state([])["idx"], top)
        self.assertEqual(st.state([True, True])["idx"], top)
        self.assertEqual(st.state([True, True, True])["idx"], top - 1)
        self.assertEqual(st.state([True, True, True, False])["idx"], top)
        self.assertEqual(st.state([True, True, True, False])["reversals"], 1)
        self.assertEqual(st.next_trial([True] * 5)["index"], 5)
        self.assertEqual(st.next_trial([True] * 5), st.next_trial([True] * 5))  # seeded per index
        self.assertIsNone(st.next_trial([True] * 60))
        self.assertIsNone(Staircase("x", PROMPTS, max_reversals=2).next_trial([True] * 3 + [False] + [True] * 3))


class BlindOverHttp(unittest.TestCase):
    """The page only ever sees opaque tokens: no rung or delay in any API response."""

    def test_next_response_is_blind(self):
        from playlib.server import serve

        tmp = tempfile.mkdtemp()
        defaults = {"size": "1k", "trials_per_pair": 2, "jnd_method": "constant", "jnd_reps": 1, "jnd_levels": None,
                    "delay_mode": "defer", "allow_no_difference": False, "break_every": 20, "ready_ms": 0,
                    "settle_ms": 0, "post_ms": 0, "seed": "http"}
        httpd = serve("127.0.0.1", 0, Path(tmp), defaults)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return r.read().decode(), dict(r.headers)

        try:
            if not all(r.available for r in C.RUNGS.values()):
                self.skipTest("rungs not built")
            for kind in ("blind", "jnd"):
                info = json.loads(post("/api/sessions", {"kind": kind})[0])
                raw, headers = post(f"/api/sessions/{info['id']}/next")
                self.assertEqual(headers["Cross-Origin-Embedder-Policy"], "require-corp")
                self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
                nx = json.loads(raw)
                self.assertEqual(set(nx), {"done", "index", "prompt", "urls", "progress"})
                for u in nx["urls"]:
                    self.assertRegex(u, r"^/t/[0-9a-f]{16}/\?items=/dataset/1k/items\.json$")
                blob = raw.lower()
                for word in ("r1", "r2", "r3", "rung", "vite", "diligent", "framework", "added", "level", "order"):
                    self.assertNotIn(word, blob.replace(nx["prompt"].lower(), ""))
                raw_w = post(f"/api/sessions/{info['id']}/warmup")[0]
                self.assertNotRegex(raw_w.lower(), r"r[1-3]\b|vite|diligent")
                # Rung page: neutral title, helper injected first.
                with urllib.request.urlopen(base + nx["urls"][0]) as r:
                    html = r.read().decode()
                self.assertIn("<title>Palette</title>", html)
                self.assertLess(html.index("__i.js"), html.index("/ladder/probe.js"))
                self.assertNotRegex(html, r"(?i)\bR[1-4]\b|typical|diligent|no framework|r\d-list")
                # Interval helper config: blind → no editing; JND → editing; both hide the marker.
                with urllib.request.urlopen(base + nx["urls"][0].split("?")[0] + "__i.js") as r:
                    cfg = json.loads(r.read().decode().split("\n", 1)[0].split("=", 1)[1].rstrip(";"))
                self.assertEqual(cfg["edit"], "none" if kind == "blind" else "all")
                self.assertEqual(cfg["marker"], "hide")
                self.assertEqual(cfg["enter"], "end")
            # Open mode: marker shown, editing on, Enter goes to the rung.
            op = json.loads(post("/api/open", {"rung": "r3", "size": "1k"})[0])
            with urllib.request.urlopen(base + op["url"].split("?")[0] + "__i.js") as r:
                cfg = json.loads(r.read().decode().split("\n", 1)[0].split("=", 1)[1].rstrip(";"))
            self.assertEqual((cfg["marker"], cfg["edit"], cfg["enter"]), ("show", "all", "pass"))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
