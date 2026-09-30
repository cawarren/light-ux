"""Turn the raw A1 report into PASS / FAIL / UNKNOWN / INFO rows.

All page times are in ms on the page clock (performance.now() domain; Chrome's
event.timeStamp and PerformanceEntry times share it). All host times are ns on
clock.now_ns(). The clock sync gives `offset_ms` with
    host_ms = page_ms + offset_ms.
"""
from __future__ import annotations

import math

PASS, FAIL, UNKNOWN, INFO = "PASS", "FAIL", "UNKNOWN", "INFO"


# ---------------------------------------------------------------- helpers

def quantile(xs, q):
    """Hyndman-Fan type 7 (numpy default)."""
    xs = sorted(xs)
    if not xs:
        return None
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def summary(xs, nd=3):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    r = lambda v: None if v is None else round(v, nd)
    return {"n": len(xs), "min": r(min(xs)), "p50": r(quantile(xs, .5)), "p95": r(quantile(xs, .95)),
            "p99": r(quantile(xs, .99)), "max": r(max(xs)), "mean": r(sum(xs) / len(xs))}


def fmt(s, unit="ms"):
    if not s or not s.get("n"):
        return "no data"
    return "p50 %.3f / p95 %.3f / p99 %.3f %s (n=%d, min %.3f, max %.3f)" % (
        s["p50"], s["p95"], s["p99"], unit, s["n"], s["min"], s["max"])


GRID_CANDIDATES_MS = [8, 4, 1, 0.5, 0.1, 0.02, 0.01, 0.005]


def grid_ms(values):
    """Coarsest grid (in ms) that every value sits on, or None if finer than 5 us."""
    vals = [v for v in values if v is not None and v != 0]
    if len(vals) < 5:
        return "n/a"
    for q in GRID_CANDIDATES_MS:
        if all(abs(v / q - round(v / q)) < 1e-3 for v in vals):
            return q
    return None


def circular_R(fracs):
    """Mean resultant length of phases in [0,1): ~0 uniform, ~1 concentrated."""
    if not fracs:
        return None
    c = sum(math.cos(2 * math.pi * f) for f in fracs) / len(fracs)
    s = sum(math.sin(2 * math.pi * f) for f in fracs) / len(fracs)
    return math.hypot(c, s)


def row(rid, assumption, ref, status, evidence, **numbers):
    return {"id": rid, "assumption": assumption, "ref": ref, "status": status,
            "evidence": evidence, "numbers": numbers}


# ---------------------------------------------------------------- pieces

def frame_period_ms(raf):
    """Median rAF interval from [frame, ts, cb] rows (consecutive frames only)."""
    iv = [b[1] - a[1] for a, b in zip(raf, raf[1:]) if b[0] == a[0] + 1]
    return quantile(iv, .5) if iv else None, iv


def clock_sync(samples):
    """samples: [[t0_page_ms, t1_server_ns, t2_server_ns, t3_page_ms], ...] (ns as strings).

    NTP: offset = ((t1 - t0) + (t2 - t3)) / 2, delay = (t3 - t0) - (t2 - t1);
    host_ms = page_ms + offset. Take the minimum-delay sample.
    """
    rows = []
    for smp in samples or []:
        if len(smp) == 3:          # old format [t0, t_ns, t3]
            smp = [smp[0], smp[1], smp[1], smp[2]]
        t0, t1, t2, t3 = smp[0], int(smp[1]) / 1e6, int(smp[2]) / 1e6, smp[3]
        rtt = (t3 - t0) - (t2 - t1)
        rows.append((rtt, ((t1 - t0) + (t2 - t3)) / 2))
    if not rows:
        return None
    rows.sort()
    best = rows[0]
    top = [o for _, o in rows[:5]]
    return {"n": len(rows), "min_rtt_ms": round(best[0], 4), "offset_ms": best[1],
            "err_ms": best[0] / 2, "rtt_p50_ms": round(quantile([r for r, _ in rows], .5), 4),
            "offset_spread_top5_ms": round(max(top) - min(top), 4)}


def combine_sync(before, after):
    if before and after:
        return {"offset_ms": (before["offset_ms"] + after["offset_ms"]) / 2,
                "drift_ms": after["offset_ms"] - before["offset_ms"],
                "err_ms": max(before["err_ms"], after["err_ms"])}
    one = before or after
    if one:
        return {"offset_ms": one["offset_ms"], "drift_ms": None, "err_ms": one["err_ms"]}
    return None


def best_time(entry):
    """(value, field) for the best available presentation-ish time of an entry."""
    for f in ("presentationTime", "renderTime", "paintTime"):
        v = entry.get(f)
        if v:
            return v, f
    return None, None


def match_trials(records, keys, offset_ms, window_ms=100.0):
    """Pair injector records with page keydowns by code and time (robust to lost keys)."""
    from .keymap import dom_code
    pairs, used = [], set()
    cand = [k for k in keys if not k.get("repeat")]
    for rec in records:
        t_page = rec["t_down_ns"] / 1e6 - offset_ms
        best = None
        for k in cand:
            if id(k) in used or k.get("code") != dom_code(rec["letter"]):
                continue
            d = k["ts"] - t_page
            if -window_ms / 4 <= d <= window_ms and (best is None or abs(d) < abs(best[0])):
                best = (d, k)
        if best:
            used.add(id(best[1]))
            pairs.append((rec, best[1], t_page))
        else:
            pairs.append((rec, None, t_page))
    return pairs


# ---------------------------------------------------------------- main

def analyze(rep):
    rows, derived = [], {}
    penv = rep.get("page_env") or {}
    simulate = bool(rep.get("meta", {}).get("simulate"))
    os_name = rep.get("meta", {}).get("os")

    # 1. isolation + timer resolution ---------------------------------------
    coi = penv.get("crossOriginIsolated")
    rows.append(row("coi", "Page is cross-origin isolated (crossOriginIsolated === true)", "§1.3",
                    UNKNOWN if coi is None else (PASS if coi else FAIL),
                    "crossOriginIsolated = %s" % coi))
    tm = rep.get("timer") or {}
    if tm.get("min") is not None:
        g = grid_ms(tm.get("samples") or [])
        st = PASS if tm["min"] <= 0.0055 else (FAIL if tm["min"] >= 0.09 else INFO)
        rows.append(row("timer_res", "performance.now() resolution is about 5 us", "§1.3", st,
                        "smallest step %.4f ms, median step %.4f ms, values on a %s ms grid" % (
                            tm["min"], tm["p50"], g), min_step_ms=tm["min"], grid_ms=g))
    else:
        rows.append(row("timer_res", "performance.now() resolution is about 5 us", "§1.3", UNKNOWN, "timer test did not run"))

    # 2. environment -----------------------------------------------------------
    chrome = rep.get("chrome") or {}
    raf_rows = (rep.get("raf") or {}).get("raf") or []
    period, iv = frame_period_ms(raf_rows)
    hz = 1000.0 / period if period else None
    derived["frame_period_ms"] = period
    uad = penv.get("uaData") or {}
    ver = next((b["version"] for b in uad.get("fullVersionList", [])
                if b.get("brand") in ("Google Chrome", "Chromium")), None) or chrome.get("version")
    sw = chrome.get("child_switches") or {}
    ozone = sw.get("--ozone-platform")
    envtxt = "Chrome %s; %s %s; dpr %s; refresh ~%s Hz (rAF median %s ms)%s" % (
        ver, uad.get("platform") or os_name, uad.get("platformVersion", ""), penv.get("dpr"),
        "%.1f" % hz if hz else "?", "%.3f" % period if period else "?",
        "; ozone %s" % ozone if ozone else "")
    rows.append(row("env", "Environment recorded (Chrome version, platform, refresh, DPR)", "§4.2",
                    INFO, envtxt, hz=hz, dpr=penv.get("dpr"), chrome=ver))
    if os_name == "linux":
        sess = ((rep.get("host") or {}).get("linux") or {}).get("XDG_SESSION_TYPE")
        if simulate:
            st, ev = UNKNOWN, "simulate/headless run; not meaningful"
        elif ozone and any(o.startswith("wayland") for o in ozone):
            st, ev = PASS, "Chrome child processes run with --ozone-platform=wayland; session %s" % sess
        elif ozone and any(o.startswith("x11") for o in ozone):
            st, ev = FAIL, "Chrome is using X11/XWayland (%s); session %s" % (ozone, sess)
        else:
            st, ev = UNKNOWN, "could not read Chrome's ozone platform; session %s" % sess
        rows.append(row("wayland", "Chrome runs natively on Wayland (not XWayland)", "§0.5", st, ev))

    # 3. Element Timing (no input) --------------------------------------------
    et = rep.get("et_test") or {}
    flips = et.get("flips") or []
    ents = et.get("entries") or []
    by_id = {}
    for e in ents:
        by_id.setdefault(e.get("identifier"), []).append(e)
    per_var = {}
    for v in ("A", "B", "C", "D"):
        fl = [f for f in flips if f["variant"] == v]
        got = sum(1 for f in fl if f["id"] in by_id)
        per_var[v] = {"flips": len(fl), "with_entry": got}
    per_var["B"]["entries_for_reused_span"] = len(by_id.get("et-B-reuse", []))
    derived["element_timing_variants"] = per_var

    input_keys = ((rep.get("page_input") or {}).get("keys")) or []
    input_ents = ((rep.get("page_input") or {}).get("entries")) or []
    in_by_id = {e.get("identifier"): e for e in input_ents}

    a_all = [(f, by_id[f["id"]][0]) for f in flips if f["variant"] == "A" and f["id"] in by_id]
    a_all += [(k["flip"], in_by_id[k["flip"]["id"]]) for k in input_keys
              if k.get("flip") and k["flip"]["id"] in in_by_id and not k.get("busy_ms")]
    n_a_flips = per_var["A"]["flips"] + sum(1 for k in input_keys if k.get("flip"))
    n_a_hit = per_var["A"]["with_entry"] + sum(1 for k in input_keys if k.get("flip") and k["flip"]["id"] in in_by_id)

    if not flips and not input_keys:
        rows.append(row("et_every_flip", "Element Timing reports every freshly inserted probe span", "§0.4, §1.1", UNKNOWN, "Element Timing test did not run"))
    else:
        frac = n_a_hit / n_a_flips if n_a_flips else 0
        rows.append(row("et_every_flip", "Element Timing reports every freshly inserted probe span", "§0.4, §1.1",
                        PASS if frac >= 0.95 else FAIL,
                        "%d of %d flips got an entry (%.0f%%)" % (n_a_hit, n_a_flips, 100 * frac),
                        flips=n_a_flips, entries=n_a_hit))

    fields_seen = sorted({k for _, e in a_all for k in ("renderTime", "paintTime", "presentationTime")
                          if e.get(k)})
    proto_el = (penv.get("proto") or {}).get("PerformanceElementTiming") or []
    has_pres_field = "presentationTime" in proto_el or any("presentationTime" in e for _, e in a_all)
    n_pres = sum(1 for _, e in a_all if e.get("presentationTime"))
    if not a_all:
        st, ev = UNKNOWN, "no Element Timing entries to inspect"
    elif not has_pres_field:
        st, ev = FAIL, ("PerformanceElementTiming has no presentationTime in this Chrome; fields with values: %s. "
                        "(Before Chrome 144/145 renderTime is the only end time.)" % fields_seen)
    else:
        f = n_pres / len(a_all)
        st = PASS if f >= 0.95 else FAIL
        ev = "presentationTime non-null on %d of %d entries; fields with values: %s" % (n_pres, len(a_all), fields_seen)
    rows.append(row("et_presentation", "Element Timing entries carry presentationTime (and paintTime)", "§0.3, §0.4", st, ev,
                    fields_with_values=fields_seen))

    # presentation vs paint, and in which frame the paint happened
    pp = [e["presentationTime"] - e["paintTime"] for _, e in a_all if e.get("presentationTime") and e.get("paintTime")]
    if pp and period:
        s = summary(pp)
        ok = s["min"] >= -0.01 and s["p95"] <= 2 * period + 0.5
        rows.append(row("et_present_minus_paint", "presentationTime - paintTime is 0 to 2 frames", "§1.1", PASS if ok else FAIL,
                        "%s = %.2f / %.2f frames at p50/p95" % (fmt(s), s["p50"] / period, s["p95"] / period), stats=s))
    else:
        rows.append(row("et_present_minus_paint", "presentationTime - paintTime is 0 to 2 frames", "§1.1", UNKNOWN,
                        "needs both paintTime and presentationTime"))

    def frame_offset(f, e, field):
        if f.get("raf_cb") is None or not e.get(field):
            return None
        return e[field] - f["raf_cb"]
    paint_field = "paintTime" if any(e.get("paintTime") for _, e in a_all) else None
    if paint_field and period:
        offs = [frame_offset(f, e, paint_field) for f, e in a_all]
        offs = [o for o in offs if o is not None]
        inside = sum(1 for o in offs if -0.05 <= o < period)
        rows.append(row("et_same_frame", "The probe's paint is in the same frame as the flip", "§0.4, §4.4",
                        PASS if offs and inside / len(offs) >= 0.95 else FAIL,
                        "paintTime - (rAF callback after the flip): %s; %d of %d inside one frame (%.2f ms)" % (
                            fmt(summary(offs)), inside, len(offs), period), stats=summary(offs)))
    else:
        rt = [frame_offset(f, e, "renderTime") for f, e in a_all]
        rt = [o for o in rt if o is not None]
        rows.append(row("et_same_frame", "The probe's paint is in the same frame as the flip", "§0.4, §4.4", UNKNOWN,
                        "no paintTime; renderTime - rAF callback after flip: %s" % fmt(summary(rt)),
                        render_minus_raf=summary(rt)))
    if period:
        pres_frames = [(e.get("presentationTime") or e.get("renderTime")) - f["raf_ts"] for f, e in a_all
                       if f.get("raf_ts") is not None and (e.get("presentationTime") or e.get("renderTime"))]
        derived["present_minus_flip_frame_begin_ms"] = summary(pres_frames)

    grids = {k: grid_ms([e.get(k) for _, e in a_all]) for k in ("renderTime", "paintTime", "presentationTime")}
    worst = [g for g in grids.values() if isinstance(g, (int, float))]
    if not a_all:
        st = UNKNOWN
    else:
        st = FAIL if any(g >= 1 for g in worst) else PASS
    rows.append(row("et_resolution", "Element Timing times are not coarsened to 4 ms (page is isolated)", "§0.4, §1.3", st,
                    "coarsest grid each field sits on: %s (None = finer than 5 us)" % grids, grids=grids))

    c = per_var["C"]
    rows.append(row("et_same_colour", "A same-colour (invisible) glyph still counts as contentful", "§4.4",
                    UNKNOWN if not c["flips"] else (PASS if c["with_entry"] >= 0.95 * c["flips"] else FAIL),
                    "%d of %d same-colour spans got an entry" % (c["with_entry"], c["flips"])))
    d = per_var["D"]
    rows.append(row("et_inline_span", "A plain inline <span elementtiming> gets NO entry (probe span must be inline-block/block)",
                    "§4.4", UNKNOWN if not d["flips"] else (PASS if d["with_entry"] == 0 else FAIL),
                    "%d of %d plain inline spans got an entry (inline-block spans: %d of %d)" % (
                        d["with_entry"], d["flips"], per_var["A"]["with_entry"], per_var["A"]["flips"])))
    b = per_var["B"]
    rows.append(row("et_reuse", "(info) Re-using one span and changing its text re-reports", "§1.1", INFO if b["flips"] else UNKNOWN,
                    "%d entries for %d text changes of one span -> %s" % (
                        b["entries_for_reused_span"], b["flips"],
                        "re-reports" if b["entries_for_reused_span"] > 1 else "reports once only: insert a new span per flip")))

    # 4-6. Input block -----------------------------------------------------------
    inj = rep.get("injector") or {}
    records = inj.get("records") or []
    pin = rep.get("page_input") or {}
    keys = pin.get("keys") or []
    sb, sa = clock_sync((rep.get("sync_before") or {}).get("samples")), clock_sync((rep.get("sync_after") or {}).get("samples"))
    sync = combine_sync(sb, sa)
    derived["sync_before"], derived["sync_after"], derived["sync"] = sb, sa, sync

    # Event Timing (needs keys; independent of injector clock)
    evs = [e for e in (pin.get("events") or []) if e.get("name") == "keydown"]
    proto_ev = (penv.get("proto") or {}).get("PerformanceEventTiming") or []
    if not keys:
        for rid, a in (("evt_start_eq_ts", "Event Timing startTime equals event.timeStamp"),
                       ("evt_duration_8ms", "Event Timing duration is rounded to 8 ms")):
            rows.append(row(rid, a, "§1.1", UNKNOWN, "no keys reached the page"))
    else:
        d_start = []
        for e in evs:
            k = min(keys, key=lambda k: abs(k["ts"] - e["startTime"]))
            d_start.append(e["startTime"] - k["ts"])
        eq = sum(1 for d in d_start if abs(d) <= 0.001)
        busy = sum(1 for k in keys if k.get("busy_ms"))
        rows.append(row("evt_start_eq_ts", "Event Timing startTime equals event.timeStamp", "§1.1",
                        UNKNOWN if not evs else (PASS if eq >= 0.95 * len(evs) else FAIL),
                        "%d keydown entries for %d slow (20 ms) keys; startTime == timeStamp for %d; diff %s" % (
                            len(evs), busy, eq, fmt(summary(d_start))), diff=summary(d_start)))
        durs = [e["duration"] for e in evs]
        mult8 = sum(1 for d in durs if abs(d / 8 - round(d / 8)) < 1e-6)
        rows.append(row("evt_duration_8ms", "Event Timing duration is rounded to 8 ms", "§0.3",
                        UNKNOWN if not durs else (PASS if mult8 == len(durs) else FAIL),
                        "%d of %d durations are multiples of 8 ms; values %s" % (mult8, len(durs), sorted(set(durs))[:12])))
    ev_has = [f for f in ("paintTime", "presentationTime") if f in proto_ev or any(f in e for e in evs)]
    rows.append(row("evt_no_presentation", "Event Timing has no paintTime/presentationTime yet", "§0.3",
                    UNKNOWN if not proto_ev and not evs else (PASS if not ev_has else FAIL),
                    ("PerformanceEventTiming exposes %s - the claim is outdated (good news: use it)" % ev_has) if ev_has
                    else "PerformanceEventTiming has neither field (as documented)"))

    # Clock sync
    if sync:
        ok = sb and sb["min_rtt_ms"] < 1.0 and (sync["drift_ms"] is None or abs(sync["drift_ms"]) < 0.2)
        rows.append(row("clock_sync", "Injector clock maps to page clock within 0.2 ms (min-RTT sync before/after)", "§3.4, §2.5",
                        PASS if ok else FAIL,
                        "%s: min RTT before %.3f ms / after %s ms; offset drift %s ms; error bound +-%.3f ms" % (
                            (rep.get("sync_before") or {}).get("transport", "?"),
                            sb["min_rtt_ms"] if sb else float("nan"),
                            "%.3f" % sa["min_rtt_ms"] if sa else "?",
                            "%.3f" % sync["drift_ms"] if sync["drift_ms"] is not None else "?", sync["err_ms"])))
    else:
        rows.append(row("clock_sync", "Injector clock maps to page clock within 0.2 ms", "§3.4", UNKNOWN, "no sync samples"))

    if not records or not sync:
        why = "no injected keys" if not records else "no clock sync"
        for rid, a, ref in (("keys_delivered", "Every injected key reached the focused page", "§3.3"),
                            ("os_delivery", "Injector -> event.timeStamp is non-negative and small (0.05-5 ms)", "§3.2"),
                            ("ts_semantics", "What event.timeStamp means on this OS", "§0.2"),
                            ("floor", "Injector -> marker presentation floor is measurable", "§1.1, §3.4"),
                            ("injector_jitter", "Injector timing jitter p99 < 0.2 ms", "§3.3")):
            rows.append(row(rid, a, ref, UNKNOWN, why + (" (%s)" % inj.get("error") if inj.get("error") else "")))
        return rows, derived

    off = sync["offset_ms"]
    pairs = match_trials(records, keys, off)
    got = [(r, k, t) for r, k, t in pairs if k]
    derived["n_injected"], derived["n_matched"] = len(records), len(got)
    lost = len(records) - len(got)
    aborted = inj.get("aborted")
    rows.append(row("keys_delivered", "Every injected key reached the focused page", "§3.3",
                    PASS if lost == 0 and not aborted and len(records) >= 20 else FAIL,
                    "%d injected, %d matched to page keydowns (by code and time)%s" % (
                        len(records), len(got), "; ABORTED (focus lost)" if aborted else ""),
                    injected=len(records), matched=len(got)))

    # A key's injector time is read right after write()/CGEventPost(). If the
    # injector was preempted around that call, the stamp is late: drop trials
    # whose before/after clock reads are more than 0.5 ms apart.
    def stamp_window_ms(r):
        before = r.get("t_before_post_ns") or r.get("t_wake_ns")
        return (r["t_down_ns"] - before) / 1e6 if before else 0.0
    n_all = len(got)
    got = [(r, k, t) for r, k, t in got if stamp_window_ms(r) <= 0.5]
    derived["n_dropped_stamp_window"] = n_all - len(got)
    derived["stamp_window_ms"] = summary([stamp_window_ms(r) for r in records], 4)
    dropped_note = "; %d trial(s) dropped: injector preempted while stamping" % (n_all - len(got)) if n_all > len(got) else ""
    deliv = [k["ts"] - t for _, k, t in got]
    s_d = summary(deliv)
    derived["os_delivery_ms"] = s_d
    tol = max(sync["err_ms"], 0.05)
    if s_d["n"]:
        ok = s_d["min"] >= -tol and 0.02 <= s_d["p50"] <= 5 and s_d["p99"] <= 20
        rows.append(row("os_delivery", "Injector -> event.timeStamp is non-negative and small (0.05-5 ms)", "§3.2, §3.4",
                        PASS if ok else FAIL, fmt(s_d) + dropped_note, stats=s_d))
    else:
        rows.append(row("os_delivery", "Injector -> event.timeStamp is non-negative and small", "§3.2", UNKNOWN, "no matched keys"))

    # What does event.timeStamp mean?
    if simulate:
        rows.append(row("ts_semantics", "What event.timeStamp means on this OS", "§0.2", INFO,
                        "simulate mode: CDP input is stamped when the DevTools message arrives; not an OS answer"))
    elif os_name == "linux":
        fr = [((k["ts"] + off) % 1.0) for _, k, _ in got]
        R = circular_R(fr)
        if R is None:
            st, ev = UNKNOWN, "no data"
        else:
            st = PASS if (R < 0.3 and s_d["min"] >= -tol) else (FAIL if R > 0.7 else UNKNOWN)
            ev = ("sub-ms phase of event.timeStamp on CLOCK_MONOTONIC: concentration R=%.2f (0=uniform -> Chrome's own "
                  "read time; 1=whole-ms -> compositor timestamp); min delivery %.3f ms" % (R, s_d["min"]))
        rows.append(row("ts_semantics", "Linux: event.timeStamp is Chrome's read time, not the compositor's ms timestamp",
                        "§0.2", st, ev, R=R))
    elif os_name == "darwin":
        g0 = [k["ts"] - t for r, k, t in got if not r.get("post_delay_ms")]
        g4 = [k["ts"] - t for r, k, t in got if r.get("post_delay_ms")]
        dmax = max([r.get("post_delay_ms") or 0 for r, _, _ in got] or [0])
        numer, denom = (inj.get("info") or {}).get("mach_timebase") or [1, 1]
        tb = numer / denom
        cg_vs_ts = []
        for r, k, _ in got:
            if r.get("cg_timestamp") and r.get("mach_at_create"):
                # CGEventTimestamp is documented as ns but is mach ticks on some
                # systems: decide by comparing with mach_absolute_time at creation.
                ratio = r["cg_timestamp"] / r["mach_at_create"]
                in_ticks = abs(ratio - 1) < abs(ratio - tb)
                cg_ns = r["cg_timestamp"] * tb if in_ticks else r["cg_timestamp"]
                derived["cg_timestamp_units"] = "mach ticks" if in_ticks and tb != 1 else "ns"
                cg_vs_ts.append(k["ts"] - (cg_ns / 1e6 - off))
        s_cg = summary(cg_vs_ts)
        if g0 and g4 and dmax:
            shift = quantile(g4, .5) - quantile(g0, .5)
            uses_os = shift < -0.6 * dmax
            verdict = ("event.timeStamp follows the OS event timestamp (CGEvent/NSEvent), not Chrome's read time"
                       if uses_os else
                       "event.timeStamp does not follow the event's creation time: Chrome stamps on read, "
                       "or the OS re-stamps at post")
            ev = ("posting %.0f ms after creating the event shifts (event.timeStamp - post time) by %.2f ms at p50 -> %s; "
                  "event.timeStamp - CGEvent timestamp: %s" % (dmax, shift, verdict, fmt(s_cg)))
            rows.append(row("ts_semantics", "macOS: does event.timeStamp come from the OS event (not Chrome's read time)?",
                            "§0.2 (macOS)", INFO, ev, shift_ms=shift, uses_os_timestamp=uses_os, ts_minus_cg=s_cg))
        else:
            rows.append(row("ts_semantics", "macOS: does event.timeStamp come from the OS event?", "§0.2", UNKNOWN, "no delayed-post keys"))

    # Floor: injector -> marker presentation, non-busy keys only
    lat, lat_field, lat_ev_to_present = [], None, []
    for r, k, t in got:
        if k.get("busy_ms") or not k.get("flip"):
            continue
        e = in_by_id.get(k["flip"]["id"])
        if not e:
            continue
        v, f = best_time(e)
        if v:
            lat.append(v - t)
            lat_ev_to_present.append(v - k["ts"])
            lat_field = lat_field or f
    s_l = summary(lat)
    derived["floor_ms"], derived["floor_field"] = s_l, lat_field
    derived["event_to_present_ms"] = summary(lat_ev_to_present)
    eligible = sum(1 for _, k, _ in got if not k.get("busy_ms") and k.get("flip"))
    if s_l["n"]:
        ok = s_l["n"] >= 0.9 * eligible and s_l["min"] > 0 and lat_field == "presentationTime"
        frames = " = %.2f / %.2f frames" % (s_l["p50"] / period, s_l["p95"] / period) if period else ""
        rows.append(row("floor", "Injector -> marker presentation floor is measurable", "§1.1, §3.4",
                        PASS if ok else (FAIL if s_l["min"] <= 0 else UNKNOWN),
                        "using %s: %s%s; of which event.timeStamp -> present %s" % (
                            lat_field, fmt(s_l), frames, fmt(summary(lat_ev_to_present))), stats=s_l, field=lat_field))
    else:
        rows.append(row("floor", "Injector -> marker presentation floor is measurable", "§1.1", UNKNOWN,
                        "no Element Timing entries for injected keys"))

    jit = [abs(r["t_wake_ns"] - r["planned_down_ns"]) / 1e6 for r in records if "t_wake_ns" in r]
    s_j = summary(jit, 4)
    rows.append(row("injector_jitter", "Injector timing jitter p99 < 0.2 ms", "§3.3",
                    UNKNOWN if not s_j["n"] else (PASS if s_j["p99"] < 0.2 else FAIL),
                    "|actual - planned| key-down: %s; scheduler: %s" % (fmt(s_j), (inj.get("info") or {}).get("sched")),
                    stats=s_j))
    return rows, derived


def render_table(rows, width=100):
    out = []
    for r in rows:
        out.append("%-8s %s  [%s]" % (r["status"], r["assumption"], r["ref"]))
        ev = r["evidence"]
        while ev:
            out.append("         " + ev[:width - 9])
            ev = ev[width - 9:]
    return "\n".join(out)
