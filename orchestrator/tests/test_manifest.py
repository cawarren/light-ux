import copy
import unittest

import _util  # noqa: F401
from ladder.soft import manifest

GOOD = {
    "schema_version": manifest.SCHEMA_VERSION, "session_id": "01ABC", "session_kind": "M", "rig": "none",
    "input_source": "uinput", "simulated": False, "not_real_warning": None, "machine_id": "x1",
    "display": {"nominal_hz": 60, "mode": "maximized", "measured_hz": 59.98},
    "started_at": "2026-10-01T10:00:00+00:00", "ended_at": "2026-10-01T11:00:00+00:00",
    "seeds": {"session": 1}, "block_order": ["b01_r1-vite_50k"], "provenance": {}, "chrome": {"version": "145"},
    "injector": {}, "env": {}, "params": {},
    "blocks": [{"block_id": "b01_r1-vite_50k", "rung": "r1-vite", "size": "50k", "rep": 0, "rung_build_id": "sha256:x",
                "health": "valid", "counts": {"ok": 1}, "segments": [], "flags": []}],
    "files": {"plan.json": "sha256:" + "0" * 64},
}


class ManifestTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(manifest.validate(GOOD), [])

    def test_missing_and_types(self):
        m = copy.deepcopy(GOOD)
        del m["seeds"]
        m["display"]["nominal_hz"] = "60"
        m["blocks"][0]["rep"] = True
        errs = manifest.validate(m)
        self.assertTrue(any("seeds" in e for e in errs))
        m = copy.deepcopy(GOOD)
        m["display"]["nominal_hz"] = "60"
        m["blocks"][0]["rep"] = True
        errs = manifest.validate(m)
        self.assertTrue(any("nominal_hz" in e for e in errs))
        self.assertTrue(any("rep" in e for e in errs))

    def test_simulated_must_say_not_real(self):
        m = copy.deepcopy(GOOD)
        m["simulated"] = True
        self.assertTrue(manifest.validate(m))                       # input_source mismatch
        m["input_source"] = "cdp-simulated"
        self.assertTrue(any("NOT REAL" in e for e in manifest.validate(m)))
        m["not_real_warning"] = "SIMULATED - NOT REAL"
        self.assertEqual(manifest.validate(m), [])

    def test_kind_health_digest_order(self):
        m = copy.deepcopy(GOOD)
        m["session_kind"] = "X"
        m["blocks"][0]["health"] = "great"
        m["files"]["x"] = "md5:1"
        m["block_order"].append("b02_missing")
        errs = manifest.validate(m)
        self.assertEqual(len(errs), 4, errs)
        m["ended_at"] = None                                         # partial manifest: order may run ahead
        self.assertEqual(len(manifest.validate(m)), 3)


if __name__ == "__main__":
    unittest.main()
