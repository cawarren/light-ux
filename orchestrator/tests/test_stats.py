import math
import random
import unittest

import _util  # noqa: F401
from ladder.soft.analysis import stats


class QuantileTest(unittest.TestCase):
    def test_type7(self):
        xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertAlmostEqual(stats.quantile(xs, 0.5), 5.5)
        self.assertAlmostEqual(stats.quantile(xs, 0.95), 9.55)   # numpy.quantile(..., 0.95)
        self.assertAlmostEqual(stats.quantile(xs, 0.99), 9.91)
        self.assertEqual(stats.quantile([3.0], 0.95), 3.0)

    def test_censored_inf(self):
        xs = [1.0] * 98 + [math.inf] * 2
        self.assertEqual(stats.quantile(xs, 0.5), 1.0)
        self.assertTrue(math.isinf(stats.quantile(xs, 0.99)))

    def test_jackknife_matches_bruteforce(self):
        rng = random.Random(3)
        xs = sorted(rng.random() for _ in range(37))
        jk = stats.jackknife_quantiles(xs, 0.95)
        for i in range(len(xs)):
            self.assertAlmostEqual(jk[i], stats.quantile(xs[:i] + xs[i + 1:], 0.95))


class BootstrapTest(unittest.TestCase):
    def test_ci_covers_true_quantiles(self):
        # Exponential(scale 20 ms) + 10 ms: p50 = 10 + 20 ln 2, p95 = 10 + 20 ln 20
        true50, true95 = 10 + 20 * math.log(2), 10 + 20 * math.log(20)
        cover50 = cover95 = bca = 0
        runs = 40
        for k in range(runs):
            rng = random.Random(100 + k)
            xs = [10 + rng.expovariate(1 / 20) for _ in range(400)]
            s = stats.summarize(xs, B=600, seed=k)
            cover50 += s["p50_ci"][0] <= true50 <= s["p50_ci"][1]
            cover95 += s["p95_ci"][0] <= true95 <= s["p95_ci"][1]
            bca += s["ci_methods"][:2] == ["bca", "bca"]   # Ljung-Box has a 5% false-positive rate
            self.assertLess(s["p50_ci"][0], s["p50"])
            self.assertGreater(s["p50_ci"][1], s["p50"])
        self.assertGreaterEqual(cover50, runs * 0.85)
        self.assertGreaterEqual(cover95, runs * 0.80)
        self.assertGreaterEqual(bca, runs * 0.8)

    def test_deterministic_seed(self):
        xs = [random.Random(1).random() for _ in range(50)]
        self.assertEqual(stats.summarize(xs, B=200, seed=5)["p95_ci"], stats.summarize(xs, B=200, seed=5)["p95_ci"])

    def test_autocorrelation_switches_to_block_bootstrap(self):
        rng = random.Random(9)
        x, xs = 0.0, []
        for _ in range(500):
            x = 0.9 * x + rng.gauss(0, 1)
            xs.append(x + 50)
        q, sig = stats.ljung_box(xs)
        self.assertTrue(sig)
        s = stats.summarize(xs, B=300, seed=1)
        self.assertTrue(s["autocorrelated"])
        self.assertEqual(s["block_len"], 23)
        self.assertTrue(all(m.endswith("-mbb") for m in s["ci_methods"]))
        iid = [rng.gauss(0, 1) for _ in range(500)]
        self.assertFalse(stats.ljung_box(iid)[1])

    def test_compare_ratio_and_frames(self):
        rng = random.Random(4)
        a = [rng.gauss(300, 20) for _ in range(300)]   # "R1"
        b = [rng.gauss(40, 3) for _ in range(300)]     # "R3"
        sa, sb = stats.summarize(a, B=500, seed=1), stats.summarize(b, B=500, seed=2)
        c = stats.compare(sa, sb, qi=1, frame_ms=1000 / 60)
        self.assertAlmostEqual(c["ratio"], sa["p95"] / sb["p95"])
        self.assertLess(c["ratio_ci"][0], c["ratio"])
        self.assertGreater(c["ratio_ci"][1], c["ratio"])
        self.assertAlmostEqual(c["diff_frames"], (sa["p95"] - sb["p95"]) * 60 / 1000)
        self.assertGreater(c["diff_frames_ci"][0], 10)

    def test_binomial(self):
        self.assertAlmostEqual(stats.binom_sf(0, 10), 1.0)
        self.assertAlmostEqual(stats.binom_sf(10, 10), 1 / 1024)
        self.assertAlmostEqual(stats.binom_sf(26, 40), 0.0403, places=4)   # 26/40 is significant
        self.assertGreater(stats.binom_sf(25, 40), 0.05)


if __name__ == "__main__":
    unittest.main()
