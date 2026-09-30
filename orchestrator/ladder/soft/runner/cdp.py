# Vendored from tools/a1-check/a1lib/cdp.py at commit 6ca4bc9 (A1 on-device check).
# Keep in sync by hand; local changes are marked 'ladder:'.
"""Minimal Chrome DevTools Protocol client: RFC 6455 websocket over a plain socket.

Only what the check needs: send a command, wait for its response (ignoring
events), fire-and-forget sends, and flat sessions for page targets.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse


class CDPError(RuntimeError):
    pass


def ws_frame(payload: bytes, opcode: int = 0x1, mask_key: bytes | None = None) -> bytes:
    """Client->server frame: FIN set, always masked (RFC 6455 §5.3)."""
    mask_key = mask_key if mask_key is not None else os.urandom(4)
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
    return head + mask_key + masked


class CDP:
    def __init__(self, ws_url: str, timeout: float = 30.0):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake: connection closed")
            resp += chunk
        head, self._buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("websocket handshake failed: %r" % head[:200])
        self._id = 0
        self.events = []
        self._lock = threading.Lock()  # ladder: sends may come from the simulate injector thread

    # -- framing
    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise CDPError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv_message(self):
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if opcode == 0x9:        # ping
                self.sock.sendall(ws_frame(data, 0xA))
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise CDPError("websocket closed by peer")
            parts.append(data)
            if fin:
                return json.loads(b"".join(parts).decode())

    # -- protocol
    def send_nowait(self, method, params=None, session=None) -> int:
        with self._lock:
            self._id += 1
            mid = self._id
            msg = {"id": mid, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            self.sock.sendall(ws_frame(json.dumps(msg).encode()))
        return mid

    def send(self, method, params=None, session=None, timeout=30.0):
        mid = self.send_nowait(method, params, session)
        deadline = time.monotonic() + timeout
        self.sock.settimeout(timeout)
        while time.monotonic() < deadline:
            msg = self._recv_message()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            if "method" in msg and len(self.events) < 1000:
                self.events.append(msg)
        raise CDPError("timeout waiting for " + method)

    def wait_event(self, method, timeout=60.0):
        """ladder: wait for (or pop an already buffered) event by method name."""
        for i, ev in enumerate(self.events):
            if ev.get("method") == method:
                return self.events.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CDPError("timeout waiting for event " + method)
            self.sock.settimeout(left)
            msg = self._recv_message()
            if msg.get("method") == method:
                return msg
            if "method" in msg and len(self.events) < 1000:
                self.events.append(msg)

    def drain(self, seconds):
        """Read and discard messages (e.g. late acks) for a while."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            self.sock.settimeout(left)
            try:
                self._recv_message()
            except (socket.timeout, TimeoutError):
                return

    def attach_page(self, url_prefix=None):
        targets = self.send("Target.getTargets")["targetInfos"]
        pages = [t for t in targets if t["type"] == "page"
                 and (url_prefix is None or t["url"].startswith(url_prefix))]
        if not pages:
            raise CDPError("no page target found")
        r = self.send("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True})
        return r["sessionId"], pages[0]

    def close(self):
        try:
            self.sock.sendall(ws_frame(b"", 0x8))
        except OSError:
            pass
        self.sock.close()
