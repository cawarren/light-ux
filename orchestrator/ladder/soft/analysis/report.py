"""`ladder soft report`: one self-contained HTML page (inline SVG) + a combined trials CSV.

Sections (phase-a §4.7): Gate A table; percentiles with bootstrap CIs per rung x size x Hz x
trial type with ratio and frame deltas vs R1; ECDFs (log x); histogram with frame gridlines;
latency vs trigger phase; A.seq latency vs seq_pos; stage splits; correctness; health; sessions
and provenance. Headline statistics use M sessions only (T sessions are attribution-only), and
exclude warm-up trials and rows without a valid latency (no_event, clock_flag,
no_probe_entry). timeout rows without a presentation count as +inf (censored).
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
from datetime import datetime, timezone

from . import build, gate, stats, svg
from .. import timing

KINDS = ["A.first_key", "A.clear", "A.seq"]
RUNG_ORDER = ["r1-typical", "r1-vite", "r2-diligent", "r3-no-framework", "r4"]
# Categorical slots from the reference palette (dataviz skill), fixed per rung (never by rank).
RUNG_VAR = {"r1-vite": "--s1", "r2-diligent": "--s2", "r3-no-framework": "--s3", "r1-typical": "--s7", "r4": "--s5"}
STAGE_COLORS = ["var(--s1)", "var(--s2)", "var(--s3)"]
esc = svg.esc


def rung_color(r):
    return "var(%s)" % RUNG_VAR.get(r, "--s8")


def rung_sort(r):
    return (RUNG_ORDER.index(r) if r in RUNG_ORDER else 99, r)


def fmt(v, nd=1):
    if v is None:
        return "–"
    if isinstance(v, float) and math.isinf(v):
        return "∞" if v > 0 else "−∞"
    return ("%." + str(nd) + "f") % v


def fmt_ci(ci, nd=1):
    if not ci or ci[0] is None:
        return ""
    return '<span class="ci">[%s, %s]</span>' % (fmt(ci[0], nd), fmt(ci[1], nd))


def load_session(d, check_correctness=True):
    d = os.path.abspath(d)
    tc = os.path.join(d, "derived", "trials.csv")
    man = build.load_manifest(d)
    raw_mtime = max((os.path.getmtime(os.path.join(dp, f)) for dp, _, fs in os.walk(os.path.join(d, "blocks")) for f in fs),
                    default=0)
    if os.path.exists(tc) and os.path.getmtime(tc) >= raw_mtime:
        rows = build.read_csv(tc)
        segs = json.load(open(os.path.join(d, "derived", "segments.json")))
        summary = json.load(open(os.path.join(d, "derived", "summary.json")))
    else:
        rows, segs, man, summary = build.analyze_session(d, check_correctness=check_correctness, verbose=True)
    return {"dir": d, "manifest": man, "rows": rows, "segs": segs, "summary": summary}


def latency_value(r):
    if r["status"] not in timing.LATENCY_STATUSES:
        return None
    v = r.get("lat_present_ms")
    if v is None:
        return math.inf if r["status"] == "timeout" else None
    return v


def seed_of(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def build_report(session_dirs, out, perceptibility=None, headline_size="50k", headline_hz=60, resamples=10000,
                 check_correctness=True):
    sessions = [load_session(d, check_correctness) for d in session_dirs]
    all_rows = [r for s in sessions for r in s["rows"]]
    simulated = any(s["manifest"]["simulated"] for s in sessions)
    m_rows = [r for r in all_rows if r["session_kind"] == "M" and not r["warmup"]]
    rungs = sorted({r["rung"] for r in m_rows}, key=rung_sort)
    r1 = "r1-typical" if "r1-typical" in rungs else ("r1-vite" if "r1-vite" in rungs else None)
    r3 = "r3-no-framework" if "r3-no-framework" in rungs else None
    settings = sorted({(r["hz"], r["dataset_size"]) for r in m_rows}, key=lambda t: (t[0], int(t[1].rstrip("k"))))

    # ---------------------------------------------------------------- stats per cell
    cells = {}
    for hz, size in settings:
        for kind in KINDS:
            for rung in rungs:
                vals = [latency_value(r) for r in m_rows
                        if r["hz"] == hz and r["dataset_size"] == size and r["action_kind"] == kind and r["rung"] == rung]
                vals = [v for v in vals if v is not None]
                if vals:
                    cells[(hz, size, kind, rung)] = stats.summarize(vals, B=resamples, seed=seed_of(hz, size, kind, rung))

    perc = gate.perceptibility(perceptibility)
    parts = []
    title = "Latency Ladder, Phase A software report"
    if simulated:
        parts.append('<div class="banner">SIMULATED — NOT REAL. At least one session was run with <code>--simulate</code>: '
                     'keys went through CDP into headless Chromium in a container, skipping the kernel, compositor and '
                     'Chrome\'s input path. Nothing below measures real latency; it only exercises the pipeline.</div>')

    # ---------------------------------------------------------------- Gate A
    g_rows = []
    for hz, size in settings:
        frame = 1000.0 / hz
        a, b = cells.get((hz, size, "A.first_key", r1)), cells.get((hz, size, "A.first_key", r3))
        cmp = stats.compare(a, b, 1, frame) if (a and b) else None
        gv = gate.gap_verdict(cmp)
        headline = (hz == headline_hz and size == headline_size)
        sim_here = any(r["simulated"] for r in m_rows if r["hz"] == hz and r["dataset_size"] == size)
        overall = gate.gate_a(gv, perc, simulated=sim_here, valid_settings=headline)
        seq_a, seq_b = cells.get((hz, size, "A.seq", r1)), cells.get((hz, size, "A.seq", r3))
        seq_cmp = stats.compare(seq_a, seq_b, 1, frame) if (seq_a and seq_b) else None
        g_rows.append("<tr%s><td>%g Hz</td><td>%s</td><td>%s %s</td><td>%s %s</td><td>%s× %s</td><td>%s %s</td>"
                      "<td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td></tr>" % (
                          ' class="headline"' if headline else "", hz, size,
                          fmt(a and a["p95"]), fmt_ci(a and a["p95_ci"]), fmt(b and b["p95"]), fmt_ci(b and b["p95_ci"]),
                          fmt(cmp and cmp["ratio"], 2), fmt_ci(cmp and cmp["ratio_ci"], 2),
                          fmt(cmp and cmp.get("diff_frames"), 2), fmt_ci(cmp and cmp.get("diff_frames_ci"), 2),
                          {True: "pass", False: "fail", None: "–"}[gv["pass"]], esc(perc["status"]), esc(overall),
                          "p95 ratio %s×, Δ %s frames" % (fmt(seq_cmp and seq_cmp["ratio"], 2), fmt(seq_cmp and seq_cmp.get("diff_frames"), 2))))
    parts.append("<h2>Gate A</h2><p>Rule (phase-a §7.3, both required): <b>gap</b> = at the headline settings "
                 "(%g Hz, %s, <code>A.first_key</code>, M sessions) p95(R1)/p95(R3) ≥ 3 with CI low ≥ 2, or p95 difference ≥ 2 frames "
                 "with CI low ≥ 1 frame; <b>perceptibility</b> = R1–R3 blind 2AFC above chance (one-sided binomial p &lt; 0.05, ≥ 40 trials) "
                 "and R3–R3 placebo at chance. R1 here = <code>%s</code>, R3 = <code>%s</code>. The headline row is highlighted; "
                 "other rows are informative.</p>" % (headline_hz, esc(headline_size), esc(r1), esc(r3)))
    parts.append("<table><tr><th>Hz</th><th>size</th><th>R1 p95 (ms)</th><th>R3 p95 (ms)</th><th>ratio</th><th>Δ frames</th>"
                 "<th>gap</th><th>perceptibility</th><th>Gate A</th><th>A.seq alongside</th></tr>%s</table>" % "".join(g_rows))
    parts.append("<p class='note'>Perceptibility: %s — %s</p>" % (esc(perc["status"]), esc(perc.get("reason", ""))))
    if perc.get("pairs"):
        parts.append("<table><tr><th>pair</th><th>n</th><th>correct</th><th>%</th><th>p (one-sided)</th></tr>%s</table>" % "".join(
            "<tr><td>%s – %s%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
                esc(p["a"]), esc(p["b"]), " (placebo)" if p["placebo"] or p["a"] == p["b"] else "", p["n"], p["correct"],
                fmt(100 * p["pct"] if p["pct"] is not None else None), fmt(p["p_value"], 4)) for p in perc["pairs"]))

    # ---------------------------------------------------------------- percentile tables
    parts.append("<h2>Percentiles (M sessions, warm-up excluded)</h2><p>ms from injected key-down to the presentation of the "
                 "marker flip (Element Timing; fallback fields are counted in the health table). 95%% bootstrap CIs, %d resamples: "
                 "BCa for p50/p95, percentile for p99; <code>-mbb</code> = moving-block bootstrap because the lag-1..10 "
                 "autocorrelation was significant. Ratio = p95(%s) / p95(rung) and Δ = their difference in frames, independent bootstrap of both arms.</p>" % (resamples, esc(r1)))
    for hz, size in settings:
        trs = []
        for kind in KINDS:
            for rung in rungs:
                c = cells.get((hz, size, kind, rung))
                if not c:
                    continue
                ref = cells.get((hz, size, kind, r1))
                cmp = stats.compare(ref, c, 1, 1000.0 / hz) if (ref and rung != r1) else None
                trs.append("<tr><td>%s</td><td><i class='sw' style='background:%s'></i>%s</td><td>%d%s</td>"
                           "<td>%s %s</td><td>%s %s</td><td>%s %s</td><td>%s</td><td>%s %s</td><td>%s %s</td></tr>" % (
                               kind, rung_color(rung), esc(rung), c["n"], " (%d cens.)" % c["censored"] if c["censored"] else "",
                               fmt(c["p50"]), fmt_ci(c["p50_ci"]), fmt(c["p95"]), fmt_ci(c["p95_ci"]),
                               fmt(c["p99"]), fmt_ci(c["p99_ci"]), esc("/".join(sorted(set(c["ci_methods"])))),
                               fmt(cmp and cmp["ratio"], 2), fmt_ci(cmp and cmp["ratio_ci"], 2),
                               fmt(cmp and cmp.get("diff_frames"), 2), fmt_ci(cmp and cmp.get("diff_frames_ci"), 2)))
        parts.append("<h3>%g Hz, %s</h3><table><tr><th>trial</th><th>rung</th><th>n</th><th>p50</th><th>p95</th><th>p99</th>"
                     "<th>CI method</th><th>p95 ratio R1 / rung</th><th>p95 Δ frames R1 − rung</th></tr>%s</table>" % (hz, esc(size), "".join(trs)))

    # ---------------------------------------------------------------- plots
    leg = svg.legend([(r, rung_color(r)) for r in rungs])
    parts.append("<h2>Distributions</h2>" + leg)
    for hz, size in settings:
        charts = []
        for kind in KINDS:
            series = [(rung, [latency_value(r) for r in m_rows if r["hz"] == hz and r["dataset_size"] == size
                              and r["action_kind"] == kind and r["rung"] == rung]) for rung in rungs]
            series = [(n, [v for v in vs if v is not None]) for n, vs in series]
            fin = [v for _, vs in series for v in vs if math.isfinite(v)]
            if not fin:
                continue
            ax = svg.Axes(max(0.5, min(fin) * 0.8), max(fin) * 1.2, 0, 1, xlog=True)
            body = ax.frame("latency, ms (log)", "fraction of trials", yfmt="{:.2g}")
            for n, vs in series:
                body += svg.step_ecdf(ax, stats.ecdf(vs), rung_color(n), "%s %s" % (n, kind))
            charts.append("<figure>%s<figcaption>ECDF, %s</figcaption></figure>" % (ax.svg(body, "ECDF " + kind), kind))
        parts.append("<h3>%g Hz, %s</h3><div class='charts'>%s</div>" % (hz, esc(size), "".join(charts)))
        # histogram with frame gridlines + trigger phase, A.first_key
        charts = []
        frame = 1000.0 / hz
        for rung in rungs:
            rs = [r for r in m_rows if r["hz"] == hz and r["dataset_size"] == size and r["action_kind"] == "A.first_key"
                  and r["rung"] == rung]
            vs = [v for v in (latency_value(r) for r in rs) if v is not None and math.isfinite(v)]
            if not vs:
                continue
            lo, hi = min(vs), max(vs)
            bw = max(frame / 8, (hi - lo) / 60 or 1)
            nb = int((hi - lo) / bw) + 1
            counts = [0] * nb
            for v in vs:
                counts[min(nb - 1, int((v - lo) / bw))] += 1
            ax = svg.Axes(lo - bw, hi + bw, 0, max(counts) * 1.1)
            body = ax.frame("latency, ms (lines = frame boundaries)", "trials")
            k = math.ceil((lo - bw) / frame)
            while k * frame <= hi + bw:
                body += svg.vline(ax, k * frame)
                k += 1
            for j, c in enumerate(counts):
                if c:
                    x0, x1 = ax.x(lo + j * bw), ax.x(lo + (j + 1) * bw)
                    body += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="var(--surface)" stroke-width="1"><title>%.1f–%.1f ms: %d</title></rect>' % (
                        x0, ax.y(c), max(0.5, x1 - x0), ax.y(0) - ax.y(c), rung_color(rung), lo + j * bw, lo + (j + 1) * bw, c)
            charts.append("<figure>%s<figcaption>%s, A.first_key histogram</figcaption></figure>" % (ax.svg(body, rung), esc(rung)))
            pts = [(r["trigger_phase"], latency_value(r)) for r in rs if r["trigger_phase"] is not None
                   and latency_value(r) is not None and math.isfinite(latency_value(r))]
            if pts:
                ax2 = svg.Axes(0, 1, min(p[1] for p in pts) * 0.95, max(p[1] for p in pts) * 1.05)
                b2 = ax2.frame("trigger phase within the rAF frame", "latency, ms") + svg.dots(ax2, pts, rung_color(rung), rung)
                charts.append("<figure>%s<figcaption>%s, latency vs trigger phase (sawtooth = frame quantization)</figcaption></figure>"
                              % (ax2.svg(b2, rung + " phase"), esc(rung)))
        parts.append("<div class='charts'>%s</div>" % "".join(charts))
        # seq_pos
        rs = [r for r in m_rows if r["hz"] == hz and r["dataset_size"] == size and r["action_kind"] == "A.seq"]
        if rs:
            ys = []
            lines = []
            for rung in rungs:
                med, p95 = [], []
                for pos in range(40):
                    vs = [latency_value(r) for r in rs if r["rung"] == rung and r["seq_pos"] == pos]
                    vs = [v for v in vs if v is not None and math.isfinite(v)]
                    if vs:
                        med.append((pos, stats.quantile(vs, .5)))
                        p95.append((pos, stats.quantile(vs, .95)))
                ys += [y for _, y in med + p95]
                lines.append((rung, med, p95))
            if ys:
                ax = svg.Axes(0, 39, 0, max(ys) * 1.1)
                body = ax.frame("seq_pos (0-19 typing, 20-39 backspace)", "latency, ms") + svg.vline(ax, 19.5, "divider")
                for rung, med, p95 in lines:
                    body += svg.polyline(ax, med, rung_color(rung), name="%s median" % rung)
                    body += svg.polyline(ax, p95, rung_color(rung), dash="4 3", name="%s p95" % rung)
                parts.append("<div class='charts'><figure>%s<figcaption>A.seq latency vs seq_pos, %g Hz %s "
                             "(solid median, dashed p95): queue growth shows as a rising line</figcaption></figure></div>"
                             % (ax.svg(body, "seq_pos"), hz, esc(size)))

    # ---------------------------------------------------------------- stage splits
    parts.append("<h2>Stage splits (M sessions, medians per stage)</h2><p>injected key-down → <code>event.timeStamp</code> "
                 "(OS delivery), → marker flip task (handler, worker, deferred rendering), → marker presentation. Medians of each "
                 "stage do not add up exactly to the median total. Trace-based stages (EventLatency) come from T sessions "
                 "(<code>ladder soft stages</code>).</p>")
    seg_names = ["OS delivery", "event → flip", "flip → present"]
    for hz, size in settings:
        bars = []
        for kind in ("A.first_key", "A.seq"):
            for rung in rungs:
                rs = [r for r in m_rows if r["hz"] == hz and r["dataset_size"] == size and r["action_kind"] == kind
                      and r["rung"] == rung and r["status"] in ("ok", "coalesced", "wrong_result")]
                if not rs:
                    continue
                v = [stats.quantile([r[c] for r in rs if r[c] is not None], .5) for c in
                     ("lat_os_delivery_ms", "lat_to_flip_ms", "lat_flip_to_present_ms")]
                bars.append(("%s %s" % (rung, kind.split(".")[1]), [x or 0 for x in v]))
        if bars:
            parts.append("<h3>%g Hz, %s</h3>%s<figure style='max-width:760px'>%s</figure>" % (
                hz, esc(size), svg.legend(list(zip(seg_names, STAGE_COLORS))),
                svg.hbar_stack(bars, seg_names, STAGE_COLORS, "ms (median per stage)", w=640)))

    # ---------------------------------------------------------------- correctness
    parts.append("<h2>Correctness spot checks</h2><p>Each attributed flip's displayed prefix (up to 50 rows) and count against "
                 "<code>rank()</code> from <code>parity/</code>. R1: tie-insensitive on forward keys, result set only on backspace; "
                 "R2+: strict everywhere (decided 2026-09-30). Any wrong result makes the rung non-parity for Phase A.</p>")
    ctr = {}
    for r in all_rows:
        if r["warmup"] or r.get("correct") is None:
            continue
        k = (r["rung"], r["dataset_size"], r["correctness_level"])
        c = ctr.setdefault(k, [0, 0])
        c[0] += 1
        c[1] += 0 if r["correct"] else 1
    parts.append("<table><tr><th>rung</th><th>size</th><th>level</th><th>checked</th><th>wrong</th><th>parity</th></tr>%s</table>" % "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>" % (
            esc(k[0]), esc(k[1]), esc(k[2]), v[0], v[1], "yes" if v[1] == 0 else "<b>NO</b>")
        for k, v in sorted(ctr.items(), key=lambda kv: (rung_sort(kv[0][0]), kv[0][1], kv[0][2]))))

    # ---------------------------------------------------------------- health
    parts.append("<h2>Health</h2>")
    hrows = []
    for s in sessions:
        man = s["manifest"]
        for b in man["blocks"]:
            rs = [r for r in s["rows"] if r["block_id"] == b["block_id"]]
            sc = {}
            for r in rs:
                sc[r["status"]] = sc.get(r["status"], 0) + 1
            srcs = {}
            for r in rs:
                if r["t_present_source"]:
                    srcs[r["t_present_source"]] = srcs.get(r["t_present_source"], 0) + 1
            segs = [g for g in s["segs"] if g["block_id"] == b["block_id"]]
            rtts = [g["sync_before"]["min_rtt_ms"] for g in segs if g.get("sync_before")]
            drifts = [abs(g["sync"]["drift_ms"]) for g in segs if g.get("sync") and g["sync"].get("drift_ms") is not None]
            late = [r["inject_late_us"] for r in rs if r["inject_late_us"] is not None]
            osd = [r["lat_os_delivery_ms"] for r in rs if r["lat_os_delivery_ms"] is not None]
            unp = sum(1 for r in rs if r.get("own_flip_unpainted"))
            clp = sum(1 for r in rs if r.get("present_clamped"))
            hrows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%d / %d</td><td>%s</td><td>%s</td>"
                         "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='flags'>%s</td></tr>" % (
                             esc(man["session_kind"] + (" SIM" if man["simulated"] else "")), esc(b["block_id"]), esc(b["rung"]),
                             esc(b["size"]), esc(b["health"]), len(rs),
                             esc(", ".join("%s %d" % kv for kv in sorted(sc.items()))), unp, clp,
                             esc(", ".join("%s %d" % kv for kv in sorted(srcs.items()))),
                             fmt(timing.quantile(rtts, .5), 3), fmt(max(drifts) if drifts else None, 3),
                             fmt(b.get("measured_hz"), 1), fmt(timing.quantile(late, .99), 0), fmt(timing.quantile(osd, .5), 2),
                             esc("; ".join(b.get("flags", [])))))
    parts.append("<table class='small'><tr><th>kind</th><th>block</th><th>rung</th><th>size</th><th>health</th><th>trials</th>"
                 "<th>status counts</th><th>own flip unpainted / present clamped</th><th>presentation source</th><th>sync min RTT p50 (ms)</th><th>max |drift| (ms)</th>"
                 "<th>measured Hz</th><th>inject late p99 (µs)</th><th>OS delivery p50 (ms)</th><th>flags</th></tr>%s</table>" % "".join(hrows))
    parts.append("<p class='note'>Statuses: ok; coalesced (key had no flip of its own, attributed to the next flip whose query "
                 "includes it, latency kept; this includes keys whose own flip was never painted because several flips fell in "
                 "one rendering opportunity — <i>own flip unpainted</i>); wrong_result; timeout (no settle within the timeout; latency kept if a flip "
                 "presented, else censored at +∞); no_probe_entry, no_event, clock_flag (excluded from latency stats). "
                 "Presentation source other than <code>presentationTime</code> means the Chrome build lacked it and the "
                 "fallback (paintTime, then renderTime) was used; <i>present clamped</i> = the fallback time preceded the flip task "
                 "(seen with Chromium 141 renderTime) and was clamped to the flip time, a lower bound.</p>")

    # ---------------------------------------------------------------- sessions / provenance
    srows = []
    for s in sessions:
        m = s["manifest"]
        inj = m.get("injector", {})
        srows.append("<tr><td>%s</td><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(m["session_id"]), esc(m["session_kind"]), " <b>SIMULATED</b>" if m["simulated"] else "", esc(m["input_source"]),
            esc(m["machine_id"]), esc((m.get("chrome") or {}).get("version")), fmt(m["display"]["nominal_hz"], 0),
            fmt(m["display"].get("measured_hz"), 1), esc(m["display"]["mode"]),
            fmt((inj.get("jitter_selftest") or {}).get("p99_us"), 0), esc(m["started_at"]),
            esc(((m["provenance"].get("orchestrator_git") or {}).get("sha") or "")[:12])))
    parts.append("<h2>Sessions</h2><table class='small'><tr><th>session</th><th>kind</th><th>input</th><th>machine</th><th>Chrome</th>"
                 "<th>Hz nominal / measured</th><th>display</th><th>injector jitter p99 (µs)</th><th>started</th><th>git</th></tr>%s</table>" % "".join(srows))
    keys = ["chrome", "display", "env"]
    if len(sessions) > 1:
        diffs = []
        base = sessions[0]["manifest"]
        for s in sessions[1:]:
            for k in keys:
                if json.dumps(base.get(k), sort_keys=True, default=str) != json.dumps(s["manifest"].get(k), sort_keys=True, default=str):
                    diffs.append("%s vs %s: <code>%s</code> differs" % (esc(base["session_id"]), esc(s["manifest"]["session_id"]), k))
        parts.append("<h3>Manifest differences</h3><p>%s</p>" % ("<br>".join(diffs) or "none in chrome/display/env"))

    csv_path = os.path.splitext(out)[0] + "-trials.csv"
    build.write_csv(csv_path, all_rows)
    parts.append("<p class='note'>All trials (every session, warm-up and every status included): <code>%s</code>. "
                 "Quantiles: Hyndman–Fan type 7. Generated %s by <code>ladder soft report</code>.</p>" % (
                     esc(os.path.basename(csv_path)), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    page = PAGE.replace("{{TITLE}}", esc(title + (" (SIMULATED — NOT REAL)" if simulated else ""))).replace("{{BODY}}", "\n".join(parts))
    with open(out, "w") as f:
        f.write(page)
    print("report: %s\ntrials: %s%s" % (out, csv_path, "\n*** SIMULATED - NOT REAL ***" if simulated else ""))
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root { color-scheme: light; --surface:#fcfcfb; --bg:#f4f3ef; --ink:#0b0b0b; --ink2:#52514e; --muted:#8a8984; --rule:#dcdad3;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s5:#e87ba4; --s7:#4a3aa7; --s8:#e34948; --warn-bg:#fde8e7; --warn-ink:#8f1d1c; --hl:#fff6d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --surface:#1a1a19; --bg:#121211; --ink:#fff;
  --ink2:#c3c2b7; --muted:#8f8e86; --rule:#3a3936; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s5:#d55181; --s7:#9085e9;
  --s8:#e66767; --warn-bg:#4a1716; --warn-ink:#ffd9d7; --hl:#3a3212; } }
:root[data-theme="dark"] { color-scheme: dark; --surface:#1a1a19; --bg:#121211; --ink:#fff; --ink2:#c3c2b7; --muted:#8f8e86;
  --rule:#3a3936; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s5:#d55181; --s7:#9085e9; --s8:#e66767; --warn-bg:#4a1716;
  --warn-ink:#ffd9d7; --hl:#3a3212; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }
main { max-width:1180px; margin:0 auto; padding:16px; }
h1 { font-size:22px; } h2 { margin-top:32px; border-bottom:1px solid var(--rule); padding-bottom:4px; } h3 { font-size:15px; }
.banner { background:var(--warn-bg); color:var(--warn-ink); border:2px solid var(--s8); padding:12px 14px; font-weight:600; border-radius:6px; }
table { border-collapse:collapse; background:var(--surface); margin:8px 0; display:block; overflow-x:auto; max-width:100%; }
th, td { border-bottom:1px solid var(--rule); padding:4px 8px; text-align:left; vertical-align:top; white-space:nowrap; }
th { color:var(--ink2); font-weight:600; } td.flags { white-space:normal; max-width:320px; color:var(--ink2); }
table.small td, table.small th { font-size:12px; }
tr.headline td { background:var(--hl); }
.ci { color:var(--muted); font-size:12px; } .note { color:var(--ink2); font-size:13px; }
.charts { display:flex; flex-wrap:wrap; gap:12px; } figure { margin:0; background:var(--surface); border:1px solid var(--rule); border-radius:6px; padding:6px; flex:1 1 440px; max-width:560px; }
figcaption { color:var(--ink2); font-size:12px; padding:2px 4px; }
svg.chart { width:100%; height:auto; display:block; }
svg .grid { stroke:var(--rule); stroke-width:1; } svg .axis, svg .tickmark { stroke:var(--muted); }
svg .frameline { stroke:var(--muted); stroke-dasharray:2 3; } svg .divider { stroke:var(--ink2); stroke-dasharray:5 3; }
svg text { fill:var(--ink2); font-size:11px; } svg .label { fill:var(--ink2); font-size:12px; }
.legend { display:flex; gap:14px; flex-wrap:wrap; margin:6px 0; color:var(--ink2); }
.legend i, i.sw { display:inline-block; width:12px; height:12px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
code { font-size:12.5px; }
</style></head><body><main>
<h1>{{TITLE}}</h1>
{{BODY}}
</main></body></html>
"""
