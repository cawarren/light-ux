"""Binomial, CI, d′, quantile and psychometric-fit maths against known values."""
import math
import unittest
from fractions import Fraction

from . import _path  # noqa: F401
from playlib import stats


def exact_sf(k, n):
    return float(sum(Fraction(math.comb(n, i), 2 ** n) for i in range(k, n + 1)))


class Binomial(unittest.TestCase):
    def test_one_sided_known_values(self):
        self.assertEqual(stats.binom_sf(8, 10), 56 / 1024)
        # R: binom.test(26, 40, alternative = "greater")$p.value = 0.04035
        self.assertAlmostEqual(stats.binom_test_greater(26, 40), 0.0403452338759962, places=12)
        self.assertAlmostEqual(stats.binom_test_greater(25, 40), 0.0769299720814160, places=12)
        for k in range(0, 41):
            self.assertAlmostEqual(stats.binom_sf(k, 40), exact_sf(k, 40), places=14)
        # General p: P(X ≥ 3 | n = 10, p = 0.2) = 0.3222004736
        self.assertAlmostEqual(stats.binom_test_greater(3, 10, 0.2), 0.3222004736, places=9)

    def test_40_trials_critical_value(self):
        """With 40 trials the one-sided test first rejects at 26 correct (65%)."""
        crit = min(k for k in range(41) if stats.binom_test_greater(k, 40) < 0.05)
        self.assertEqual(crit, 26)

    def test_two_sided(self):
        self.assertAlmostEqual(stats.binom_test_two_sided(7, 10), 0.34375, places=12)
        self.assertAlmostEqual(stats.binom_test_two_sided(26, 40), 2 * 0.0403452338759962, places=12)
        self.assertEqual(stats.binom_test_two_sided(20, 40), 1.0)
        self.assertAlmostEqual(stats.binom_test_two_sided(14, 40), stats.binom_test_two_sided(26, 40), places=12)

    def test_clopper_pearson(self):
        lo, hi = stats.clopper_pearson(7, 10)
        self.assertAlmostEqual(lo, 0.3475471, places=6)
        self.assertAlmostEqual(hi, 0.9332605, places=6)
        self.assertEqual(stats.clopper_pearson(0, 10)[0], 0.0)
        self.assertAlmostEqual(stats.clopper_pearson(0, 10)[1], 0.3084971, places=6)
        self.assertAlmostEqual(stats.clopper_pearson(10, 10)[0], 0.6915029, places=6)
        lo, hi = stats.clopper_pearson(26, 40)  # R: 0.4831555 0.7937175
        self.assertAlmostEqual(lo, 0.4831555, places=6)
        self.assertAlmostEqual(hi, 0.7937175, places=6)
        # Defining identities.
        self.assertAlmostEqual(stats.binom_sf(26, 40, lo), 0.025, places=9)
        self.assertAlmostEqual(stats.binom_cdf(26, 40, hi), 0.025, places=9)

    def test_dprime(self):
        d, corrected = stats.dprime_2afc(76, 100)  # √2 · Φ⁻¹(0.76) = 0.99886
        self.assertAlmostEqual(d, 0.998863, places=5)
        self.assertFalse(corrected)
        self.assertAlmostEqual(stats.dprime_2afc(20, 40)[0], 0.0, places=12)
        d, corrected = stats.dprime_2afc(40, 40)
        self.assertTrue(corrected)
        self.assertAlmostEqual(d, math.sqrt(2) * stats._N.inv_cdf(1 - 1 / 80), places=12)


class Descriptive(unittest.TestCase):
    def test_quantile_type7(self):
        self.assertEqual(stats.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(stats.quantile(range(1, 11), 0.95), 9.55)
        self.assertIsNone(stats.quantile([], 0.5))
        self.assertEqual(stats.median([3, None, 1, 2]), 2)


class Psychometric(unittest.TestCase):
    def test_recovers_exact_parameters(self):
        a, b = -3.0, 0.08  # threshold at 75% = -a/b = 37.5 ms
        xs = [8, 17, 25, 33, 50, 67, 100]
        ps = [0.5 + 0.5 / (1 + math.exp(-(a + b * x))) for x in xs]
        fit = stats.fit_2afc_logistic(xs, ps, [20] * len(xs))
        self.assertTrue(fit["converged"])
        self.assertAlmostEqual(fit["a"], a, places=6)
        self.assertAlmostEqual(fit["b"], b, places=8)
        self.assertAlmostEqual(fit["threshold"], 37.5, places=6)

    def test_binary_trials_and_bootstrap(self):
        # 20 trials per level with the correct counts of the model above, rounded.
        a, b = -3.0, 0.08
        outcomes = {}
        xs, ys = [], []
        for x in (8, 17, 25, 33, 50, 67, 100):
            k = round(20 * (0.5 + 0.5 / (1 + math.exp(-(a + b * x)))))
            o = [1] * k + [0] * (20 - k)
            outcomes[x] = o
            xs += [x] * 20
            ys += o
        fit = stats.fit_2afc_logistic(xs, ys)
        self.assertTrue(fit["converged"])
        self.assertAlmostEqual(fit["threshold"], 37.5, delta=4)
        boot = stats.bootstrap_threshold(outcomes, n_boot=200, seed="t")
        lo, hi = boot["ci95"]
        self.assertLess(lo, fit["threshold"])
        self.assertGreater(hi, fit["threshold"])
        self.assertEqual(boot, stats.bootstrap_threshold(outcomes, n_boot=200, seed="t"))  # seeded

    def test_degenerate(self):
        self.assertIsNone(stats.fit_2afc_logistic([10, 10], [1, 0])["threshold"])
        flat = stats.fit_2afc_logistic([10, 20, 30] * 10, [1, 0] * 15)
        self.assertTrue(flat["threshold"] is None or flat["threshold"] > 30 or flat["threshold"] < 10)

    def test_lapse_fit_recovers_parameters(self):
        xs = [8, 17, 25, 33, 50, 67, 100]
        for lam in (0.0, 0.021, 0.045):
            a, b = -3.0, 0.08
            ps = [0.5 + (0.5 - lam) / (1 + math.exp(-(a + b * x))) for x in xs]
            fit = stats.fit_2afc_lapse(xs, ps, [40] * len(xs))
            self.assertAlmostEqual(fit["lapse"], lam, places=3)
            f_t = 0.25 / (0.5 - lam)
            expected = (math.log(f_t / (1 - f_t)) - a) / b  # x where p = 0.75
            self.assertAlmostEqual(fit["threshold"], expected, delta=0.3)
        # Lapses bias the lapse-free fit: threshold too high.
        ps = [0.5 + 0.47 / (1 + math.exp(-(-3.0 + 0.08 * x))) for x in xs]
        self.assertGreater(stats.fit_2afc_logistic(xs, ps, [40] * 7)["threshold"],
                           stats.fit_2afc_lapse(xs, ps, [40] * 7)["threshold"])

    def test_lapse_bounded(self):
        xs = [8, 17, 25, 33, 50, 67, 100]
        ps = [0.5 + 0.35 / (1 + math.exp(-(-3.0 + 0.08 * x))) for x in xs]  # true λ = 0.15
        fit = stats.fit_2afc_lapse(xs, ps, [40] * 7)
        self.assertLessEqual(fit["lapse"], stats.MAX_LAPSE + 1e-9)
        self.assertTrue(fit["lapse_at_bound"])

    def test_aggregate_same_likelihood(self):
        xs = [10, 10, 20, 20, 20, 40, 40]
        ys = [1, 0, 1, 1, 0, 1, 1]
        lv, ps, ws = stats.aggregate(xs, ys)
        self.assertEqual((lv, ws), ([10, 20, 40], [2, 3, 2]))
        f1 = stats.fit_2afc_logistic(xs, ys)
        f2 = stats.fit_2afc_logistic(lv, ps, ws)
        self.assertAlmostEqual(f1["loglik"], f2["loglik"], places=9)

    def test_staircase_estimate(self):
        seq = [100, 67, 50, 33, 50, 33, 25, 33, 25, 33]
        est = stats.staircase_estimate(seq, k_last=4)
        self.assertEqual(est["reversals"], [33, 50, 25, 33, 25])
        self.assertAlmostEqual(est["threshold"], (50 + 25 + 33 + 25) / 4)


if __name__ == "__main__":
    unittest.main()
