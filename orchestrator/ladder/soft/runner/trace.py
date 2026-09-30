"""T sessions: Chrome tracing over CDP for stage attribution (phase-a §2.2, §2.6).

T-session numbers are never headline numbers (they have CDP attached and tracing on).
Collection: Tracing.start(transferMode=ReturnAsStream, streamFormat=proto) on the browser
target before a block's first measured segment, Tracing.end after the last, then IO.read the
stream into blocks/<id>/trace.pftrace.

Two config forms:
  "perfetto" (default, §2.2): a Perfetto TraceConfig proto, encoded here by hand (stdlib has
      no protobuf), passed base64 in `perfettoConfig`: track_event with only the §2.2 category
      list enabled, a 256 MB DISCARD buffer, plus org.chromium.trace_metadata.
  "json": the legacy traceConfig {includedCategories, excludedCategories:["*"]} with
      traceBufferSizeInKb, for a Chrome that rejects the proto form.
Field numbers used ([verify] against the pinned Chrome; perfetto protos/perfetto/config):
  TraceConfig.buffers=1 (BufferConfig.size_kb=1, fill_policy=4: DISCARD=2),
  TraceConfig.data_sources=2 (DataSource.config=1),
  DataSourceConfig.name=1, DataSourceConfig.track_event_config=113,
  TrackEventConfig.disabled_categories=1, enabled_categories=2.
Parsing (trace_processor, chrome_event_latencies, marker-frame join) is analysis-side:
ladder/soft/analysis/stages.py.
"""
from __future__ import annotations

import base64
import os

CATEGORIES = ["input", "cc", "benchmark", "viz", "gpu", "blink.user_timing", "toplevel"]
BUFFER_KB = 262144


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_varint(num, v):
    return _varint(num << 3 | 0) + _varint(v)


def _field_bytes(num, b):
    return _varint(num << 3 | 2) + _varint(len(b)) + b


def perfetto_config(categories=CATEGORIES, size_kb=BUFFER_KB) -> bytes:
    buf = _field_varint(1, size_kb) + _field_varint(4, 2)            # BufferConfig{size_kb, DISCARD}
    tec = _field_bytes(1, b"*") + b"".join(_field_bytes(2, c.encode()) for c in categories)
    ds_track = _field_bytes(1, _field_bytes(1, b"track_event") + _field_bytes(113, tec))
    ds_meta = _field_bytes(1, _field_bytes(1, b"org.chromium.trace_metadata"))
    return _field_bytes(1, buf) + _field_bytes(2, ds_track) + _field_bytes(2, ds_meta)


def start(cdp, form="perfetto"):
    params = {"transferMode": "ReturnAsStream", "streamFormat": "proto"}
    if form == "perfetto":
        params["perfettoConfig"] = base64.b64encode(perfetto_config()).decode()
    else:
        params["traceConfig"] = {"recordMode": "recordUntilFull", "traceBufferSizeInKb": BUFFER_KB,
                                 "includedCategories": CATEGORIES, "excludedCategories": ["*"]}
    cdp.send("Tracing.start", params)
    return {"form": form, "categories": CATEGORIES, "buffer_kb": BUFFER_KB}


def stop(cdp, out_path, timeout=300):
    cdp.send("Tracing.end")
    ev = cdp.wait_event("Tracing.tracingComplete", timeout)
    p = ev.get("params", {})
    handle = p.get("stream")
    info = {"data_loss": p.get("dataLossOccurred"), "stream_format": p.get("streamFormat"),
            "compression": p.get("streamCompression")}
    n = 0
    with open(out_path, "wb") as f:
        while handle:
            r = cdp.send("IO.read", {"handle": handle, "size": 1 << 20}, timeout=120)
            data = r.get("data", "")
            chunk = base64.b64decode(data) if r.get("base64Encoded") else data.encode("latin-1")
            f.write(chunk)
            n += len(chunk)
            if r.get("eof"):
                break
        if handle:
            cdp.send("IO.close", {"handle": handle})
    info["bytes"] = n
    info["path"] = os.path.basename(out_path)
    return info
