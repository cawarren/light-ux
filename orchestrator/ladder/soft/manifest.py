"""Session manifest (phase-a §4.6, a Phase A variant of 03 §4.4) and its validator.

The 03 plan uses pydantic; the runner has to stay standard-library only on the device under
test, so the schema is a small declarative table checked by `validate()`. Unknown extra keys
are allowed (forward compatible); missing required keys and wrong types are errors.
"""
from __future__ import annotations

SCHEMA_VERSION = "phase-a-soft/1"

# key -> (type or tuple of types, required)
_TOP = {
    "schema_version": (str, True),
    "session_id": (str, True),
    "session_kind": (str, True),          # M | T (P and playground are other tools)
    "rig": (str, True),                   # "none" in Phase A
    "input_source": (str, True),          # uinput | quartz | cdp-simulated
    "simulated": (bool, True),
    "machine_id": (str, True),
    "display": (dict, True),
    "started_at": (str, True),
    "ended_at": ((str, type(None)), True),
    "seeds": (dict, True),
    "block_order": (list, True),
    "provenance": (dict, True),
    "chrome": (dict, True),
    "injector": (dict, True),
    "env": (dict, True),
    "params": (dict, True),
    "blocks": (list, True),
    "files": (dict, True),
    "notes": ((str, type(None)), False),
    "not_real_warning": ((str, type(None)), False),
}
_DISPLAY = {"nominal_hz": ((int, float), True), "mode": (str, True), "measured_hz": ((int, float, type(None)), False)}
_BLOCK = {
    "block_id": (str, True), "rung": (str, True), "size": (str, True), "rep": (int, True),
    "rung_build_id": (str, True), "health": (str, True), "counts": (dict, True),
    "segments": (list, True), "flags": (list, True),
}
_KINDS = {"M", "T"}
_HEALTH = {"valid", "unhealthy", "aborted", "refused", "error"}


def _check(obj, spec, where, errs):
    if not isinstance(obj, dict):
        errs.append("%s: expected object" % where)
        return
    for k, (typ, req) in spec.items():
        if k not in obj:
            if req:
                errs.append("%s.%s: missing" % (where, k))
            continue
        if not isinstance(obj[k], typ) or (typ is int and isinstance(obj[k], bool)):
            errs.append("%s.%s: wrong type %s" % (where, k, type(obj[k]).__name__))


def validate(m) -> list:
    """Return a list of problems (empty when valid)."""
    errs = []
    _check(m, _TOP, "manifest", errs)
    if errs:
        return errs
    if m["schema_version"] != SCHEMA_VERSION:
        errs.append("manifest.schema_version: %r != %r" % (m["schema_version"], SCHEMA_VERSION))
    if m["session_kind"] not in _KINDS:
        errs.append("manifest.session_kind: %r not in %s" % (m["session_kind"], sorted(_KINDS)))
    if m["simulated"] != (m["input_source"] == "cdp-simulated"):
        errs.append("manifest: simulated must be true exactly when input_source is cdp-simulated")
    if m["simulated"] and "NOT REAL" not in (m.get("not_real_warning") or ""):
        errs.append("manifest.not_real_warning: simulated sessions must say NOT REAL")
    _check(m["display"], _DISPLAY, "manifest.display", errs)
    ids = []
    for i, b in enumerate(m["blocks"]):
        _check(b, _BLOCK, "manifest.blocks[%d]" % i, errs)
        if isinstance(b, dict):
            ids.append(b.get("block_id"))
            if b.get("health") not in _HEALTH:
                errs.append("manifest.blocks[%d].health: %r" % (i, b.get("health")))
    missing = [x for x in m["block_order"] if x not in ids]
    if m["ended_at"] is not None and missing:
        errs.append("manifest.block_order: blocks without a record: %s" % missing)
    for path, digest in m["files"].items():
        if not (isinstance(digest, str) and digest.startswith("sha256:") and len(digest) == 71):
            errs.append("manifest.files[%s]: bad digest" % path)
    return errs
