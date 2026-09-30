import threading
import time
import unittest

import _util  # noqa: F401
from ladder.soft.runner import clock, injector


class FakePage:
    """Backend that 'settles' each key 30 ms after its key-down (and sends a stale notice)."""

    def __init__(self, lines, settle_ms=30, drop_after=None):
        self.lines, self.settle_ms, self.n, self.log = lines, settle_ms, 0, []
        self.drop_after = drop_after

    def key(self, k, down):
        self.log.append((k, down, clock.now_ns()))
        if not down:
            return
        self.n += 1
        n = self.n
        if self.drop_after is not None and n > self.drop_after:
            return
        def later():
            time.sleep(self.settle_ms / 1000)
            self.lines.put("settled %d %d %d" % (clock.now_ns(), n - 1, 99))   # stale count, must be ignored
            self.lines.put("settled %d %d %d" % (clock.now_ns(), n, n))
        threading.Thread(target=later, daemon=True).start()


def keys():
    return [
        {"i": 0, "key": "a", "after": "settle", "gap_ms": 50.0, "hold_ms": 20.0},
        {"i": 1, "key": "Backspace", "after": "settle", "gap_ms": 60.0, "hold_ms": 20.0},
        {"i": 2, "key": "b", "after": "settle", "gap_ms": 50.0, "hold_ms": 20.0},
        {"i": 3, "key": "c", "after": "prev_down", "gap_ms": 95.0, "hold_ms": 20.0},
    ]


class ExecutorTest(unittest.TestCase):
    def test_settle_and_cadence(self):
        lines = injector.QueueLines()
        page = FakePage(lines)
        res = injector.Executor(page, lines).run(keys(), lead_in_ms=10, settle_timeout_ms=1000)
        r = res["records"]
        self.assertFalse(res["aborted"])
        self.assertEqual([x["ref"] for x in r], ["lead_in", "settle", "settle", "prev_down"])
        # gap counted from the settle notice time, not from the key
        self.assertGreaterEqual(r[1]["t_down_ns"] - r[1]["t_ref_ns"], 60e6)
        self.assertEqual(r[1]["settle_flip_seq"], 1)
        # cadence counted from the previous key-down
        self.assertAlmostEqual((r[3]["t_down_ns"] - r[2]["t_down_ns"]) / 1e6, 95.0, delta=5.0)
        for x in r:
            self.assertGreaterEqual(x["t_up_ns"] - x["t_down_ns"], 20e6)
            self.assertLess(abs(x["t_down_ns"] - x["planned_down_ns"]), 5e6)
        self.assertEqual(res["final"]["ref"], "settle")

    def test_settle_timeout(self):
        lines = injector.QueueLines()
        page = FakePage(lines, drop_after=1)
        t0 = time.monotonic()
        res = injector.Executor(page, lines).run(keys()[:3], lead_in_ms=5, settle_timeout_ms=150, final_settle=True)
        self.assertEqual([x["ref"] for x in res["records"]], ["lead_in", "settle", "settle_timeout"])
        self.assertEqual(res["final"]["ref"], "settle_timeout")
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_stop_aborts(self):
        lines = injector.QueueLines()
        page = FakePage(lines, drop_after=0)
        threading.Timer(0.1, lambda: lines.put("stop")).start()
        res = injector.Executor(page, lines).run(keys(), lead_in_ms=5, settle_timeout_ms=5000)
        self.assertTrue(res["aborted"])
        self.assertEqual(len(res["records"]), 1)
        self.assertEqual(sum(1 for k, down, _ in page.log if down), 1)

    def test_jitter_selftest_shape(self):
        j = injector.jitter_selftest(50)
        self.assertEqual(j["n"], 50)
        self.assertGreaterEqual(j["p99_us"], j["p50_us"])


if __name__ == "__main__":
    unittest.main()
