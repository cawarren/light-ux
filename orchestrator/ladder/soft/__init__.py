"""Phase A software measurement harness (docs/phase-a/README.md).

  runner/    runs on the device under test during measurement; standard library only
  timing.py  clock mapping, attribution, per-trial extraction (stdlib; used by both sides)
  manifest.py session manifest schema + validator (stdlib)
  analysis/  trials table, correctness, statistics, Gate A, HTML report (stdlib; optional
             pyarrow for Parquet, optional perfetto for T-session stage parsing)
"""
