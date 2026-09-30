"""Harness server: serves each rung's production build cross-origin isolated, with the
harness-owned probe, marker config, dataset and page collector (phase-a §4.3).

Adapted from tools/a1-check/a1lib/server.py (COOP/COEP headers, keep-alive + no Nagle, the
WebSocket clock-sync reply stamped at frame receipt). Changes:
  - one local server (own port = own origin) per rung. The rung builds use absolute asset
    URLs (/assets/..., /ladder/probe.js, /dataset/...), so they cannot share one origin under
    /r/<token>/ prefixes as §4.3 sketches; opaque per-rung origins are the playground's job.
  - static dist/ for Vite/esbuild rungs; a reverse proxy to `next start` for r1-typical;
  - the collector script is injected into every HTML response before </head>;
  - /ladder/probe.js and /ladder/marker.json come from rungs/shared (harness copy), /dataset/*
    from dataset/out/<seed>/ (identical bytes for every rung), whatever the rung's dist holds;
  - one full-duplex WebSocket per page (/__ladder/ws) for commands, results, settle notices,
    focus reports and clock sync.
Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import itertools
import json
import mimetypes
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import clock

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR_PATH = os.path.join(HERE, "web", "collector.js")
ISOLATION_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
}
COLLECTOR_TAG = b'<script src="/__ladder/collector.js"></script>'
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
         ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml", ".ico": "image/x-icon",
         ".png": "image/png", ".wasm": "application/wasm", ".txt": "text/plain; charset=utf-8"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "content-encoding"}


def inject_collector(html: bytes) -> bytes:
    i = html.lower().find(b"</head>")
    if i < 0:
        return COLLECTOR_TAG + html
    return html[:i] + COLLECTOR_TAG + html[i:]


# ------------------------------------------------------------------ websocket

def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()


def ws_encode(payload: bytes, opcode: int = 0x1) -> bytes:
    """Server->client frame: FIN, unmasked (RFC 6455 §5.1)."""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 1 << 16:
        head = bytes([0x80 | opcode, 126]) + struct.pack("!H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack("!Q", n)
    return head + payload


def ws_read_message(rfile):
    """Read one (possibly fragmented) client message. Returns (opcode, payload, t_first_byte_ns)
    or None on EOF. Stamps the host clock as soon as the first header bytes arrive."""
    parts, first_op, t_rx = [], None, None
    while True:
        head = rfile.read(2)
        if len(head) < 2:
            return None
        if t_rx is None:
            t_rx = clock.now_ns()
        fin, op, masked, n = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x80, head[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", rfile.read(8))[0]
        mask = rfile.read(4) if masked else b"\0\0\0\0"
        data = rfile.read(n)
        if masked:
            data = bytes(b ^ mask[i & 3] for i, b in enumerate(data)) if n < 4096 else _unmask(data, mask)
        if op >= 0x8:          # control frames may interleave
            return op, data, t_rx
        if first_op is None:
            first_op = op
        parts.append(data)
        if fin:
            return first_op, b"".join(parts), t_rx


def _unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    m = int.from_bytes(mask * ((n + 3) // 4), "big") >> (8 * ((4 - n % 4) % 4))
    return (int.from_bytes(data, "big") ^ m).to_bytes(n, "big")


class PageLink:
    """The (single) connected page. Only one Chrome runs at a time; a new hello replaces
    the previous connection."""

    def __init__(self):
        self.conn = None
        self.conn_id = 0
        self.wlock = threading.Lock()
        self.cv = threading.Condition()
        self.results = {}
        self.ids = itertools.count(1)
        self.hello = None
        self.hello_evt = threading.Event()
        self.on_settled = None   # fn(t_rx_ns, n, seq)
        self.on_focus = None     # fn(msg)
        self.settle_log = []     # (t_rx_ns, n, seq)
        self.focus_log = []

    def reset(self):
        self.hello, self.results = None, {}
        self.hello_evt.clear()
        self.settle_log, self.focus_log = [], []

    def _send_raw(self, conn, data: bytes):
        with self.wlock:
            conn.sendall(data)

    def call(self, op, args=None, timeout=60.0):
        conn = self.conn
        if conn is None:
            raise RuntimeError("no page connected")
        cid = next(self.ids)
        self._send_raw(conn, ws_encode(json.dumps({"type": "cmd", "id": cid, "op": op, "args": args or {}}).encode()))
        with self.cv:
            if not self.cv.wait_for(lambda: cid in self.results, timeout=timeout):
                raise TimeoutError("page did not answer %r within %.0fs" % (op, timeout))
            msg = self.results.pop(cid)
        if msg.get("error"):
            raise RuntimeError("page op %s failed: %s" % (op, msg["error"]))
        return msg.get("result")

    def serve(self, handler):
        """Run the WebSocket loop for one page connection (in the handler thread)."""
        conn = handler.connection
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        rf = handler.rfile
        my_id = None
        while True:
            m = ws_read_message(rf)
            if m is None:
                break
            op, data, t1 = m
            if op == 0x8:
                break
            if op == 0x9:
                self._send_raw(conn, ws_encode(data, 0xA))
                continue
            if op != 0x1:
                continue
            if data == b"s":   # clock sync: answer at once (phase-a §3.4)
                t2 = clock.now_ns()
                self._send_raw(conn, ws_encode(json.dumps({"type": "sync", "t1": str(t1), "t2": str(t2)}).encode()))
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "hello":
                self.conn_id += 1
                my_id = self.conn_id
                self.conn = conn
                self.hello = dict(msg, t_rx_ns=t1)
                self.hello_evt.set()
                continue
            if my_id is None or my_id != self.conn_id:
                continue   # a stale page from a previous block
            if kind == "result":
                with self.cv:
                    self.results[msg.get("id")] = msg
                    self.cv.notify_all()
            elif kind == "settled":
                self.settle_log.append((t1, msg.get("n"), msg.get("seq")))
                cb = self.on_settled
                if cb:
                    cb(t1, int(msg.get("n", -1)), int(msg.get("seq", -1)))
            elif kind == "focus":
                self.focus_log.append(dict(msg, t_rx_ns=t1))
                cb = self.on_focus
                if cb:
                    cb(msg)
        if self.conn is conn:
            self.conn = None


# ------------------------------------------------------------------ rung server

class RungSite:
    """What one rung server serves. Either `root` (a static build dir) or `proxy` ((host, port))."""

    def __init__(self, rung, root=None, proxy=None, probe_path=None, marker_path=None, dataset_files=None):
        self.rung, self.root, self.proxy = rung, root, proxy
        self.probe_path, self.marker_path = probe_path, marker_path
        self.dataset_files = dataset_files or {}   # "/dataset/10k/items.json" -> path
        self.requests = 0


def make_server(site: RungSite, link: PageLink, host="127.0.0.1", port=0):
    with open(COLLECTOR_PATH, "rb") as f:
        collector = f.read()
    fixed = {"/__ladder/collector.js": (lambda: collector, TYPES[".js"])}

    def file_bytes(p):
        with open(p, "rb") as f:
            return f.read()

    if site.probe_path:
        fixed["/ladder/probe.js"] = (lambda: file_bytes(site.probe_path), TYPES[".js"])
    if site.marker_path:
        fixed["/ladder/marker.json"] = (lambda: file_bytes(site.marker_path), TYPES[".json"])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/octet-stream", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in ISOLATION_HEADERS.items():
                self.send_header(k, v)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _ws(self):
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_accept(self.headers.get("Sec-WebSocket-Key", "")))
            self.end_headers()
            self.wfile.flush()
            link.serve(self)
            self.close_connection = True

        def _static(self, path):
            rel = path.lstrip("/") or "index.html"
            if rel.endswith("/"):
                rel += "index.html"
            full = os.path.realpath(os.path.join(site.root, rel))
            root = os.path.realpath(site.root)
            if not (full == root or full.startswith(root + os.sep)):
                return self._send(403, b"forbidden", "text/plain")
            if not os.path.isfile(full):
                if "." not in os.path.basename(rel):      # SPA fallback
                    full = os.path.join(root, "index.html")
                else:
                    return self._send(404, b"not found", "text/plain")
            body = file_bytes(full)
            ext = os.path.splitext(full)[1]
            ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ext == ".html":
                body = inject_collector(body)
            self._send(200, body, ctype)

        def _proxy(self):
            host, port = site.proxy
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != "host"}
            headers["Accept-Encoding"] = "identity"
            headers["Host"] = "%s:%d" % (host, port)
            c = http.client.HTTPConnection(host, port, timeout=120)
            try:
                c.request(self.command, self.path, body=body, headers=headers)
                r = c.getresponse()
                data = r.read()
                ctype = r.getheader("Content-Type", "application/octet-stream")
                if "text/html" in ctype:
                    data = inject_collector(data)
                extra = {k: v for k, v in r.getheaders()
                         if k.lower() not in HOP and k.lower() not in ("content-type", "cache-control")
                         and k.lower() not in (h.lower() for h in ISOLATION_HEADERS)}
                self._send(r.status, data, ctype, extra)
            finally:
                c.close()

        def do_GET(self):
            site.requests += 1
            path = self.path.split("?", 1)[0]
            if path == "/__ladder/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                return self._ws()
            if path in fixed:
                fn, ctype = fixed[path]
                return self._send(200, fn(), ctype)
            if path in site.dataset_files:
                return self._send(200, file_bytes(site.dataset_files[path]), TYPES[".json"])
            if path == "/favicon.ico" and site.root and not os.path.exists(os.path.join(site.root, "favicon.ico")):
                return self._send(204, b"")
            if site.proxy:
                return self._proxy()
            return self._static(path)

        do_HEAD = do_GET

        def do_POST(self):
            if site.proxy:
                return self._proxy()
            self._send(405, b"", "text/plain")

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
