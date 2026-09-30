"""Statistics for Phase A (phase-a §4.7, reusing 03 §5.1 / §5.6). Standard library only.

- Quantiles: Hyndman-Fan type 7 (numpy "linear"). +inf is allowed (a censored trial: no
  presentation within the timeout) and propagates, so a p99 above the censored fraction reads
  as "> timeout" instead of being silently optimistic.
- CIs: bootstrap, default 10,000 resamples, BCa for p50/p95 and percentile for p99 (03 §5.1).
  If the lag-1..10 autocorrelation is significant (Ljung-Box, alpha 0.05) the resampling
  switches to a moving-block bootstrap (block length ceil(sqrt(n))) with percentile CIs, and
  says so. BCa also falls back to percentile when censored (inf) values are present.
- Ratios and differences between arms (R1 vs R3 etc.): independent bootstrap of both arms,
  paired by resample index, percentile CI. A difference of quantiles is not a quantile of
  differences (03 §5.6).
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist

_N = NormalDist()
LJUNG_BOX_CRIT_10 = 18.307   # chi-square 0.95 quantile, 10 degrees of freedom
QS = (0.5, 0.95, 0.99)


def quantile_sorted(s, q):
    n = len(s)
    if n == 0:
        return None
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    a, b = s[lo], s[hi]
    if a == b or h == lo:
        return a
    if math.isinf(b):
        return b
    return a + (h - lo) * (b - a)


def quantile(xs, q):
    return quantile_sorted(sorted(xs), q)


def acf(xs, maxlag=10):
    fin = [x for x in xs if math.isfinite(x)]
    n = len(fin)
    if n < maxlag + 3:
        return []
    m = sum(fin) / n
    d = [x - m for x in fin]
    c0 = sum(v * v for v in d)
    if c0 == 0:
        return [0.0] * maxlag
    return [sum(d[i] * d[i + k] for i in range(n - k)) / c0 for k in range(1, maxlag + 1)]


def ljung_box(xs, h=10):
    """(Q, significant at 0.05) for lags 1..h."""
    r = acf(xs, h)
    n = len([x for x in xs if math.isfinite(x)])
    if not r:
        return None, False
    q = n * (n + 2) * sum(rk * rk / (n - k) for k, rk in enumerate(r, start=1))
    return q, q > LJUNG_BOX_CRIT_10


def resample_indices(n, rng, block_len=None):
    if not block_len or block_len <= 1:
        return [int(rng.random() * n) for _ in range(n)]
    out = []
    starts = n - block_len + 1
    while len(out) < n:
        s = int(rng.random() * starts)
        out.extend(range(s, s + block_len))
    return out[:n]


def bootstrap(xs, qs=QS, B=10000, seed=0, block_len=None):
    """B x len(qs) matrix (list of tuples) of resampled quantiles."""
    rng = random.Random(seed)
    n = len(xs)
    out = []
    if not block_len:
        choices = rng.choices
        for _ in range(B):
            s = sorted(choices(xs, k=n))
            out.append(tuple(quantile_sorted(s, q) for q in qs))
    else:
        for _ in range(B):
            s = sorted(xs[i] for i in resample_indices(n, rng, block_len))
            out.append(tuple(quantile_sorted(s, q) for q in qs))
    return out


def percentile_ci(vals, alpha=0.05):
    s = sorted(vals)
    return quantile_sorted(s, alpha / 2), quantile_sorted(s, 1 - alpha / 2)


def jackknife_quantiles(sorted_xs, q):
    """Leave-one-out type-7 quantiles, O(1) each on the sorted sample."""
    s = sorted_xs
    n = len(s)
    h = (n - 2) * q
    lo = int(math.floor(h))
    frac = h - lo
    out = []
    for i in range(n):
        def at(j):
            return s[j] if j < i else s[j + 1]
        a = at(lo)
        b = at(min(lo + 1, n - 2))
        out.append(a + frac * (b - a))
    return out


def bca_ci(xs_sorted, q, theta, boot_vals, alpha=0.05):
    B = len(boot_vals)
    less = sum(1 for v in boot_vals if v < theta)
    eq = sum(1 for v in boot_vals if v == theta)
    p0 = (less + 0.5 * eq) / B
    p0 = min(max(p0, 1.0 / (B + 1)), 1 - 1.0 / (B + 1))
    z0 = _N.inv_cdf(p0)
    jk = jackknife_quantiles(xs_sorted, q)
    m = sum(jk) / len(jk)
    num = sum((m - v) ** 3 for v in jk)
    den = 6.0 * (sum((m - v) ** 2 for v in jk) ** 1.5)
    a = num / den if den else 0.0
    s = sorted(boot_vals)
    out = []
    for z in (_N.inv_cdf(alpha / 2), _N.inv_cdf(1 - alpha / 2)):
        adj = _N.cdf(z0 + (z0 + z) / (1 - a * (z0 + z)))
        out.append(quantile_sorted(s, adj))
    return tuple(out)


def summarize(xs, B=10000, seed=0, alpha=0.05):
    """n, p50/p95/p99 with CIs, the method used, and the bootstrap matrix (for ratios)."""
    xs = list(xs)
    n = len(xs)
    if n == 0:
        return {"n": 0}
    s = sorted(xs)
    est = [quantile_sorted(s, q) for q in QS]
    qlb, sig = ljung_box(xs)
    block_len = int(math.ceil(math.sqrt(n))) if sig else None
    censored = sum(1 for x in xs if math.isinf(x))
    boot = bootstrap(xs, QS, B, seed, block_len) if n >= 2 else [tuple(est)] * 2
    cis, methods = [], []
    for k, q in enumerate(QS):
        col = [b[k] for b in boot]
        use_bca = q < 0.99 and not block_len and not censored and n >= 10
        if use_bca:
            try:
                cis.append(bca_ci(s, q, est[k], col, alpha))
                methods.append("bca")
                continue
            except (ValueError, ZeroDivisionError, OverflowError):
                pass
        cis.append(percentile_ci(col, alpha))
        methods.append("percentile" + ("-mbb" if block_len else ""))
    fin = [x for x in xs if math.isfinite(x)]
    return {"n": n, "censored": censored, "p50": est[0], "p95": est[1], "p99": est[2],
            "p50_ci": cis[0], "p95_ci": cis[1], "p99_ci": cis[2], "ci_methods": methods,
            "ljung_box_q": qlb, "autocorrelated": sig, "block_len": block_len, "B": len(boot),
            "mean_finite": sum(fin) / len(fin) if fin else None, "boot": boot}


def _div(a, b):
    if a is None or b is None or (math.isinf(a) and math.isinf(b)):
        return None
    if b == 0:
        return math.inf if a > 0 else None
    return a / b


def compare(sa, sb, qi=1, frame_ms=None, alpha=0.05):
    """Arm A vs arm B at quantile index qi (1 = p95): ratio A/B, difference A-B (ms, frames)."""
    if not sa.get("n") or not sb.get("n"):
        return None
    qa, qb = sa[("p50", "p95", "p99")[qi]], sb[("p50", "p95", "p99")[qi]]
    ra = [b[qi] for b in sa["boot"]]
    rb = [b[qi] for b in sb["boot"]]
    m = min(len(ra), len(rb))
    ratios = [_div(ra[i], rb[i]) for i in range(m)]
    ratios = [r for r in ratios if r is not None]
    diffs = [ra[i] - rb[i] for i in range(m) if not (math.isinf(ra[i]) and math.isinf(rb[i]))]
    out = {"ratio": _div(qa, qb), "ratio_ci": percentile_ci(ratios, alpha) if ratios else (None, None),
           "diff_ms": (qa - qb) if not (math.isinf(qa) and math.isinf(qb)) else None,
           "diff_ci": percentile_ci(diffs, alpha) if diffs else (None, None)}
    if frame_ms:
        out["diff_frames"] = None if out["diff_ms"] is None else out["diff_ms"] / frame_ms
        out["diff_frames_ci"] = tuple(None if v is None else v / frame_ms for v in out["diff_ci"])
    return out


def ecdf(xs):
    s = sorted(x for x in xs if math.isfinite(x))
    n = len(xs)
    return [(v, (i + 1) / n) for i, v in enumerate(s)]


def binom_sf(k, n, p=0.5):
    """P(X >= k), X ~ Binomial(n, p): the one-sided exact test for 2AFC above chance."""
    if k <= 0:
        return 1.0
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
