import json
import os
import tempfile
import unittest

import _util  # noqa: F401
from ladder.soft.analysis import gate


def cmp(ratio, rlo, frames, flo):
    return {"ratio": ratio, "ratio_ci": (rlo, ratio * 1.2), "diff_frames": frames, "diff_frames_ci": (flo, frames + 1)}


class GapTest(unittest.TestCase):
    def test_ratio_route(self):
        self.assertTrue(gate.gap_verdict(cmp(3.2, 2.1, 1.0, 0.5))["pass"])
        self.assertFalse(gate.gap_verdict(cmp(3.2, 1.9, 1.0, 0.5))["pass"])   # CI low < 2
        self.assertFalse(gate.gap_verdict(cmp(2.9, 2.5, 1.0, 0.5))["pass"])

    def test_frames_route(self):
        v = gate.gap_verdict(cmp(1.5, 1.2, 2.1, 1.1))
        self.assertTrue(v["pass"])
        self.assertTrue(v["by_frames"])
        self.assertFalse(gate.gap_verdict(cmp(1.5, 1.2, 2.1, 0.9))["pass"])
        self.assertFalse(gate.gap_verdict(cmp(1.5, 1.2, 1.9, 1.5))["pass"])

    def test_no_data(self):
        self.assertIsNone(gate.gap_verdict(None)["pass"])


class PerceptibilityTest(unittest.TestCase):
    def write(self, obj):
        fd, p = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
        self.addCleanup(os.remove, p)
        return p

    def test_pending_without_file(self):
        self.assertEqual(gate.perceptibility(None)["status"], "pending")

    def test_pass_and_placebo(self):
        p = self.write({"pairs": [{"a": "r1-vite", "b": "r3-no-framework", "n": 40, "correct": 30},
                                  {"a": "r3-no-framework", "b": "r3-no-framework", "n": 40, "correct": 21}]})
        self.assertEqual(gate.perceptibility(p)["status"], "pass")

    def test_placebo_above_chance_fails(self):
        p = self.write({"pairs": [{"pair": ["r1-typical", "r3-no-framework"], "n": 40, "correct": 30},
                                  {"pair": ["r3-no-framework", "r3-no-framework"], "n": 40, "correct": 30}]})
        self.assertEqual(gate.perceptibility(p)["status"], "fail")

    def test_too_few_trials_pending_and_chance_fails(self):
        p = self.write({"pairs": [{"a": "r1-vite", "b": "r3-no-framework", "n": 30, "correct": 29}]})
        self.assertEqual(gate.perceptibility(p)["status"], "pending")
        p = self.write({"pairs": [{"a": "r1-vite", "b": "r3-no-framework", "n": 40, "correct": 22},
                                  {"a": "r3-no-framework", "b": "r3-no-framework", "n": 40, "correct": 20}]})
        self.assertEqual(gate.perceptibility(p)["status"], "fail")

    def test_trial_list_format(self):
        trials = [{"a": "r3-no-framework", "b": "r1-vite", "correct": k < 31} for k in range(40)]
        trials += [{"a": "r3-no-framework", "b": "r3-no-framework", "correct": k < 20} for k in range(40)]
        trials += [{"a": "r1-vite", "b": "r3-no-framework", "correct": None}]   # "no difference": ignored
        out = gate.perceptibility(self.write({"trials": trials}))
        self.assertEqual(out["status"], "pass")
        main = [p for p in out["pairs"] if p["a"] != p["b"]][0]
        self.assertEqual((main["n"], main["correct"]), (40, 31))


class GateATest(unittest.TestCase):
    def test_both_required(self):
        gp, gf = {"pass": True}, {"pass": False}
        self.assertEqual(gate.gate_a(gp, {"status": "pass"}), "PASS")
        self.assertEqual(gate.gate_a(gp, {"status": "fail"}), "FAIL")
        self.assertEqual(gate.gate_a(gf, {"status": "pass"}), "FAIL")
        self.assertTrue(gate.gate_a(gp, {"status": "pending"}).startswith("PENDING"))
        self.assertEqual(gate.gate_a(gp, {"status": "pass"}, simulated=True), "NOT VALID (simulated)")
        self.assertEqual(gate.gate_a(gp, {"status": "pass"}, valid_settings=False), "NOT AT HEADLINE SETTINGS")


if __name__ == "__main__":
    unittest.main()
