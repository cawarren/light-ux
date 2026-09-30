"""Local test server: serves the page cross-origin isolated, and a tiny command
channel so the Python side can drive the page without CDP attached.

  GET  /            page.html with COOP/COEP (crossOriginIsolated, 5 us timers)
  GET  /cmd         long-poll: the page asks for its next command
  POST /result      the page returns a command's result
  POST /sync        clock sync over fetch: server stamps now_ns() (fallback)
  GET  /ws          clock sync over a WebSocket (phase-a §3.4): each text
                    message is answered at once with {"t1": recv ns, "t2": send ns}
  POST /focus       page reports focus/blur (used to abort injection on blur)
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import struct
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import clock

HERE = os.path.dirname(os.path.abspath(__file__))
ISOLATION_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class PageChannel:
    def __init__(self):
        self.cmds = queue.Queue()
        self.results = {}
        self.cv = threading.Condition()
        self.ids = itertools.count(1)
        self.page_seen = threading.Event()
        self.focus_log = []          # [(now_ns, focused, reason)]
        self.on_blur = None          # callback set by the runner during injection

    def call(self, op, args=None, timeout=60.0):
        cid = next(self.ids)
        self.cmds.put({"id": cid, "op": op, "args": args or {}})
        with self.cv:
            ok = self.cv.wait_for(lambda: cid in self.results, timeout=timeout)
            if not ok:
                raise TimeoutError("page did not answer %r within %.0fs" % (op, timeout))
            res = self.results.pop(cid)
        if isinstance(res, dict) and "__error" in res:
            raise RuntimeError("page %s failed: %s" % (op, res["__error"]))
        return res


def make_server(channel: PageChannel, port: int = 0):
    with open(os.path.join(HERE, "page.html"), "rb") as f:
        page_bytes = f.read()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"   # keep-alive: clock-sync RTT stays small
        disable_nagle_algorithm = True  # else headers/body writes hit Nagle + delayed ACK (~40 ms)

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in ISOLATION_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _websocket_sync(self):
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            rf, sock = self.rfile, self.connection
            while True:
                head = rf.read(2)
                if len(head) < 2:
                    break
                t1 = clock.now_ns()
                opcode, n = head[0] & 0x0F, head[1] & 0x7F
                if n == 126:
                    n = struct.unpack("!H", rf.read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", rf.read(8))[0]
                mask = rf.read(4) if head[1] & 0x80 else b"\0\0\0\0"
                rf.read(n)  # payload content is not needed
                if opcode == 0x8:
                    break
                if opcode != 0x1:
                    continue
                t2 = clock.now_ns()
                body = json.dumps({"t1": str(t1), "t2": str(t2)}).encode()
                sock.sendall(bytes([0x81, len(body)]) + body)   # < 126 bytes, unmasked
            self.close_connection = True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                self._websocket_sync()
            elif path in ("/", "/index.html"):
                self._send(200, page_bytes, "text/html; charset=utf-8")
            elif path == "/cmd":
                channel.page_seen.set()
                try:
                    cmd = channel.cmds.get(timeout=10)
                except queue.Empty:
                    cmd = {"op": "noop"}
                self._send(200, json.dumps(cmd).encode())
            elif path == "/favicon.ico":
                self._send(204, b"")
            else:
                self._send(404, b"{}")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/sync":
                t = clock.now_ns()           # stamp first, before reading the body
                self._body()
                self._send(200, json.dumps({"t_ns": str(t)}).encode())
            elif path == "/result":
                msg = json.loads(self._body() or b"{}")
                with channel.cv:
                    channel.results[msg.get("id")] = msg.get("result")
                    channel.cv.notify_all()
                self._send(200, b"{}")
            elif path == "/focus":
                msg = json.loads(self._body() or b"{}")
                channel.focus_log.append((clock.now_ns(), bool(msg.get("focused")),
                                          msg.get("reason")))
                if not msg.get("focused") and channel.on_blur:
                    channel.on_blur(msg)
                self._send(200, b"{}")
            else:
                self._send(404, b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
