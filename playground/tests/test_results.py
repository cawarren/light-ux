"""Results file schema, resumable sessions and the report / Gate A verdict."""
import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from . import _path  # noqa: F401
from playlib import report
from playlib.results import SCHEMA, Session, read_records, validate

PROMPTS = ["config", "install", "search"]


def iv(p50, nb=0):
    return {"load_ms": 100, "hold_ms": 3000, "typed": "config",
            "latency": {"p50": p50, "p95": p50 + 5, "n_measured": 6, "n_backspace": nb, "applied_delay_p50": 0.8}}


def answer_for(t, correct, lat=(80.0, 20.0)):
    """Payload that answers trial t correctly (or not); latencies follow the slower/faster roles."""
    exp = None
    if "pair" in t and t["rungs"][0] != t["rungs"][1]:
        exp = 1 if int(t["rungs"][0][1]) > int(t["rungs"][1][1]) else 2
    if "level_ms" in t and t["level_ms"]:
        exp = 1 if t["added_ms"][0] == 0 else 2
    ans = 1 if exp is None else (exp if correct else 3 - exp)
    slow, fast = lat
    ivs = [iv(fast), iv(slow)] if exp == 1 else [iv(slow), iv(fast)]
    return {"index": t["index"], "answer": ans, "confidence": 2, "rt_ms": 900, "intervals": ivs,
            "client": {"browser": "Chrome 145", "frame_ms": 16.67, "userAgent": "should be dropped"}}


class Sessions(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_record_schema_and_resume(self):
        s = Session.create(self.dir, "blind", seed="abc", trials_per_pair=2, prompts=PROMPTS)
        first = s.next_trial()
        rec = s.record(first["index"], answer_for(first, True))
        self.assertEqual(validate(rec), [])
        self.assertEqual(rec["schema"], SCHEMA)
        self.assertNotIn("userAgent", rec["client"])
        self.assertEqual(rec["typed"], ["config", "config"])
        self.assertTrue(all("typed" not in x for x in rec["intervals"]))
        self.assertEqual([x["rung"] for x in rec["intervals"]], first["rungs"])
        if rec["expected_faster_interval"]:
            self.assertTrue(rec["correct"])
            self.assertEqual(rec["chosen_rung"], max(first["rungs"]))
            self.assertTrue(rec["chose_lower_measured_p50"])
        else:
            self.assertIsNone(rec["correct"])
        # Double submit is idempotent; out-of-order trial is rejected.
        s.record(first["index"], answer_for(first, False))
        with self.assertRaises(ValueError):
            s.record(first["index"] + 3, answer_for(first, True))
        # Resume from disk: same schedule, next trial, a new sitting.
        s2 = Session(s.path)
        self.assertEqual(s2.schedule, s.schedule)
        self.assertEqual(s2.next_trial()["index"], first["index"] + 1)
        self.assertEqual(s2.sitting, 2)
        lines = s.path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(validate(json.loads(line)), [])

    def test_defaults_editing_and_marker(self):
        b = Session.create(self.dir, "blind", seed="d1", trials_per_pair=1, prompts=PROMPTS)
        j = Session.create(self.dir, "jnd", seed="d2", reps=1, prompts=PROMPTS)
        self.assertFalse(b.config["allow_editing"])  # R1 backspace-order tell
        self.assertTrue(j.config["allow_editing"])   # R3 vs R3: no tell
        self.assertTrue(b.config["marker_hidden"] and j.config["marker_hidden"])
        self.assertFalse(b.config["allow_no_difference"])
        self.assertEqual(b.config["dataset_size"], "10k")
        b2 = Session.create(self.dir, "blind", seed="d3", trials_per_pair=1, prompts=PROMPTS,
                            allow_editing=True, marker_hidden=False)
        self.assertTrue(b2.config["allow_editing"])
        self.assertFalse(b2.config["marker_hidden"])

    def test_no_difference_disabled_by_default(self):
        s = Session.create(self.dir, "blind", seed="abc", trials_per_pair=1, prompts=PROMPTS)
        t = s.next_trial()
        with self.assertRaises(ValueError):
            s.record(t["index"], {**answer_for(t, True), "no_difference": True})
        s2 = Session.create(self.dir, "blind", seed="abd", trials_per_pair=1, prompts=PROMPTS, allow_no_difference=True)
        t = s2.next_trial()
        rec = s2.record(t["index"], {**answer_for(t, True), "no_difference": True})
        self.assertIsNone(rec["answer"])
        self.assertIsNone(rec["correct"])

    def test_validate_catches_problems(self):
        self.assertIn("missing session_id", validate({"schema": SCHEMA, "record": "trial", "kind": "blind"}))
        self.assertTrue(any("answer" in e for e in validate({"schema": SCHEMA, "record": "trial", "kind": "blind", "answer": 3})))

    def test_staircase_session(self):
        s = Session.create(self.dir, "jnd", seed="st", method="staircase", max_trials=6, prompts=PROMPTS)
        for _ in range(6):
            t = s.next_trial()
            s.record(t["index"], answer_for(t, True))
        self.assertIsNone(s.next_trial())
        self.assertTrue(s.progress()["finished"])
        levels = [s.trials[i]["level_ms"] for i in range(6)]
        self.assertEqual(levels, [100, 100, 100, 67, 67, 67])


def fill_blind(dir_, n_per_pair, correct_by_pair, placebo_int1, seed="rep"):
    s = Session.create(dir_, "blind", seed=seed, trials_per_pair=n_per_pair, prompts=PROMPTS)
    done = {}
    while (t := s.next_trial()) is not None:
        name = t["pair_name"]
        i = done.get(name, 0)
        done[name] = i + 1
        if name == "R3-R3":
            p = answer_for(t, True)
            p["answer"] = 1 if i < placebo_int1 else 2
        else:
            p = answer_for(t, i < correct_by_pair[name])
        s.record(t["index"], p)
    return s


class Report(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_gate_pass(self):
        fill_blind(self.dir, 40, {"R1-R3": 26, "R1-R2": 22, "R2-R3": 20}, placebo_int1=21)
        s = report.build_summary([self.dir], n_boot=0)
        pairs = s["blind"]["by_size"]["10k"]["pairs"]
        r13 = pairs["R1-R3"]
        self.assertEqual((r13["n"], r13["correct"]), (40, 26))
        self.assertAlmostEqual(r13["p_one_sided"], 0.040345, places=6)
        self.assertEqual(r13["ci95"], [0.4832, 0.7937])
        self.assertTrue(r13["significant"])
        self.assertEqual(r13["faster_rung"], "r3")
        self.assertEqual(r13["measured"]["delta_p50_ms_median"], 60.0)
        self.assertFalse(pairs["R1-R2"]["significant"])
        pl = pairs["R3-R3"]
        self.assertTrue(pl["placebo"])
        self.assertEqual(pl["chose_interval_1"], 21)
        self.assertTrue(pl["at_chance"])
        g = s["gate_a_perceptibility"]
        self.assertEqual(g["verdict"], "pass")
        self.assertEqual(g["dataset_size"], "10k")
        self.assertEqual(s["validation_problems"], [])
        self.assertEqual(s["schema"], "ladder.playground.summary/1")
        text = report.render_text(s)
        self.assertIn("PASS", text)

    def test_gate_fail_and_incomplete(self):
        fill_blind(self.dir, 40, {"R1-R3": 25, "R1-R2": 20, "R2-R3": 20}, placebo_int1=20)
        self.assertEqual(report.build_summary([self.dir], n_boot=0)["gate_a_perceptibility"]["verdict"], "fail")
        d2 = Path(tempfile.mkdtemp())
        fill_blind(d2, 40, {"R1-R3": 30, "R1-R2": 20, "R2-R3": 20}, placebo_int1=28)  # placebo biased
        g = report.build_summary([d2], n_boot=0)["gate_a_perceptibility"]
        self.assertEqual(g["verdict"], "fail")
        self.assertFalse(g["placebo_at_chance"]["pass"])
        d3 = Path(tempfile.mkdtemp())
        fill_blind(d3, 20, {"R1-R3": 20, "R1-R2": 10, "R2-R3": 10}, placebo_int1=10)
        g = report.build_summary([d3], n_boot=0)["gate_a_perceptibility"]
        self.assertEqual(g["verdict"], "incomplete")
        self.assertFalse(g["r1_r3_above_chance"]["enough_trials"])

    def test_jnd_threshold_and_delta_check(self):
        fill_blind(self.dir, 40, {"R1-R3": 30, "R1-R2": 20, "R2-R3": 20}, placebo_int1=20)
        s = Session.create(self.dir, "jnd", seed="j", reps=20, prompts=PROMPTS)
        a, b = -3.0, 0.08  # true threshold 37.5 ms
        count = {}
        while (t := s.next_trial()) is not None:
            lv = t["level_ms"]
            i = count.get(lv, 0)
            count[lv] = i + 1
            k = round(20 * (0.5 + 0.5 / (1 + math.exp(-(a + b * lv)))))
            s.record(t["index"], answer_for(t, i < k, lat=(20.0 + lv, 20.0)))
        out = report.build_summary([self.dir], n_boot=100)
        j = out["jnd"]["by_mode"]["defer"]["all"]
        self.assertEqual(j["levels"]["100"]["n"], 20)
        self.assertTrue(j["levels"]["0"]["catch"])
        self.assertEqual(j["levels"]["50"]["measured_delta_p50_ms_median"], 50.0)
        self.assertAlmostEqual(j["fit"]["threshold_75_ms"], 37.5, delta=4)
        self.assertAlmostEqual(j["fit_no_lapse"]["threshold_75_ms"], 37.5, delta=4)
        self.assertLessEqual(j["fit"]["lapse"], 0.06)
        self.assertEqual(j["fit_no_lapse"]["lapse"], 0.0)
        self.assertIn("ci95", j["fit_no_lapse"])
        lo, hi = j["fit"]["ci95"]
        self.assertLess(lo, j["fit"]["threshold_75_ms"])
        self.assertGreater(hi, j["fit"]["threshold_75_ms"])
        sup = out["gate_a_perceptibility"]["delta_vs_jnd"]
        self.assertEqual(sup["delta_p50_ms"], 60.0)
        self.assertTrue(sup["above"])
        # report writes the JSON file
        path = self.dir / "summary.json"
        with contextlib.redirect_stdout(io.StringIO()) as out_text:
            self.assertEqual(report.main([str(self.dir)], None, 50, 40, None), 0)
        self.assertIn("Gate A perceptibility", out_text.getvalue())
        self.assertEqual(json.loads(path.read_text())["schema"], "ladder.playground.summary/1")
        headers, trials = read_records([self.dir])
        self.assertEqual(len(headers), 2)


if __name__ == "__main__":
    unittest.main()
