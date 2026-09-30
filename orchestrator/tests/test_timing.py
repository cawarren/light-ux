import unittest

import _util  # noqa: F401
from ladder.soft import timing


def sync_samples(offset_ms, rtts, asym=0.0, t0=1000.0):
    """Synthetic NTP samples: host_ms = page_ms + offset_ms. Each sample's request leg takes
    rtt/2 + asym, the reply leg rtt/2 - asym (asymmetry is what min-RTT bounds)."""
    out = []
    for k, rtt in enumerate(rtts):
        p0 = t0 + 10 * k
        t1 = p0 + rtt / 2 + asym + offset_ms          # host clock, ms
        t2 = t1 + 0.01
        p3 = (t2 - offset_ms) + rtt / 2 - asym
        out.append([p0, str(int(round(t1 * 1e6))), str(int(round(t2 * 1e6))), p3])
    return out


class ClockTest(unittest.TestCase):
    def test_offset_from_min_rtt(self):
        s = sync_samples(123456.789, [0.9, 0.3, 2.5, 0.31, 1.2])
        c = timing.clock_sync(s)
        self.assertAlmostEqual(c["min_rtt_ms"], 0.3, places=4)
        self.assertAlmostEqual(c["offset_ms"], 123456.789, places=4)
        self.assertAlmostEqual(c["err_ms"], 0.15, places=4)

    def test_asymmetry_bounded_by_half_rtt(self):
        s = sync_samples(50.0, [0.4] * 5, asym=0.1)
        c = timing.clock_sync(s)
        self.assertLessEqual(abs(c["offset_ms"] - 50.0), c["err_ms"] + 1e-9)

    def test_combine_and_drift(self):
        b = timing.clock_sync(sync_samples(10.0, [0.3]))
        a = timing.clock_sync(sync_samples(10.5, [0.3]))
        c = timing.combine_sync(b, a)
        self.assertAlmostEqual(c["offset_ms"], 10.25, places=6)
        self.assertAlmostEqual(c["drift_ms"], 0.5, places=6)
        self.assertGreater(abs(c["drift_ms"]), timing.CLOCK_DRIFT_LIMIT_MS)
        self.assertIsNone(timing.combine_sync(None, None))
        self.assertIsNone(timing.combine_sync(b, None)["drift_ms"])

    def test_mapping(self):
        # injector at host 5,000,000,000 ns; host = page + 4000 ms -> page 1000 ms
        self.assertAlmostEqual(timing.to_page_ms(5_000_000_000, 4000.0), 1000.0)


def keys_for(text):
    """A.seq-like unit: type text then backspace it all."""
    out, v, i = [], "", 0
    for k in list(text) + ["Backspace"] * len(text):
        v = v[:-1] if k == "Backspace" else v + k
        out.append({"i": i, "key": k, "expect": v, "unit": "u0"})
        i += 1
    return out


class AttributionTest(unittest.TestCase):
    def test_own_flips(self):
        keys = keys_for("ab")
        t = {0: 0.0, 1: 100.0, 2: 200.0, 3: 300.0}
        flips = [{"seq": s + 1, "query": k["expect"], "t": t[k["i"]] + 20} for s, k in enumerate(keys)]
        a = timing.attribute_flips(keys, t, flips)
        self.assertEqual({i: (f["seq"], off) for i, (f, off) in a.items()}, {0: (1, 0), 1: (2, 0), 2: (3, 0), 3: (4, 0)})

    def test_coalesced_forward(self):
        # "a","ab","abc": the rung skipped "ab" (one query in flight) and flipped "abc" later
        keys = keys_for("abc")[:3]
        t = {0: 0.0, 1: 100.0, 2: 200.0}
        flips = [{"seq": 1, "query": "a", "t": 150.0}, {"seq": 2, "query": "abc", "t": 400.0}]
        a = timing.attribute_flips(keys, t, flips)
        self.assertEqual(a[0][0]["seq"], 1)
        self.assertEqual((a[1][0]["seq"], a[1][1]), (2, 1))      # coalesced into "abc"
        self.assertEqual((a[2][0]["seq"], a[2][1]), (2, 0))

    def test_stale_flip_not_attributed_to_later_key(self):
        # key 1 ("ab") is pressed while the rung still paints "a": that late "a" flip must not
        # be matched to the backspace that later produces "a" again (it was not pressed yet).
        keys = keys_for("ab")
        t = {0: 0.0, 1: 10.0, 2: 500.0, 3: 600.0}
        flips = [{"seq": 1, "query": "a", "t": 50.0}, {"seq": 2, "query": "ab", "t": 90.0},
                 {"seq": 3, "query": "a", "t": 520.0}, {"seq": 4, "query": "", "t": 620.0}]
        a = timing.attribute_flips(keys, t, flips)
        self.assertEqual(a[1][0]["seq"], 2)
        self.assertEqual(a[2][0]["seq"], 3)

    def test_no_flip_is_missing(self):
        keys = keys_for("a")[:1]
        self.assertEqual(timing.attribute_flips(keys, {0: 0.0}, [{"seq": 1, "query": "zz", "t": 5}]), {})
        self.assertEqual(timing.attribute_flips(keys, {0: 10.0}, [{"seq": 1, "query": "a", "t": 5}]), {})

    def test_worst_status(self):
        self.assertEqual(timing.worst_status({"ok", "coalesced"}), "coalesced")
        self.assertEqual(timing.worst_status({"timeout", "clock_flag"}), "clock_flag")
        self.assertEqual(timing.worst_status(set()), "ok")


def raw_segment(present_field="presentationTime", paint_second=True, drift_ms=0.0):
    """Two isolated trials ('a' + clear); second key's own flip coalesced with nothing."""
    off = 4000.0                                   # host = page + off (ms)
    keys = [
        {"i": 0, "kind": "A.first_key", "dir": "fwd", "seq_pos": None, "key": "a", "expect": "a", "unit": "u0",
         "warmup": False, "after": "settle", "gap_ms": 100.0, "hold_ms": 40.0},
        {"i": 1, "kind": "A.clear", "dir": "back", "seq_pos": None, "key": "Backspace", "expect": "", "unit": "u0",
         "warmup": False, "after": "settle", "gap_ms": 120.0, "hold_ms": 40.0},
    ]
    ns = lambda page_ms: int(round((page_ms + off) * 1e6))
    records = [
        {"i": 0, "key": "a", "ref": "lead_in", "t_ref_ns": ns(900.0), "planned_down_ns": ns(1000.0), "t_down_ns": ns(1000.05),
         "t_up_ns": ns(1040.0)},
        {"i": 1, "key": "Backspace", "ref": "settle", "t_ref_ns": ns(1100.0), "planned_down_ns": ns(1220.0),
         "t_down_ns": ns(1220.02), "t_up_ns": ns(1260.0)},
    ]
    el = [{"identifier": "ladder-flip-7", present_field: 1030.0, "renderTime": 1025.0}]
    if paint_second:
        el.append({"identifier": "ladder-flip-8", present_field: 1250.0, "renderTime": 1245.0})
    snap = {"inputs": [{"type": "keydown", "t": 1000.5, "key": "a", "frame": 60},
                       {"type": "input", "t": 1000.6, "key": None, "frame": 60},
                       {"type": "keydown", "t": 1220.4, "key": "Backspace", "frame": 73}],
            "flips": [{"seq": 7, "query": "a", "t": 1010.0, "frame": 60, "count": 3, "top50": []},
                      {"seq": 8, "query": "", "t": 1230.0, "frame": 74, "count": 9, "top50": []}],
            "frames": [{"frame": 59 + k, "raf": 983.0 + 16.0 * k} for k in range(40)],
            "entries": {"element": el, "event": [{"name": "keydown", "startTime": 1000.5, "processingStart": 1001.5,
                                                  "processingEnd": 1009.0, "duration": 32, "interactionId": 5}]}}
    return {"block_id": "b01", "seg": 0, "rung": "r3-no-framework", "size": "10k", "keys": keys,
            "injector": {"records": records, "aborted": False, "final": {"ref": "settle"}},
            "sync_before": {"samples": sync_samples(off, [0.3, 0.5])},
            "sync_after": {"samples": sync_samples(off + drift_ms, [0.3, 0.5])},
            "collect": {"snapshot": snap}}


class ExtractTest(unittest.TestCase):
    def test_latency_and_splits(self):
        rows, flips, info = timing.extract_segment(raw_segment())
        r0, r1 = rows
        self.assertEqual((r0["status"], r1["status"]), ("ok", "ok"))
        self.assertAlmostEqual(r0["lat_present_ms"], 1030.0 - 1000.05, places=3)
        self.assertAlmostEqual(r0["lat_os_delivery_ms"], 0.45, places=3)
        self.assertAlmostEqual(r0["lat_to_flip_ms"], 9.5, places=6)
        self.assertAlmostEqual(r0["lat_flip_to_present_ms"], 20.0, places=6)
        self.assertAlmostEqual(r0["lat_input_delay_ms"], 1.0, places=6)
        self.assertEqual(r0["t_present_source"], "presentationTime")
        self.assertEqual(r0["frames_between"], 0)
        self.assertAlmostEqual(r0["trigger_phase"], ((1000.05 - 999.0) / 16.0) % 1, places=3)
        self.assertAlmostEqual(r1["lat_present_ms"], 1250.0 - 1220.02, places=3)
        self.assertEqual(info["present_sources"], ["presentationTime"])

    def test_fallback_is_flagged(self):
        rows, _, info = timing.extract_segment(raw_segment(present_field="paintTime"))
        self.assertEqual(rows[0]["t_present_source"], "paintTime")
        rs = raw_segment(present_field="other")
        rows, _, _ = timing.extract_segment(rs)
        self.assertEqual(rows[0]["t_present_source"], "renderTime")

    def test_unpainted_flip_is_no_probe_entry(self):
        rows, _, _ = timing.extract_segment(raw_segment(paint_second=False))
        self.assertEqual(rows[1]["status"], "no_probe_entry")
        self.assertIsNone(rows[1]["lat_present_ms"])

    def test_clock_drift_flags(self):
        rows, _, info = timing.extract_segment(raw_segment(drift_ms=0.5))
        self.assertTrue(info["clock_flag"])
        self.assertEqual({r["status"] for r in rows}, {"clock_flag"})

    def test_lost_key(self):
        raw = raw_segment()
        raw["collect"]["snapshot"]["inputs"] = raw["collect"]["snapshot"]["inputs"][:2]
        rows, _, _ = timing.extract_segment(raw)
        self.assertEqual(rows[1]["status"], "no_event")

    def test_settle_timeout_marks_previous_key(self):
        raw = raw_segment()
        raw["injector"]["records"][1]["ref"] = "settle_timeout"
        rows, _, _ = timing.extract_segment(raw)
        self.assertTrue(rows[0]["settle_timeout"])
        self.assertEqual(rows[0]["status"], "timeout")
        self.assertIsNotNone(rows[0]["lat_present_ms"])       # latency kept when a flip presented



class MatchTest(unittest.TestCase):
    def test_index_match_when_sequences_agree(self):
        inj = [(0, "Backspace", 10_000_000), (1, "Backspace", 20_000_000)]
        kds = [{"key": "Backspace", "t": 7.0}, {"key": "Backspace", "t": 20.2}]   # first stamp "early" (simulate GIL)
        m = timing.match_keydowns(inj, kds, 0.0)
        self.assertEqual((m[0]["t"], m[1]["t"]), (7.0, 20.2))

    def test_time_match_skips_lost_key(self):
        inj = [(0, "a", 10_000_000), (1, "b", 110_000_000), (2, "c", 210_000_000)]
        kds = [{"key": "a", "t": 10.3}, {"key": "c", "t": 210.4}]
        m = timing.match_keydowns(inj, kds, 0.0)
        self.assertEqual(sorted(m), [0, 2])


if __name__ == "__main__":
    unittest.main()
