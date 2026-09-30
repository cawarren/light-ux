"""`ladder soft stages SESSION`: T-session traces -> derived/stages.csv (phase-a §2.3).

Optional dependency: the `perfetto` Python package (`uv sync --extra trace`), whose
TraceProcessor downloads the trace_processor_shell binary on first use.

Done here: one row per EventLatency from the stdlib table `chrome_event_latencies`
(ts, dur, event_type, is_presented, presentation_timestamp, vsync interval), plus the
duration of every descendant stage slice pivoted into columns (§2.1 names), and the
`ladder:flip` user-timing marks for reference.
TODO [verify, A1/A2 on the pinned Chrome]:
  - the marker-frame join (§2.4): frame_sequence / surface_frame_trace_id join key between the
    `ladder:flip` mark's BeginMainFrame and the presented frame, to get t_present_marker in
    trace time and cross-check Element Timing presentationTime (agree within 1 ms);
  - which of RawKeyDown / Char carries the EventLatency of the input's frame;
  - the clock domain (Perfetto may rebase onto CLOCK_BOOTTIME; read clock_snapshot) and the
    join of stages to trials by key order and injector time.
"""
from __future__ import annotations

import csv
import glob
import json
import os

EL_SQL = """
INCLUDE PERFETTO MODULE chrome.event_latency;
SELECT id, ts, dur, event_type, is_presented, presentation_timestamp, vsync_interval_ms
FROM chrome_event_latencies
WHERE event_type IN ('KEY_PRESSED', 'KEY_RELEASED', 'CHAR')
ORDER BY ts
"""
STAGE_SQL = """
SELECT d.name AS name, d.dur AS dur, d.depth AS depth
FROM descendant_slice({id}) d
"""
MARK_SQL = "SELECT ts, name FROM slice WHERE name = 'ladder:flip' ORDER BY ts"


def main(session_dir):
    try:
        from perfetto.trace_processor import TraceProcessor
    except ImportError:
        print("perfetto is not installed: `cd orchestrator && uv sync --extra trace` "
              "(or pip install perfetto), then rerun. Traces stay in blocks/*/trace.pftrace.")
        return 2
    traces = sorted(glob.glob(os.path.join(session_dir, "blocks", "*", "trace.pftrace")))
    if not traces:
        print("no traces in %s (T sessions only: ladder soft run --trace)" % session_dir)
        return 2
    out_rows, stage_names, marks = [], set(), {}
    for path in traces:
        bid = os.path.basename(os.path.dirname(path))
        tp = TraceProcessor(trace=path)
        try:
            els = list(tp.query(EL_SQL))
            for e in els:
                row = {"block_id": bid, "event_latency_id": e.id, "ts": e.ts, "dur": e.dur, "event_type": e.event_type,
                       "is_presented": e.is_presented, "presentation_timestamp": e.presentation_timestamp,
                       "vsync_interval_ms": e.vsync_interval_ms}
                for s in tp.query(STAGE_SQL.format(id=e.id)):
                    key = "stage:" + s.name
                    if key not in row:            # first (shallowest) occurrence
                        row[key] = s.dur
                        stage_names.add(key)
                out_rows.append(row)
            marks[bid] = [m.ts for m in tp.query(MARK_SQL)]
        finally:
            tp.close()
    out = os.path.join(session_dir, "derived")
    os.makedirs(out, exist_ok=True)
    cols = ["block_id", "event_latency_id", "ts", "dur", "event_type", "is_presented", "presentation_timestamp",
            "vsync_interval_ms"] + sorted(stage_names)
    with open(os.path.join(out, "stages.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    with open(os.path.join(out, "flip_marks.json"), "w") as f:
        json.dump(marks, f)
    print("stages: %d EventLatency rows, %d stage columns -> %s" % (len(out_rows), len(stage_names), out))
    print("TODO [verify]: marker-frame join and trial join (see ladder/soft/analysis/stages.py)")
    return 0
