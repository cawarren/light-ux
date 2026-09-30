"""Gate A rule (phase-a §7.3; decided 2026-09-30: BOTH conditions are required).

1. Gap (proposal thresholds, §7.3/§8.4): at the headline settings (60 Hz, headline size,
   A.first_key, M sessions, real input), either
     p95(R1)/p95(R3) >= 3 with the CI lower bound >= 2, or
     p95(R1) - p95(R3) >= 2 frames with the CI lower bound >= 1 frame.
2. Perceptibility (hard requirement): R1-R3 blind 2AFC significantly above chance
   (one-sided exact binomial p < 0.05, >= 40 trials) AND the R3-R3 placebo pair at chance.
   Comes from the playground's results file; absent -> "pending".

Accepted perceptibility JSON (the playground is built separately; any of):
  {"pairs": [{"a": "r1-vite", "b": "r3-no-framework", "n": 40, "correct": 29}, ...]}
  {"pairs": [{"pair": ["r1-vite", "r3-no-framework"], "n": 40, "correct": 29, "placebo": false}]}
  {"trials": [{"a": ..., "b": ..., "correct": true}, ...]}   (or a top-level list of those)
  optional "jnd_ms": the owner's 75% threshold from §5.3 (informational here).
A pair is R1-R3 when one side starts with "r1" and the other with "r3"; placebo when both
sides are the same rung (or "placebo": true).
"""
from __future__ import annotations

import json

from .stats import binom_sf

RATIO_MIN, RATIO_LO_MIN = 3.0, 2.0
FRAMES_MIN, FRAMES_LO_MIN = 2.0, 1.0
BLIND_MIN_N, ALPHA = 40, 0.05


def gap_verdict(cmp):
    """cmp: stats.compare(R1, R3, qi=1, frame_ms) output."""
    if not cmp or cmp.get("ratio") is None:
        return {"pass": None, "reason": "no data"}
    r, (rlo, _) = cmp["ratio"], cmp["ratio_ci"]
    f = cmp.get("diff_frames")
    flo = (cmp.get("diff_frames_ci") or (None, None))[0]
    by_ratio = r is not None and rlo is not None and r >= RATIO_MIN and rlo >= RATIO_LO_MIN
    by_frames = f is not None and flo is not None and f >= FRAMES_MIN and flo >= FRAMES_LO_MIN
    return {"pass": bool(by_ratio or by_frames), "by_ratio": by_ratio, "by_frames": by_frames,
            "reason": "ratio %s (lo %s), frames %s (lo %s)" % (_fmt(r), _fmt(rlo), _fmt(f), _fmt(flo))}


def _fmt(v):
    return "-" if v is None else "%.2f" % v


def _pairs(obj):
    trials = obj if isinstance(obj, list) else obj.get("trials") or obj.get("judgements")
    pairs = {}
    if trials:
        for t in trials:
            a, b = t.get("a"), t.get("b")
            if (a is None or b is None) and t.get("pair"):
                a, b = t["pair"][0], t["pair"][1]
            if t.get("correct") is None:
                continue                      # "no difference" answers are analysed separately (§5.2)
            key = tuple(sorted((a, b)))
            p = pairs.setdefault(key, {"a": key[0], "b": key[1], "n": 0, "correct": 0, "placebo": bool(t.get("placebo"))})
            p["n"] += 1
            p["correct"] += 1 if t["correct"] else 0
    for p in (obj.get("pairs") if isinstance(obj, dict) else None) or []:
        a, b = p.get("a"), p.get("b")
        if (a is None or b is None) and p.get("pair"):
            a, b = p["pair"][0], p["pair"][1]
        key = tuple(sorted((a, b)))
        pairs[key] = {"a": key[0], "b": key[1], "n": int(p["n"]), "correct": int(p["correct"]),
                      "placebo": bool(p.get("placebo"))}
    return list(pairs.values())


def perceptibility(path):
    if not path:
        return {"status": "pending", "reason": "no playground results given (--perceptibility)"}
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, ValueError) as e:
        return {"status": "pending", "reason": "cannot read %s: %s" % (path, e)}
    pairs = _pairs(obj)
    for p in pairs:
        p["p_value"] = binom_sf(p["correct"], p["n"]) if p["n"] else None
        p["pct"] = p["correct"] / p["n"] if p["n"] else None
    main = [p for p in pairs if {p["a"][:2], p["b"][:2]} == {"r1", "r3"}]
    placebo = [p for p in pairs if p["placebo"] or p["a"] == p["b"]]
    out = {"pairs": pairs, "jnd_ms": obj.get("jnd_ms") if isinstance(obj, dict) else None}
    if not main:
        out.update(status="pending", reason="no R1-R3 pair in the results")
        return out
    m = max(main, key=lambda p: p["n"])
    if m["n"] < BLIND_MIN_N:
        out.update(status="pending", reason="R1-R3 has %d < %d trials" % (m["n"], BLIND_MIN_N))
        return out
    main_ok = m["p_value"] < ALPHA
    if not placebo:
        out.update(status="pending" if main_ok else "fail",
                   reason="R1-R3 p=%.4g; placebo pair (R3-R3) not run" % m["p_value"])
        return out
    pl = max(placebo, key=lambda p: p["n"])
    placebo_ok = pl["p_value"] >= ALPHA
    out.update(status="pass" if (main_ok and placebo_ok) else "fail",
               reason="R1-R3 %d/%d p=%.4g; placebo %d/%d p=%.4g%s" % (
                   m["correct"], m["n"], m["p_value"], pl["correct"], pl["n"], pl["p_value"],
                   "" if placebo_ok else " (placebo above chance: leakage)"))
    return out


def gate_a(gap, perc, simulated=False, valid_settings=True):
    """Overall verdict. PASS needs both conditions; simulated data can never pass."""
    if simulated:
        return "NOT VALID (simulated)"
    if not valid_settings:
        return "NOT AT HEADLINE SETTINGS"
    g, p = gap.get("pass"), perc.get("status")
    if g is False or p == "fail":
        return "FAIL"
    if g is None:
        return "NO DATA"
    if p == "pass":
        return "PASS"
    return "PENDING (gap passes; perceptibility %s)" % p
