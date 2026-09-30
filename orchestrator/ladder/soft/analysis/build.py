"""`ladder soft analyze`: raw session -> derived/trials.csv (+ trials.parquet if pyarrow is
installed), derived/segments.json and derived/summary.json (L1 of 03 §4.1; regenerable,
deletable). Standard library only (pyarrow optional)."""
from __future__ import annotations

import csv
import glob
import json
import os

from .. import timing
from . import correctness

TRIAL_COLUMNS = [
    "validity", "session_id", "session_kind", "simulated", "input_source", "machine_id", "chrome_version",
    "block_id", "seg", "attempt", "rung", "rung_variant", "rung_build_id", "dataset_size", "hz", "measured_hz",
    "display_mode", "scenario", "action_kind", "direction", "seq_pos", "unit", "key_idx", "key", "query_prefix",
    "warmup", "t_inject_down_ns", "t_inject_up_ns", "planned_pre_delay_us", "actual_pre_delay_us", "inject_late_us",
    "pre_delay_ref", "clock_offset_ns", "clock_offset_err_ns", "clock_drift_ns", "t_input_page_ms", "t_event_ms",
    "et_start", "et_processing_start", "et_processing_end", "et_duration", "interaction_id",
    "flip_seq", "flip_query", "flip_count", "flip_digest", "attributed_offset", "coalesced", "own_flip_unpainted", "present_clamped",
    "t_flip_task_ms", "t_marker_paint_ms", "t_marker_present_ms", "t_marker_render_ms", "t_present_used_ms",
    "t_present_source", "frame_idx_event", "frame_idx_flip", "frames_between", "frame_period_ms",
    "lat_present_ms", "lat_os_delivery_ms", "lat_input_delay_ms", "lat_processing_ms", "lat_to_flip_ms",
    "lat_flip_to_present_ms", "status", "settle_timeout", "trigger_phase",
    "correct", "correctness_level", "correctness_reason",
]
FLOAT_COLS = {c for c in TRIAL_COLUMNS if c.startswith(("t_", "lat_", "et_", "clock_", "planned_", "actual_", "inject_"))
              } - {"t_present_source"} | {"hz", "measured_hz", "frame_period_ms", "trigger_phase"}
INT_COLS = {"seg", "attempt", "seq_pos", "key_idx", "interaction_id", "flip_seq", "flip_count", "attributed_offset",
            "frame_idx_event", "frame_idx_flip", "frames_between", "t_inject_down_ns", "t_inject_up_ns",
            "clock_offset_ns", "clock_offset_err_ns", "clock_drift_ns"}
BOOL_COLS = {"simulated", "warmup", "coalesced", "own_flip_unpainted", "present_clamped", "settle_timeout", "correct"}


def load_manifest(session_dir):
    for name in ("manifest.json", "manifest.partial.json"):
        p = os.path.join(session_dir, name)
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    raise FileNotFoundError("no manifest in %s" % session_dir)


def analyze_session(session_dir, check_correctness=True, verbose=False):
    m = load_manifest(session_dir)
    blocks = {b["block_id"]: b for b in m["blocks"]}
    base = {
        "validity": "SIMULATED-NOT-REAL" if m["simulated"] else "real",
        "session_id": m["session_id"], "session_kind": m["session_kind"], "simulated": m["simulated"],
        "input_source": m["input_source"], "machine_id": m["machine_id"],
        "chrome_version": (m.get("chrome") or {}).get("version"), "hz": m["display"]["nominal_hz"],
        "display_mode": m["display"]["mode"],
    }
    rows, flips_by_seg, segs = [], {}, []
    for bdir in sorted(glob.glob(os.path.join(session_dir, "blocks", "*"))):
        bid = os.path.basename(bdir)
        b = blocks.get(bid, {})
        meta = dict(base, rung_build_id=b.get("rung_build_id"), measured_hz=b.get("measured_hz"),
                    rung_variant=b.get("rung"))
        for sp in sorted(glob.glob(os.path.join(bdir, "seg-*.json"))):
            with open(sp) as f:
                raw = json.load(f)
            r, fl, info = timing.extract_segment(raw, meta)
            rows += r
            flips_by_seg[(raw["block_id"], raw["seg"])] = fl
            info = {k: v for k, v in info.items()}
            info.update({"block_id": bid, "seg": raw["seg"], "rung": raw["rung"], "size": raw["size"],
                         "block_health": b.get("health")})
            segs.append(info)
    summary = {"session_id": m["session_id"], "simulated": m["simulated"], "rows": len(rows)}
    if check_correctness and rows:
        ds = os.path.join(correctness.REPO, "dataset", "out", m["provenance"].get("dataset_seed_id", "dev-1"))
        summary["correctness"] = correctness.check_rows(rows, flips_by_seg, ds)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary["status_counts"] = counts
    out = os.path.join(session_dir, "derived")
    os.makedirs(out, exist_ok=True)
    write_csv(os.path.join(out, "trials.csv"), rows)
    summary["parquet"] = write_parquet(os.path.join(out, "trials.parquet"), rows)
    with open(os.path.join(out, "segments.json"), "w") as f:
        json.dump(segs, f, indent=1, default=str)
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    if verbose:
        print("%s: %d trials %s%s -> %s" % (m["session_id"], len(rows), json.dumps(counts),
                                             "  [SIMULATED - NOT REAL]" if m["simulated"] else "", out))
        if "correctness" in summary:
            c = summary["correctness"]
            print("   correctness (%s): %d checked, %d wrong, %d unchecked, levels %s" % (
                c["impl"], c["checked"], c["wrong"], c["unchecked"], c["by_level"]))
    return rows, segs, m, summary


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRIAL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in TRIAL_COLUMNS})


def write_parquet(path, rows):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return "skipped (pyarrow not installed; `uv sync --extra parquet`)"
    cols = {}
    for c in TRIAL_COLUMNS:
        vals = [r.get(c) for r in rows]
        if c in BOOL_COLS:
            typ = pa.bool_()
        elif c in INT_COLS:
            typ = pa.int64()
        elif c in FLOAT_COLS:
            typ = pa.float64()
            vals = [None if v is None else float(v) for v in vals]
        else:
            typ = pa.string()
            vals = [None if v is None else str(v) for v in vals]
        cols[c] = pa.array(vals, type=typ)
    pq.write_table(pa.table(cols), path)
    return "written"


def read_csv(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k, v in list(r.items()):
                if v == "":
                    r[k] = None
                elif k in BOOL_COLS:
                    r[k] = v == "True"
                elif k in INT_COLS:
                    r[k] = int(float(v))
                elif k in FLOAT_COLS:
                    r[k] = float(v)
            rows.append(r)
    return rows
