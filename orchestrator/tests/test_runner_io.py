import io
import json
import os
import unittest

import _util  # noqa: F401
from ladder.soft.runner import chrome, server, trace


class WebSocketTest(unittest.TestCase):
    def client_frame(self, payload, fin=True, opcode=1):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([(0x80 if fin else 0) | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        return head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))

    def test_roundtrip_sizes_and_fragments(self):
        for n in (1, 125, 126, 5000, 70000):
            payload = (b"x" * (n - 1)) + b"!"
            op, data, t = server.ws_read_message(io.BytesIO(self.client_frame(payload)))
            self.assertEqual((op, data), (1, payload))
            self.assertIsInstance(t, int)
        frames = self.client_frame(b"hel", fin=False) + self.client_frame(b"lo", opcode=0)
        self.assertEqual(server.ws_read_message(io.BytesIO(frames))[1], b"hello")
        self.assertIsNone(server.ws_read_message(io.BytesIO(b"")))

    def test_server_encode(self):
        for n in (3, 200, 70000):
            f = server.ws_encode(b"a" * n)
            self.assertEqual(f[0], 0x81)
            self.assertTrue(f.endswith(b"a" * n))

    def test_inject_collector(self):
        out = server.inject_collector(b"<html><head><title>x</title></HEAD><body></body></html>")
        self.assertIn(server.COLLECTOR_TAG + b"</HEAD>", out)
        self.assertTrue(server.inject_collector(b"<p>no head</p>").startswith(server.COLLECTOR_TAG))

    def test_accept_key(self):   # RFC 6455 §1.3 example
        self.assertEqual(server.ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class ChromeFlagsTest(unittest.TestCase):
    def test_m_session_has_no_devtools_port(self):
        f = chrome.build_flags("/tmp/p", "M", "fullscreen", simulate=False, headless=False, wayland=True)
        self.assertIn("--start-fullscreen", f)
        self.assertIn("--ozone-platform=wayland", f)
        self.assertFalse(any(x.startswith("--remote-debugging-port") for x in f))
        self.assertIn("--remote-debugging-port=0", chrome.build_flags("/tmp/p", "T", wayland=False))

    def test_denylist(self):
        for bad in ("--no-sandbox", "--disable-gpu-vsync", "--enable-automation", "--disable-features=IntensiveWakeUpThrottling",
                    "--js-flags=--expose-gc", "--some-unknown-flag", "--remote-debugging-port=9222"):
            with self.assertRaises(ValueError, msg=bad):
                chrome.flag_lint(["--user-data-dir=/x", bad], "M", simulate=False)

    def test_version_parse(self):
        self.assertEqual(chrome.major_of("Google Chrome 145.0.7632.45 "), 145)
        self.assertEqual(chrome.major_of("Chromium 141.0.7390.37"), 141)
        self.assertIsNone(chrome.major_of("garbage"))


class TraceConfigTest(unittest.TestCase):
    def decode(self, b):
        """Minimal protobuf reader: {field: [values]} (varint or bytes)."""
        out, i = {}, 0
        def varint():
            nonlocal i
            shift = v = 0
            while True:
                c = b[i]; i += 1
                v |= (c & 0x7F) << shift
                shift += 7
                if not c & 0x80:
                    return v
        while i < len(b):
            key = varint()
            num, wt = key >> 3, key & 7
            if wt == 0:
                out.setdefault(num, []).append(varint())
            else:
                n = varint()
                out.setdefault(num, []).append(b[i:i + n]); i += n
        return out

    def test_perfetto_config(self):
        cfg = self.decode(trace.perfetto_config())
        buf = self.decode(cfg[1][0])
        self.assertEqual(buf, {1: [trace.BUFFER_KB], 4: [2]})
        sources = [self.decode(self.decode(ds)[1][0]) for ds in cfg[2]]
        names = [s[1][0] for s in sources]
        self.assertEqual(names, [b"track_event", b"org.chromium.trace_metadata"])
        tec = self.decode(sources[0][113][0])
        self.assertEqual(tec[1], [b"*"])
        self.assertEqual([c.decode() for c in tec[2]], trace.CATEGORIES)


if __name__ == "__main__":
    unittest.main()
