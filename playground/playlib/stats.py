"""Statistics for 2AFC judgements, stdlib only (docs/phase-a/README.md §5.2, §5.3, §7.3).

- Exact binomial tests (one-sided "greater" for pairs with a known faster rung, two-sided for
  the placebo), Clopper–Pearson 95% intervals, d′ for 2AFC.
- A 2AFC psychometric function p(x) = γ + (1 − γ − λ)·σ(a + b·x), guess rate γ = 0.5 fixed, fitted
  by maximum likelihood (Fisher scoring with step halving for a, b); the lapse rate λ is either fixed
  at 0 or free within [0, 0.06] (profile likelihood: grid, then golden-section search). The threshold
  is the x where p = 0.75.
- Percentile bootstrap of the threshold (resampling trials within stimulus levels).
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist

_N = NormalDist()


# ----------------------------------------------------------------------------- binomial
def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def binom_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p == 0.5:
        return math.comb(n, k) / 2 ** n  # exact big-int ratio, correctly rounded
    return math.exp(_log_pmf(k, n, p))


def binom_sf(k: int, n: int, p: float = 0.5) -> float:
    """P(X ≥ k), X ~ Bin(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p == 0.5:
        return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, math.fsum(binom_pmf(i, n, p) for i in range(k, n + 1)))


def binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    """P(X ≤ k)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.5:
        return sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, math.fsum(binom_pmf(i, n, p) for i in range(0, k + 1)))


def binom_test_greater(k: int, n: int, p: float = 0.5) -> float:
    """One-sided exact binomial p-value for H1: rate > p."""
    return binom_sf(k, n, p) if n > 0 else 1.0


def binom_test_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Two-sided exact binomial p-value (sum of outcomes no more likely than k, as scipy)."""
    if n == 0:
        return 1.0
    d = binom_pmf(k, n, p)
    rel = 1 + 1e-7
    return min(1.0, math.fsum(binom_pmf(i, n, p) for i in range(n + 1) if binom_pmf(i, n, p) <= d * rel))


def _bisect(f, lo: float, hi: float, iters: int = 200) -> float:
    """Root of an increasing function f on [lo, hi]."""
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Clopper–Pearson) two-sided 1 − alpha interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    a = alpha / 2
    lo = 0.0 if k == 0 else _bisect(lambda p: binom_sf(k, n, p) - a, 0.0, 1.0)
    hi = 1.0 if k == n else _bisect(lambda p: a - binom_cdf(k, n, p), 0.0, 1.0)
    return (lo, hi)


def dprime_2afc(k: int, n: int) -> tuple[float | None, bool]:
    """d′ = √2 · Φ⁻¹(Pc) for 2AFC. Rates of 0 or 1 are moved by 1/(2n); returns (d′, corrected)."""
    if n == 0:
        return (None, False)
    pc = k / n
    corrected = False
    if pc <= 0 or pc >= 1:
        pc = min(max(pc, 1 / (2 * n)), 1 - 1 / (2 * n))
        corrected = True
    return (math.sqrt(2) * _N.inv_cdf(pc), corrected)


# ----------------------------------------------------------------------------- descriptive
def quantile(xs, q: float) -> float | None:
    """Hyndman–Fan type 7 (the default of R and numpy), as the harness uses (§4.7)."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def median(xs) -> float | None:
    return quantile(xs, 0.5)


# ----------------------------------------------------------------------------- psychometric fit
def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def fit_2afc_logistic(xs, ys, weights=None, gamma: float = 0.5, lapse: float = 0.0,
                      target: float = 0.75, max_iter: int = 200) -> dict:
    """ML fit of p(x) = γ + (1 − γ − λ)·σ(a + b·x) to binary (or proportion + weight) data.

    xs: stimulus (ms); ys: 1 correct / 0 wrong, or a proportion when weights are counts.
    Returns {a, b, threshold, slope_at_threshold, converged, loglik, n, reason}. threshold is the
    x with p(x) = target (75% by default); None when the fit is degenerate (b ≤ 0 or diverging).
    """
    xs = [float(x) for x in xs]
    ys = [float(y) for y in ys]
    ws = [1.0] * len(xs) if weights is None else [float(w) for w in weights]
    n = sum(ws)
    out = {"a": None, "b": None, "threshold": None, "converged": False, "loglik": None,
           "n": n, "gamma": gamma, "lapse": lapse, "target": target, "reason": None}
    if len(set(xs)) < 2 or n == 0:
        out["reason"] = "need at least two stimulus levels"
        return out
    span = 1 - gamma - lapse
    eps = 1e-12

    def loglik(a, b):
        s = 0.0
        for x, y, w in zip(xs, ys, ws):
            p = gamma + span * _sigmoid(a + b * x)
            p = min(max(p, eps), 1 - eps)
            s += w * (y * math.log(p) + (1 - y) * math.log(1 - p))
        return s

    xm = sum(w * x for x, w in zip(xs, ws)) / n
    xr = (max(xs) - min(xs)) or 1.0
    b = 4.0 / xr
    a = -b * xm
    ll = loglik(a, b)
    b_cap = 1e3 / xr  # beyond this the psychometric function is a step: separation
    for _ in range(max_iter):
        u0 = u1 = i00 = i01 = i11 = 0.0
        for x, y, w in zip(xs, ys, ws):
            f = _sigmoid(a + b * x)
            p = min(max(gamma + span * f, eps), 1 - eps)
            dp = span * f * (1 - f)
            g = w * (y - p) / (p * (1 - p)) * dp
            h = w * dp * dp / (p * (1 - p))
            u0 += g
            u1 += g * x
            i00 += h
            i01 += h * x
            i11 += h * x * x
        det = i00 * i11 - i01 * i01
        if det <= 1e-300:
            out["reason"] = "singular information matrix"
            break
        da = (i11 * u0 - i01 * u1) / det
        db = (-i01 * u0 + i00 * u1) / det
        step = 1.0
        while step > 1e-8:
            na, nb = a + step * da, b + step * db
            nll = loglik(na, nb)
            if nll >= ll - 1e-12:
                break
            step /= 2
        else:
            out["reason"] = "line search failed"
            break
        a, b, ll_old, ll = na, nb, ll, nll
        if abs(b) > b_cap:
            out["reason"] = "separated data (step-like psychometric function)"
            break
        if abs(step * da) < 1e-9 * (1 + abs(a)) and abs(step * db) < 1e-9 * (1 + abs(b)) or abs(ll - ll_old) < 1e-12:
            out["converged"] = True
            break
    out.update(a=a, b=b, loglik=ll)
    if out["converged"] and b > 0:
        f_t = (target - gamma) / span
        if 0 < f_t < 1:
            out["threshold"] = (math.log(f_t / (1 - f_t)) - a) / b
            out["slope_at_threshold"] = span * f_t * (1 - f_t) * b
    elif out["converged"] and b <= 0:
        out["reason"] = "non-increasing psychometric function (b ≤ 0)"
    return out


MAX_LAPSE = 0.06


def aggregate(xs, ys):
    """Binary trials → (levels, proportions, counts): the same likelihood, far fewer terms."""
    acc = {}
    for x, y in zip(xs, ys):
        k, n = acc.get(x, (0.0, 0))
        acc[x] = (k + y, n + 1)
    lv = sorted(acc)
    return lv, [acc[x][0] / acc[x][1] for x in lv], [acc[x][1] for x in lv]


def fit_2afc_lapse(xs, ys, weights=None, max_lapse: float = MAX_LAPSE, gamma: float = 0.5,
                   target: float = 0.75) -> dict:
    """ML fit with a free lapse rate λ ∈ [0, max_lapse] (profile likelihood over λ)."""
    if weights is None:
        xs, ys, weights = aggregate(xs, ys)

    def at(lam):
        return fit_2afc_logistic(xs, ys, weights, gamma=gamma, lapse=lam, target=target)

    grid = [max_lapse * i / 12 for i in range(13)]
    fits = [(lam, at(lam)) for lam in grid]
    ok = [(lam, f) for lam, f in fits if f["loglik"] is not None and f["converged"]]
    if not ok:
        best = fits[0][1]
        best["lapse_bounded"] = False
        return best
    i = max(range(len(ok)), key=lambda j: ok[j][1]["loglik"])
    lo = ok[max(0, i - 1)][0]
    hi = ok[min(len(ok) - 1, i + 1)][0]
    best_lam, best = ok[i]
    # Golden-section refinement of the profile log-likelihood on [lo, hi].
    g = (math.sqrt(5) - 1) / 2
    c, d = hi - g * (hi - lo), lo + g * (hi - lo)
    fc, fd = at(c), at(d)
    for _ in range(25):
        if hi - lo < 1e-5:
            break
        if (fc["loglik"] or -math.inf) >= (fd["loglik"] or -math.inf):
            hi, d, fd = d, c, fc
            c = hi - g * (hi - lo)
            fc = at(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + g * (hi - lo)
            fd = at(d)
    for lam, f in ((c, fc), (d, fd)):
        if f["converged"] and f["loglik"] is not None and f["loglik"] > best["loglik"]:
            best_lam, best = lam, f
    best["lapse"] = best_lam
    best["lapse_at_bound"] = best_lam >= max_lapse - 1e-4
    best["max_lapse"] = max_lapse
    return best


def bootstrap_threshold(levels_to_outcomes: dict, n_boot: int = 1000, seed: str = "jnd",
                        free_lapse: bool = False, **fit_kw) -> dict:
    """Percentile 95% CI of the fitted threshold, resampling trials within each level."""
    rng = random.Random(seed)
    thresholds = []
    failed = 0
    items = [(x, list(v)) for x, v in sorted(levels_to_outcomes.items()) if v]
    for _ in range(n_boot):
        xs, ps, ws = [], [], []
        for x, outcomes in items:
            m = len(outcomes)
            k = sum(outcomes[rng.randrange(m)] for _ in range(m))
            xs.append(x)
            ps.append(k / m)
            ws.append(m)
        fit = fit_2afc_lapse(xs, ps, ws, **fit_kw) if free_lapse else fit_2afc_logistic(xs, ps, ws, **fit_kw)
        if fit["threshold"] is None:
            failed += 1
        else:
            thresholds.append(fit["threshold"])
    return {"n_boot": n_boot, "n_failed": failed,
            "ci95": [quantile(thresholds, 0.025), quantile(thresholds, 0.975)] if thresholds else None}


def staircase_estimate(levels_seq, k_last: int = 6) -> dict:
    """Threshold from an adaptive staircase: mean of the last k reversal levels."""
    reversals = []
    direction = 0
    for i in range(1, len(levels_seq)):
        d = (levels_seq[i] > levels_seq[i - 1]) - (levels_seq[i] < levels_seq[i - 1])
        if d != 0:
            if direction != 0 and d != direction:
                reversals.append(levels_seq[i - 1])
            direction = d
    last = reversals[-k_last:]
    return {"reversals": reversals, "n_reversals": len(reversals),
            "threshold": sum(last) / len(last) if last else None, "k_used": len(last)}
