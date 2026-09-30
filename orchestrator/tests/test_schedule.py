import json
import unittest

from _util import POOLS
from ladder.soft.runner import keymap, schedule


class ScheduleTest(unittest.TestCase):
    def plan(self, seed=7, **kw):
        args = dict(reps=2, first_key=30, seq=3, warmup=5, max_keys=40)
        args.update(kw)
        return schedule.plan_session(seed, ["r1-vite", "r3-no-framework"], ["10k", "50k"], POOLS, **args)

    def test_seeded_determinism(self):
        a, b = self.plan(7), self.plan(7)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertNotEqual(json.dumps(a, sort_keys=True), json.dumps(self.plan(8), sort_keys=True))

    def all_keys(self, plan):
        return [k for b in plan["blocks"] for s in b["segments"] for k in s["keys"]]

    def test_jitter_bounds(self):
        keys = self.all_keys(self.plan(11))
        for k in keys:
            self.assertTrue(30.0 <= k["hold_ms"] <= 60.0, k)
            if k["after"] == "settle":
                self.assertTrue(50.0 <= k["gap_ms"] <= 250.0, k)
            else:
                self.assertEqual(k["kind"], "A.seq")
                self.assertTrue(90.0 <= k["gap_ms"] <= 117.0, k)
                self.assertLess(k["hold_ms"], k["gap_ms"])     # key-up before the next key-down
        cad = [k["gap_ms"] for k in keys if k["after"] == "prev_down"]
        self.assertGreater(len(set(cad)), len(cad) * 0.9)       # continuous, not a lattice
        self.assertLess(min(cad), 95)
        self.assertGreater(max(cad), 112)

    def test_block_order_abba_and_counts(self):
        p = self.plan(3)
        order = [(b["rung"], b["size"]) for b in p["blocks"]]
        self.assertEqual(len(order), 8)
        self.assertEqual(order[:4], order[4:][::-1])
        for cond in set(order):
            self.assertEqual(order.count(cond), 2)
        for cond in set(order):
            bs = [b for b in p["blocks"] if (b["rung"], b["size"]) == cond]
            self.assertEqual(sum(b["counts"]["first_key"] for b in bs), 30)
            self.assertEqual(sum(b["counts"]["seq"] for b in bs), 3)

    def test_units_and_expected_values(self):
        p = self.plan(5)
        for b in p["blocks"]:
            idx = [k["i"] for s in b["segments"] for k in s["keys"]]
            self.assertEqual(idx, list(range(len(idx))))
            for s in b["segments"]:
                self.assertLessEqual(len(s["keys"]), 40)
                value = ""
                for k in s["keys"]:
                    value = keymap.apply(value, k["key"])
                    self.assertEqual(k["expect"], value)
                self.assertEqual(value, "")                   # every segment returns to empty
            warm = [k for s in b["segments"] for k in s["keys"] if k["warmup"]]
            self.assertEqual(len(warm), 10)                   # 5 warm-up first_key + clear
            self.assertTrue(all(k["kind"] in ("A.first_key", "A.clear") for k in warm))
            seqs = [k for s in b["segments"] for k in s["keys"] if k["kind"] == "A.seq"]
            self.assertEqual(sorted({k["seq_pos"] for k in seqs}), list(range(40)))

    def test_all_keys_injectable(self):
        for k in self.all_keys(self.plan(9)):
            self.assertIn(k["key"], keymap.EVDEV)
            self.assertIn(k["key"], keymap.MAC_VK)

    def test_split_counts(self):
        self.assertEqual(schedule.split_counts(7, 2), [4, 3])
        self.assertEqual(sum(schedule.split_counts(301, 4)), 301)


class KeymapTest(unittest.TestCase):
    def test_codes_unique(self):
        self.assertEqual(len(set(keymap.EVDEV.values())), len(keymap.EVDEV))
        self.assertEqual(len(set(keymap.MAC_VK.values())), len(keymap.MAC_VK))

    def test_cdp_params(self):
        self.assertEqual(keymap.cdp_params("Backspace", True)["type"], "rawKeyDown")
        p = keymap.cdp_params(" ", True)
        self.assertEqual((p["code"], p["text"], p["windowsVirtualKeyCode"]), ("Space", " ", 32))
        self.assertEqual(keymap.cdp_params("7", True)["code"], "Digit7")


if __name__ == "__main__":
    unittest.main()
