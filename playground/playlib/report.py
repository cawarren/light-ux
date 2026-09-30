"""`play.py report`: blind 2AFC and JND summary + Gate A perceptibility verdict (§5, §7.3).

Output schema "ladder.playground.summary/1" is documented in playground/README.md; the harness
report (`ladder soft report`) ingests it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import stats
from .results import now_iso, read_records, validate

SUMMARY_SCHEMA = "ladder.playground.summary/1"
ALPHA = 0.05


def _r(x, nd=4):
    return None if x is None else round(x, nd)


def _rate_block(k: int, n: int) -> dict:
    lo, hi = stats.clopper_pearson(k, n) if n else (None, None)
    return {"n": n, "k": k, "rate": _r(k / n) if n else None, "ci95": [_r(lo), _r(hi)] if n else None}


def _lat(iv) -> float | None:
    return ((iv or {}).get("latency") or {}).get("p50")


def _trial_delta(t) -> float | None:
    """Measured Δp50 for the trial: slower-condition interval minus faster-condition interval.

    Blind: slower = lower rung number. JND: slower = the interval with the added delay.
    Placebo / catch: interval 1 minus interval 2.
    """
    a, b = (_lat(iv) for iv in t["intervals"])
    if a is None or b is None:
        return None
    exp = t.get("expected_faster_interval")
    if exp == 1:
        return b - a
    if exp == 2:
        return a - b
    return a - b


def _used_backspace(t) -> str:
    n = sum(((iv.get("latency") or {}).get("n_backspace") or 0) for iv in t["intervals"])
    return "with_backspace" if n else "without_backspace"


def _by(trials, key):
    g = defaultdict(list)
    for t in trials:
        g[key(t)].append(t)
    return dict(sorted(g.items(), key=lambda kv: str(kv[0])))


# ----------------------------------------------------------------------------- blind
def blind_pair(trials: list[dict]) -> dict:
    forced = [t for t in trials if t["answer"] is not None]
    placebo = forced and forced[0].get("expected_faster_interval") is None
    out = {"n_total": len(trials), "n_no_difference": sum(1 for t in trials if t["no_difference"]),
           "placebo": bool(placebo) or all(t.get("expected_faster_interval") is None for t in trials)}
    deltas = [d for d in (_trial_delta(t) for t in forced) if d is not None]
    lat_by_rung = defaultdict(list)
    for t in forced:
        for iv in t["intervals"]:
            if _lat(iv) is not None:
                lat_by_rung[iv["rung"]].append(_lat(iv))
    out["measured"] = {
        "delta_p50_ms_median": _r(stats.median(deltas), 2),
        "delta_p50_ms_iqr": [_r(stats.quantile(deltas, 0.25), 2), _r(stats.quantile(deltas, 0.75), 2)] if deltas else None,
        "n_with_latency": len(deltas),
        "interval_p50_ms_median_by_rung": {r: _r(stats.median(v), 2) for r, v in sorted(lat_by_rung.items())},
    }
    confs = [t["confidence"] for t in forced if t.get("confidence")]
    out["confidence_mean"] = _r(sum(confs) / len(confs), 3) if confs else None
    out["rt_ms_median"] = _r(stats.median([t["rt_ms"] for t in forced]), 1)
    n = len(forced)
    if out["placebo"]:
        k1 = sum(1 for t in forced if t["answer"] == 1)
        blk = _rate_block(k1, n)
        p2 = stats.binom_test_two_sided(k1, n) if n else None
        out.update({"n": n, "chose_interval_1": k1, "rate_interval_1": blk["rate"], "ci95": blk["ci95"],
                    "p_two_sided": _r(p2, 6), "at_chance": (p2 is not None and p2 >= ALPHA)})
        return out
    k = sum(1 for t in forced if t["correct"])
    blk = _rate_block(k, n)
    p1 = stats.binom_test_greater(k, n) if n else None
    d, corrected = stats.dprime_2afc(k, n)
    faster = {t["rungs"][t["expected_faster_interval"] - 1] for t in forced}
    out.update({"n": n, "correct": k, "rate": blk["rate"], "ci95": blk["ci95"], "p_one_sided": _r(p1, 6),
                "significant": p1 is not None and p1 < ALPHA, "d_prime": _r(d, 3), "d_prime_corrected": corrected,
                "faster_rung": sorted(faster)[0] if len(faster) == 1 else sorted(faster)})
    out["by_order"] = {o: _rate_block(sum(1 for t in g if t["correct"]), len(g))
                       for o, g in _by(forced, lambda t: t["order"]).items()}
    out["by_sitting"] = {str(s): _rate_block(sum(1 for t in g if t["correct"]), len(g))
                         for s, g in _by(forced, lambda t: t.get("sitting", 1)).items()}
    out["by_confidence"] = {str(c): _rate_block(sum(1 for t in g if t["correct"]), len(g))
                            for c, g in _by(forced, lambda t: t.get("confidence")).items() if c}
    # R1 (stock cmdk) mis-orders the list after a backspace (rungs/README.md): a content cue, not a
    # latency cue. Split by whether either interval used backspace so the two can be compared.
    out["by_backspace"] = {k: _rate_block(sum(1 for t in g if t["correct"]), len(g))
                           for k, g in _by(forced, _used_backspace).items()}
    cl = [t["chose_lower_measured_p50"] for t in forced if t.get("chose_lower_measured_p50") is not None]
    out["chose_lower_measured_p50"] = _rate_block(sum(cl), len(cl))
    # §5.2: logistic fit of P(correct) against the measured Δp50 per trial.
    xs = [_trial_delta(t) for t in forced]
    pts = [(x, 1 if t["correct"] else 0) for x, t in zip(xs, forced) if x is not None]
    if len(pts) >= 10 and len({round(x, 1) for x, _ in pts}) >= 3:
        fit = stats.fit_2afc_logistic([p[0] for p in pts], [p[1] for p in pts])
        out["fit_vs_measured_delta"] = _fit_json(fit)
    return out


def _fit_json(fit: dict, free_lapse: bool = False) -> dict:
    out = {"threshold_75_ms": _r(fit["threshold"], 2), "a": _r(fit["a"], 5), "b": _r(fit["b"], 6),
           "lapse": _r(fit.get("lapse", 0.0), 4), "converged": fit["converged"], "reason": fit["reason"], "n": fit["n"],
           "model": ("p = 0.5 + (0.5 − λ)·σ(a + b·x), λ free in [0, 0.06]" if free_lapse
                     else "p = 0.5 + 0.5·σ(a + b·x) (λ = 0)") + ", x in ms, ML fit; threshold where p = 0.75"}
    if free_lapse:
        out["lapse_at_bound"] = fit.get("lapse_at_bound")
    return out


def blind_summary(trials: list[dict]) -> dict:
    by_size = {}
    for size, g in _by(trials, lambda t: t["dataset_size"]).items():
        pairs = {name: blind_pair(pt) for name, pt in _by(g, lambda t: t["pair_name"]).items()}
        by_session = {sid: {name: _compact(blind_pair(pt)) for name, pt in _by(st, lambda t: t["pair_name"]).items()}
                      for sid, st in _by(g, lambda t: t["session_id"]).items()}
        by_size[size] = {"pairs": pairs, "by_session": by_session}
    return {"by_size": by_size}


def _compact(p: dict) -> dict:
    keys = ("n", "correct", "rate", "p_one_sided", "chose_interval_1", "rate_interval_1", "p_two_sided")
    return {k: p[k] for k in keys if k in p}


# ----------------------------------------------------------------------------- JND
def _hz(t) -> str:
    fm = (t.get("client") or {}).get("frame_ms")
    if not fm:
        return "unknown"
    hz = 1000 / fm
    for nominal in (60, 75, 90, 120, 144, 165, 240):
        if abs(hz - nominal) / nominal < 0.08:
            return str(nominal)
    return str(round(hz))


def jnd_group(trials: list[dict], n_boot: int, seed: str) -> dict:
    forced = [t for t in trials if t["answer"] is not None]
    levels = {}
    outcomes = defaultdict(list)
    for lv, g in sorted(_by(forced, lambda t: t["level_ms"]).items()):
        added_meas = []
        for t in g:
            ref = 0 if t["added_ms"][0] == 0 else 1
            comp = 1 - ref
            lr, lc = _lat(t["intervals"][ref]), _lat(t["intervals"][comp])
            if lr is not None and lc is not None and t["level_ms"] > 0:
                added_meas.append(lc - lr)
        applied = [((t["intervals"][0 if t["added_ms"][0] else 1].get("latency") or {}).get("applied_delay_p50"))
                   for t in g if t["level_ms"] > 0]
        applied = [a for a in applied if a is not None]
        if lv == 0:
            k1 = sum(1 for t in g if t["answer"] == 1)
            p2 = stats.binom_test_two_sided(k1, len(g))
            levels["0"] = {"catch": True, **_rate_block(k1, len(g)), "chose_interval_1": k1,
                           "p_two_sided": _r(p2, 6), "at_chance": p2 >= ALPHA}
            continue
        k = sum(1 for t in g if t["correct"])
        for t in g:
            outcomes[lv].append(1 if t["correct"] else 0)
        levels[str(lv)] = {**_rate_block(k, len(g)), "correct": k,
                           "p_one_sided": _r(stats.binom_test_greater(k, len(g)), 6),
                           "measured_delta_p50_ms_median": _r(stats.median(added_meas), 2),
                           "applied_delay_ms_median": _r(stats.median(applied), 2)}
    out = {"levels": levels, "n": len(forced)}
    xs, ys = [], []
    for lv, o in outcomes.items():
        xs += [lv] * len(o)
        ys += o
    if len(outcomes) >= 2:
        # Primary: guess rate 0.5, lapse free in [0, 0.06]; also reported with the lapse fixed at 0.
        tested = sorted(outcomes)
        for key, free in (("fit", True), ("fit_no_lapse", False)):
            fit = stats.fit_2afc_lapse(xs, ys) if free else stats.fit_2afc_logistic(xs, ys)
            fj = _fit_json(fit, free)
            if fit["threshold"] is not None and not (tested[0] <= fit["threshold"] <= tested[-1]):
                fj["note"] = f"threshold outside the tested range {tested[0]}–{tested[-1]} ms (extrapolated)"
            if fit["threshold"] is not None and n_boot > 0:
                fj.update(stats.bootstrap_threshold(outcomes, n_boot=n_boot, seed=seed, free_lapse=free))
                if fj.get("ci95"):
                    fj["ci95"] = [_r(v, 2) for v in fj["ci95"]]
            out[key] = fj
    stair = [t for t in forced if t.get("method") == "staircase"]
    if stair:
        out["staircase"] = stats.staircase_estimate([t["level_ms"] for t in stair])
    return out


def jnd_summary(trials: list[dict], headers: list[dict], n_boot: int) -> dict:
    method = {h["session_id"]: h["config"].get("method", "constant") for h in headers if h["kind"] == "jnd"}
    for t in trials:
        t["method"] = method.get(t["session_id"], "constant")
    out = {"by_mode": {}}
    for mode, g in _by(trials, lambda t: t.get("delay_mode", "defer")).items():
        m = {"all": jnd_group(g, n_boot, f"jnd:{mode}")}
        hz = _by(g, _hz)
        if len(hz) > 1:
            m["by_hz"] = {h: jnd_group(hg, n_boot, f"jnd:{mode}:{h}") for h, hg in hz.items()}
        m["methods"] = sorted({t["method"] for t in g})
        out["by_mode"][mode] = m
    return out


# ----------------------------------------------------------------------------- Gate A
def gate_a(blind: dict, jnd: dict, size: str | None, min_trials: int) -> dict:
    rule = ("Gate A condition 2 (docs/phase-a/README.md §7.3, hard requirement): R1–R3 blind 2AFC "
            f"significantly above chance (one-sided exact binomial p < {ALPHA}, n ≥ {min_trials} forced-choice "
            f"trials) AND the R3–R3 placebo at chance (two-sided exact binomial p ≥ {ALPHA}, n ≥ {min_trials}). "
            "Supporting check: the measured Δ should exceed the owner's JND (§5.3).")
    sizes = blind.get("by_size", {})
    if size is None and sizes:
        size = max(sizes, key=lambda s: sizes[s]["pairs"].get("R1-R3", {}).get("n", 0))
    pairs = sizes.get(size, {}).get("pairs", {}) if size else {}
    r13, pl = pairs.get("R1-R3"), pairs.get("R3-R3")
    c1 = {"n": r13["n"] if r13 else 0, "correct": r13.get("correct") if r13 else None,
          "p_one_sided": r13.get("p_one_sided") if r13 else None}
    c1["enough_trials"] = c1["n"] >= min_trials
    c1["pass"] = bool(r13 and c1["enough_trials"] and r13["p_one_sided"] < ALPHA)
    c2 = {"n": pl["n"] if pl else 0, "chose_interval_1": pl.get("chose_interval_1") if pl else None,
          "p_two_sided": pl.get("p_two_sided") if pl else None}
    c2["enough_trials"] = c2["n"] >= min_trials
    c2["pass"] = bool(pl and c2["enough_trials"] and pl["p_two_sided"] >= ALPHA)
    if c1["pass"] and c2["pass"]:
        verdict = "pass"
    elif (c1["enough_trials"] and not c1["pass"]) or (c2["enough_trials"] and not c2["pass"]):
        verdict = "fail"
    else:
        verdict = "incomplete"
    # Supporting check: playground-measured Δp50 (R1 − R3, from the blind intervals) vs JND.
    jnd_ms = jnd_ci = None
    for mode in ("defer", "block"):
        f = jnd.get("by_mode", {}).get(mode, {}).get("all", {}).get("fit")
        if f and f.get("threshold_75_ms") is not None:
            jnd_ms, jnd_ci = f["threshold_75_ms"], f.get("ci95")  # the lapse fit (primary)
            jnd_mode = mode
            break
    delta = r13["measured"]["delta_p50_ms_median"] if r13 else None
    sup = {"delta_p50_ms": delta, "delta_source": "playground blind intervals, median per-trial Δp50 (R1 − R3), "
           "input event.timeStamp → marker frame; the harness M-session Δ supersedes it",
           "jnd_ms": jnd_ms, "jnd_ci95": jnd_ci, "jnd_mode": jnd_mode if jnd_ms is not None else None,
           "above": (delta > jnd_ms) if (delta is not None and jnd_ms is not None) else None}
    return {"rule": rule, "dataset_size": size, "alpha": ALPHA, "min_trials": min_trials,
            "r1_r3_above_chance": c1, "placebo_at_chance": c2, "verdict": verdict,
            "delta_vs_jnd": sup}


# ----------------------------------------------------------------------------- entry point
def build_summary(paths, *, n_boot: int = 1000, min_trials: int = 40, gate_size: str | None = None) -> dict:
    headers, trials = read_records(paths)
    problems = []
    for t in trials:
        for e in validate(t):
            problems.append(f"{t.get('session_id')}#{t.get('trial_index')}: {e}")
    blind_t = [t for t in trials if t["kind"] == "blind"]
    jnd_t = [t for t in trials if t["kind"] == "jnd"]
    blind = blind_summary(blind_t)
    jnd = jnd_summary(jnd_t, headers, n_boot)
    return {
        "schema": SUMMARY_SCHEMA, "generated_at": now_iso(), "inputs": [str(p) for p in paths],
        "sessions": [{"session_id": h["session_id"], "kind": h["kind"], "seed": h["seed"],
                      "created_at": h["created_at"], "dataset_size": h["config"].get("dataset_size"),
                      "method": h["config"].get("method"), "delay_mode": h["config"].get("delay_mode"),
                      "n_trials": sum(1 for t in trials if t["session_id"] == h["session_id"]),
                      "total_trials": h.get("total_trials"), "rungs": h.get("rungs")} for h in headers],
        "n_trials": {"blind": len(blind_t), "jnd": len(jnd_t)},
        "blind": blind, "jnd": jnd,
        "gate_a_perceptibility": gate_a(blind, jnd, gate_size, min_trials),
        "validation_problems": problems,
    }


def _fmt_ci(ci):
    return f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "—"


def render_text(s: dict) -> str:
    L = [f"Playground summary ({s['n_trials']['blind']} blind trials, {s['n_trials']['jnd']} JND trials)"]
    for size, blk in s["blind"]["by_size"].items():
        L.append(f"\nBlind 2AFC, dataset {size}")
        L.append(f"  {'pair':8} {'n':>4} {'correct':>8} {'rate':>6} {'95% CI':>14} {'p (1-sided)':>12} {'d′':>6} {'Δp50 ms':>8}")
        for name, p in sorted(blk["pairs"].items(), key=lambda kv: kv[0]):
            d = p["measured"]["delta_p50_ms_median"]
            ds = f"{d:8.1f}" if d is not None else f"{'—':>8}"
            if p["placebo"]:
                L.append(f"  {name:8} {p['n']:>4} {'int.1: ' + str(p['chose_interval_1']):>8} {p['rate_interval_1'] or 0:6.2f} "
                         f"{_fmt_ci(p['ci95']):>14} {'2-sided ' + format(p['p_two_sided'] or 1, '.4f'):>12} {'':>6} {ds}  (placebo)")
            else:
                dp = f"{p['d_prime']:6.2f}" if p["d_prime"] is not None else f"{'—':>6}"
                L.append(f"  {name:8} {p['n']:>4} {p['correct']:>8} {p['rate'] or 0:6.2f} {_fmt_ci(p['ci95']):>14} "
                         f"{p['p_one_sided'] if p['p_one_sided'] is not None else 1:12.4f} {dp} {ds}")
    for mode, m in s["jnd"]["by_mode"].items():
        a = m["all"]
        L.append(f"\nJND calibration (mode {mode}, {'/'.join(m['methods'])}), {a['n']} forced-choice trials")
        L.append(f"  {'N ms':>5} {'n':>4} {'rate':>6} {'95% CI':>14} {'applied':>8} {'Δp50':>7}")
        for lv, r in sorted(a["levels"].items(), key=lambda kv: int(kv[0])):
            if r.get("catch"):
                L.append(f"  {lv:>5} {r['n']:>4} {r['rate'] or 0:6.2f} {_fmt_ci(r['ci95']):>14}   catch: rate = chose interval 1, p2={r['p_two_sided']}")
            else:
                ap = r["applied_delay_ms_median"]
                md = r["measured_delta_p50_ms_median"]
                L.append(f"  {lv:>5} {r['n']:>4} {r['rate'] or 0:6.2f} {_fmt_ci(r['ci95']):>14} "
                         f"{(f'{ap:8.1f}' if ap is not None else '       —')} {(f'{md:7.1f}' if md is not None else '      —')}")
        for key, label in (("fit", "with lapse"), ("fit_no_lapse", "lapse = 0")):
            f = a.get(key)
            if f:
                th = f["threshold_75_ms"]
                lam = f" λ = {f['lapse']}" if key == "fit" else ""
                L.append(f"  75% threshold (JND, {label}{lam}): {th if th is not None else '—'} ms, 95% CI {f.get('ci95')}"
                         f"{'  ' + f['note'] if f.get('note') else ''}{'  (' + f['reason'] + ')' if f.get('reason') else ''}")
        if a.get("staircase"):
            L.append(f"  staircase: mean of last reversals = {a['staircase']['threshold']} ms ({a['staircase']['n_reversals']} reversals)")
    g = s["gate_a_perceptibility"]
    L.append(f"\nGate A perceptibility (dataset {g['dataset_size']}): {g['verdict'].upper()}")
    c1, c2 = g["r1_r3_above_chance"], g["placebo_at_chance"]
    L.append(f"  R1–R3 above chance: n={c1['n']} p={c1['p_one_sided']} -> {'pass' if c1['pass'] else 'not met'}")
    L.append(f"  R3–R3 placebo at chance: n={c2['n']} p(2-sided)={c2['p_two_sided']} -> {'pass' if c2['pass'] else 'not met'}")
    sj = g["delta_vs_jnd"]
    L.append(f"  Δp50 (R1−R3) {sj['delta_p50_ms']} ms vs JND {sj['jnd_ms']} ms -> above: {sj['above']}")
    if s["validation_problems"]:
        L.append(f"\n{len(s['validation_problems'])} schema problems, e.g. {s['validation_problems'][:3]}")
    return "\n".join(L)


def main(paths, out: str | None, n_boot: int, min_trials: int, gate_size: str | None) -> int:
    s = build_summary(paths, n_boot=n_boot, min_trials=min_trials, gate_size=gate_size)
    print(render_text(s))
    if out is None:
        first = Path(paths[0])
        out = str((first if first.is_dir() else first.parent) / "summary.json")
    Path(out).write_text(json.dumps(s, indent=2) + "\n")
    print(f"\nwrote {out}")
    return 0

