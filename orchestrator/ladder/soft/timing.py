"""Clock mapping, key->event matching, coalesced-key attribution and per-trial timing
extraction (phase-a §3.4, §4.5, §4.6). Standard library only: the runner uses it for its
preflight checks on the device under test, the analysis for trials.csv.

Clock convention (as in tools/a1-check/a1lib/analysis.py, from which clock_sync and
combine_sync are copied): host times are ns on the injector/server clock
(CLOCK_MONOTONIC on Linux, CLOCK_UPTIME_RAW on macOS); page times are ms on the page clock
(performance.now(), event.timeStamp, PerformanceEntry times). The sync gives offset_ms with
    host_ms = page_ms + offset_ms,    so    t_input_page = t_inject_down_ns / 1e6 - offset_ms.
"""
from __future__ import annotations

import math

CLOCK_DRIFT_LIMIT_MS = 0.2       # phase-a §3.4: flag a segment whose before/after offsets differ more
MATCH_EARLY_TOL_MS = 2.0         # a keydown may not precede its injection by more than this
MATCH_WINDOW_MS = 1500.0         # nor follow it by more than this
PRESENT_FIELDS = ("presentationTime", "paintTime", "renderTime")   # phase-a §0.4, fallbacks flagged

STATUS_ORDER = ("no_event", "clock_flag", "no_probe_entry", "timeout", "wrong_result", "coalesced", "ok")
LATENCY_STATUSES = ("ok", "coalesced", "wrong_result", "timeout")


def quantile(xs, q):
    """Hyndman-Fan type 7 (numpy default)."""
    xs = sorted(xs)
    if not xs:
        return None
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


# ------------------------------------------------------------------ clock sync

def clock_sync(samples):
    """samples: [[t0_page_ms, t1_server_ns, t2_server_ns, t3_page_ms], ...] (ns may be strings).
    NTP: offset = ((t1 - t0) + (t2 - t3)) / 2, delay = (t3 - t0) - (t2 - t1); min-delay sample."""
    rows = []
    for smp in samples or []:
        t0, t1, t2, t3 = smp[0], int(smp[1]) / 1e6, int(smp[2]) / 1e6, smp[3]
        rtt = (t3 - t0) - (t2 - t1)
        rows.append((rtt, ((t1 - t0) + (t2 - t3)) / 2))
    if not rows:
        return None
    rows.sort()
    best = rows[0]
    return {"n": len(rows), "min_rtt_ms": best[0], "offset_ms": best[1], "err_ms": best[0] / 2,
            "rtt_p50_ms": quantile([r for r, _ in rows], .5)}


def combine_sync(before, after):
    if before and after:
        return {"offset_ms": (before["offset_ms"] + after["offset_ms"]) / 2,
                "drift_ms": after["offset_ms"] - before["offset_ms"],
                "err_ms": max(before["err_ms"], after["err_ms"]) + abs(after["offset_ms"] - before["offset_ms"]) / 2}
    one = before or after
    if one:
        return {"offset_ms": one["offset_ms"], "drift_ms": None, "err_ms": one["err_ms"]}
    return None


def to_page_ms(t_ns, offset_ms):
    return t_ns / 1e6 - offset_ms


# ------------------------------------------------------------------ matching

def match_keydowns(injected, keydowns, offset_ms):
    """injected: [(i, key, t_down_ns)] in order; keydowns: probe input records (type keydown).
    Returns {i: keydown_record}. The page buffers are reset when a segment is armed, so when the
    counts and the key sequence agree, the k-th keydown is the k-th injected key. Otherwise
    (lost or stray keys) an in-order greedy match on key identity and a time window."""
    if len(injected) == len(keydowns) and all(kd.get("key") == key for (_, key, _), kd in zip(injected, keydowns)):
        return {i: kd for (i, _, _), kd in zip(injected, keydowns)}
    out, j = {}, 0
    for i, key, t_ns in injected:
        t_page = to_page_ms(t_ns, offset_ms)
        for idx in range(j, len(keydowns)):
            kd = keydowns[idx]
            if kd["t"] < t_page - MATCH_EARLY_TOL_MS:
                continue
            if kd["t"] > t_page + MATCH_WINDOW_MS:
                break
            if kd.get("key") == key:
                out[i] = kd
                j = idx + 1
                break
    return out


def attribute_flips(keys, t_key, flips):
    """Coalesced-key attribution (phase-a §4.5, found building R3).

    keys:  plan keys of ONE unit, in order, each with "i" and "expect" (input value after it)
    t_key: {i: page time of that key (keydown timeStamp, or mapped injection time)}
    flips: probe flip records {seq, query, t, ...}, any order
    Each key k is attributed to the first flip f (in time) at or after t_key[k] whose query is
    the state after some key j >= k of the same unit that was already pressed (t_key[j] <= f.t).
    j == k: the key's own flip. j > k: the key was coalesced into a later query. Returns
    {i: (flip, offset j-k)} for keys that got a flip.
    """
    fl = sorted(flips, key=lambda f: f["t"])
    out = {}
    for a, k in enumerate(keys):
        tk = t_key.get(k["i"])
        if tk is None:
            continue
        for f in fl:
            if f["t"] < tk:
                continue
            hit = None
            for b in range(a, len(keys)):
                tj = t_key.get(keys[b]["i"])
                if tj is None or tj > f["t"]:
                    break
                if keys[b]["expect"] == f["query"]:
                    hit = b
                    break
            if hit is not None:
                out[k["i"]] = (f, hit - a)
                break
    return out


def present_time(entry):
    """(value, source field) of the marker probe's presentation, with the §0.4 fallbacks."""
    if not entry:
        return None, None
    for f in PRESENT_FIELDS:
        v = entry.get(f)
        if v:
            return v, f
    return None, None


def frame_period_ms(frames):
    iv = [b["raf"] - a["raf"] for a, b in zip(frames, frames[1:]) if b["frame"] == a["frame"] + 1]
    return quantile(iv, .5) if iv else None


def trigger_phase(t, frames, period):
    """Phase (0..1) of time t within the rAF frame grid (03 §5.5 aliasing check)."""
    if not frames or not period:
        return None
    prev = None
    for fr in frames:
        if fr["raf"] <= t:
            prev = fr
        else:
            break
    if prev is None:
        return None
    return ((t - prev["raf"]) / period) % 1.0


def worst_status(statuses):
    for s in STATUS_ORDER:
        if s in statuses:
            return s
    return "ok"


# ------------------------------------------------------------------ extraction

def extract_segment(raw, meta=None):
    """Trial rows (one per injected key-down) plus the segment's flips, from one raw segment
    file written by the runner. `meta` adds constant columns (session, rung build, hz...)."""
    meta = meta or {}
    inj = raw.get("injector") or {}
    records = inj.get("records") or []
    keys = {k["i"]: k for k in raw["keys"]}
    col = raw.get("collect") or {}
    snap = col.get("snapshot") or {}
    sb, sa = clock_sync((raw.get("sync_before") or {}).get("samples")), clock_sync((raw.get("sync_after") or {}).get("samples"))
    sync = combine_sync(sb, sa)
    offset = sync["offset_ms"] if sync else None
    clock_bad = sync is None or (sync["drift_ms"] is not None and abs(sync["drift_ms"]) > CLOCK_DRIFT_LIMIT_MS)

    keydowns = [x for x in snap.get("inputs", []) if x.get("type") == "keydown"]
    injected = [(r["i"], keys[r["i"]]["key"], r["t_down_ns"]) for r in records]
    matched = match_keydowns(injected, keydowns, offset) if offset is not None else {}
    flips = snap.get("flips", [])
    elements = {e.get("identifier"): e for e in snap.get("entries", {}).get("element", [])}
    events = [e for e in snap.get("entries", {}).get("event", []) if e.get("name") == "keydown"]
    frames = snap.get("frames", [])
    period = frame_period_ms(frames)

    # page time of every injected key: keydown timeStamp, else the mapped injection time
    t_key = {}
    for i, _, t_ns in injected:
        if i in matched:
            t_key[i] = matched[i]["t"]
        elif offset is not None:
            t_key[i] = to_page_ms(t_ns, offset)
    # attribution per unit
    units = {}
    for r in records:
        units.setdefault(keys[r["i"]]["unit"], []).append(keys[r["i"]])
    # A flip whose probe span was replaced before any paint (several flips inside one
    # rendering opportunity, e.g. R1 draining queued keystrokes) has no Element Timing entry:
    # its state was never on screen. Keys are therefore attributed to the first PRESENTED flip
    # whose query includes them; `own_flip_unpainted` marks keys whose own flip was skipped.
    presented = [f for f in flips if present_time(elements.get("ladder-flip-%d" % f["seq"]))[0] is not None]
    attributed, attributed_any = {}, {}
    for ukeys in units.values():
        uk = sorted(ukeys, key=lambda k: k["i"])
        attributed.update(attribute_flips(uk, t_key, presented))
        attributed_any.update(attribute_flips(uk, t_key, flips))

    # settle timeouts: the settle wait after key k is recorded on key k+1 (or in "final")
    rec_by_i = {r["i"]: r for r in records}
    order = [r["i"] for r in records]
    settle_to = set()
    for a, i in enumerate(order):
        nxt = rec_by_i[order[a + 1]] if a + 1 < len(order) else None
        if nxt is not None and nxt.get("ref") == "settle_timeout":
            settle_to.add(i)
        if nxt is None and (inj.get("final") or {}).get("ref") == "settle_timeout":
            settle_to.add(i)

    rows = []
    for r in records:
        k = keys[r["i"]]
        kd = matched.get(r["i"])
        row = dict(meta)
        row.update({
            "block_id": raw.get("block_id"), "seg": raw.get("seg"), "attempt": raw.get("attempt", 0),
            "rung": raw.get("rung"), "dataset_size": raw.get("size"), "scenario": "A",
            "action_kind": k["kind"], "direction": k["dir"], "seq_pos": k["seq_pos"], "unit": k["unit"],
            "key_idx": k["i"], "key": k["key"], "query_prefix": k["expect"], "warmup": bool(k["warmup"]),
            "t_inject_down_ns": r["t_down_ns"], "t_inject_up_ns": r.get("t_up_ns"),
            "planned_pre_delay_us": round(k["gap_ms"] * 1000.0, 3),
            "actual_pre_delay_us": round((r["t_down_ns"] - r["t_ref_ns"]) / 1e3, 3),
            "inject_late_us": round((r["t_down_ns"] - r["planned_down_ns"]) / 1e3, 3),
            "pre_delay_ref": r.get("ref"),
            "clock_offset_ns": None if offset is None else round(offset * 1e6),
            "clock_offset_err_ns": None if sync is None else round(sync["err_ms"] * 1e6),
            "clock_drift_ns": None if not sync or sync["drift_ms"] is None else round(sync["drift_ms"] * 1e6),
        })
        statuses = set()
        t_in = to_page_ms(r["t_down_ns"], offset) if offset is not None else None
        row["t_input_page_ms"] = t_in
        row["t_event_ms"] = kd["t"] if kd else None
        row["frame_idx_event"] = kd.get("frame") if kd else None
        if kd is None:
            statuses.add("no_event")
        et = None
        if kd:
            et = next((e for e in events if abs(e["startTime"] - kd["t"]) < 0.01), None)
        row.update({"et_start": et and et["startTime"], "et_processing_start": et and et["processingStart"],
                    "et_processing_end": et and et["processingEnd"], "et_duration": et and et["duration"],
                    "interaction_id": et and et.get("interactionId")})
        att = attributed.get(r["i"])
        any_att = attributed_any.get(r["i"])
        own_unpainted = bool(any_att and any_att[1] == 0 and (att is None or att[1] > 0))
        if att is None and any_att is not None:
            att = any_att                    # a flip exists but none presented: no_probe_entry below
        flip, off = (att if att else (None, None))
        entry = elements.get("ladder-flip-%d" % flip["seq"]) if flip else None
        t_present, src = present_time(entry)
        # Chromium 141's renderTime (the fallback) can precede the flip task that inserted the
        # span (it looks like the start of a long frame). Clamp to the flip time, a lower bound,
        # and flag it: dropping these trials would drop exactly R1's slow tail.
        clamped = bool(flip and t_present is not None and t_present < flip["t"])
        if clamped:
            t_present, src = flip["t"], src + "+clamped"
        row.update({
            "flip_seq": flip and flip["seq"], "flip_query": flip and flip["query"], "flip_count": flip and flip.get("count"),
            "flip_digest": flip and flip.get("digest"), "attributed_offset": off, "coalesced": bool(off),
            "own_flip_unpainted": own_unpainted, "present_clamped": clamped,
            "t_flip_task_ms": flip and flip["t"], "frame_idx_flip": flip and flip.get("frame"),
            "t_marker_paint_ms": entry.get("paintTime") if entry else None,
            "t_marker_present_ms": entry.get("presentationTime") if entry else None,
            "t_marker_render_ms": entry.get("renderTime") if entry else None,
            "t_present_used_ms": t_present, "t_present_source": src,
        })
        row["frames_between"] = (row["frame_idx_flip"] - row["frame_idx_event"]) if (flip and kd) else None
        if flip is None:
            statuses.add("timeout")
        elif t_present is None:
            statuses.add("no_probe_entry")
        if off:
            statuses.add("coalesced")
        if r["i"] in settle_to:
            statuses.add("timeout")
        row["settle_timeout"] = r["i"] in settle_to
        if clock_bad:
            statuses.add("clock_flag")
        lat = lambda a, b: (a - b) if (a is not None and b is not None) else None
        row["lat_present_ms"] = lat(t_present, t_in)
        row["lat_os_delivery_ms"] = lat(row["t_event_ms"], t_in)
        row["lat_input_delay_ms"] = lat(row["et_processing_start"], row["et_start"])
        row["lat_processing_ms"] = lat(row["et_processing_end"], row["et_processing_start"])
        row["lat_to_flip_ms"] = lat(row["t_flip_task_ms"], row["t_event_ms"])
        row["lat_flip_to_present_ms"] = lat(t_present, row["t_flip_task_ms"])
        row["trigger_phase"] = trigger_phase(t_in, frames, period) if t_in is not None else None
        row["frame_period_ms"] = period
        row["status"] = worst_status(statuses)
        row["correct"] = None
        row["correctness_level"] = None
        rows.append(row)
    seg_info = {"sync": sync, "sync_before": sb, "sync_after": sa, "clock_flag": clock_bad, "frame_period_ms": period,
                "n_injected": len(records), "n_keydowns": len(keydowns), "n_flips": len(flips),
                "aborted": bool(inj.get("aborted")), "frames_buffer_full": len(frames) >= 8192,
                "present_sources": sorted({row["t_present_source"] for row in rows if row["t_present_source"]})}
    return rows, flips, seg_info
