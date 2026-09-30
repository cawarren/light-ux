"""Playground HTTP server (stdlib `http.server`), docs/phase-a/README.md §4.3 and §5.

One origin serves the playground UI, every rung's static production build, the dataset, the probe
and a small JSON API. Rungs are served under neutral per-interval paths `/t/<random token>/`, so
neither the URL nor the title reveals which rung is loaded. The rung builds use root-absolute asset
paths (`/assets/…`, `/worker.js`), so those are resolved against the rung of the token that loaded
the page (from the Referer when it carries the token, else the most recently loaded rung page; only
one rung page is live at a time). Every response is cross-origin isolated (COOP/COEP/CORP) and
`no-store`, so each interval loads the same way.
"""
from __future__ import annotations

import json
import mimetypes
import random
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config as C
from .results import Session, list_sessions

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/json", ".map")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")

TOKEN_RE = re.compile(r"^/t/([0-9a-f]{16})(/.*)?$")
INJECT = C.STATIC / "inject.js"


class State:
    def __init__(self, results_dir: Path, defaults: dict):
        self.lock = threading.Lock()
        self.results_dir = Path(results_dir)
        self.defaults = defaults
        self.tokens: dict[str, dict] = {}
        self.active_rung: str | None = None
        self.sessions: dict[str, Session] = {}
        self.rng = random.Random()

    # ----------------------------------------------------------------- tokens
    def token(self, rung: str, *, mode: str = "pass", n: int = 0, enter: str = "end", edit: str = "all",
              marker: str = "show") -> str:
        tok = secrets.token_hex(8)
        with self.lock:
            self.tokens[tok] = {"rung": rung, "mode": mode, "n": int(n), "enter": enter, "edit": edit, "marker": marker}
            if len(self.tokens) > 5000:  # bounded memory in very long sessions
                for k in list(self.tokens)[:1000]:
                    del self.tokens[k]
        return tok

    def rung_url(self, tok: str, size: str) -> str:
        return f"/t/{tok}/?items=/dataset/{size}/items.json"

    def session(self, sid: str) -> Session:
        with self.lock:
            if sid not in self.sessions:
                path = self.results_dir / f"{sid}.jsonl"
                if not re.fullmatch(r"[a-z]+-[0-9T]+-[0-9a-f]+", sid) or not path.is_file():
                    raise KeyError(sid)
                self.sessions[sid] = Session(path)
            return self.sessions[sid]

    @staticmethod
    def interval_opts(s: Session) -> dict:
        c = s.config
        # Sessions written before these options existed get the current defaults.
        return {"enter": "end", "edit": "all" if c.get("allow_editing", s.kind == "jnd") else "none",
                "marker": "hide" if c.get("marker_hidden", True) else "show"}

    def interval_tokens(self, s: Session, t: dict) -> list[str]:
        o = self.interval_opts(s)
        if s.kind == "blind":
            return [self.token(r, mode="pass", **o) for r in t["rungs"]]
        mode = s.config["delay_mode"]
        return [self.token(r, mode=mode, n=n, **o) for r, n in zip(t["rungs"], t["added_ms"])]


def rewrite_rung_html(html: str) -> str:
    """Neutralize the rung page: same title for all, no comments, no favicon link, no rung-named ids
    in the static markup, and the playground helper as the very first script."""
    html = re.sub(r"<!--.*?-->\s*", "", html, flags=re.S)
    html = re.sub(r"<title>.*?</title>", "<title>Palette</title>", html, count=1, flags=re.S | re.I)
    html = re.sub(r"<link[^>]*rel=\"icon\"[^>]*>\s*", "", html, flags=re.I)
    html = re.sub(r"\br\d-(list|opt)\b", r"pal-\1", html)
    return re.sub(r"<head>", '<head>\n    <script src="__i.js"></script>', html, count=1, flags=re.I)


def make_handler(state: State, verbose: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Playground"
        sys_version = ""

        def log_message(self, fmt, *args):
            if verbose:
                super().log_message(fmt, *args)

        # ------------------------------------------------------------- responses
        def _send(self, code: int, body: bytes, ctype: str, head: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _file(self, path: Path, head: bool = False):
            if not path.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json",):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype, head)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode() or "{}")

        # ------------------------------------------------------------- GET
        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head: bool = False):
            url = urlsplit(self.path)
            p = url.path
            if p in ("/", "/index.html"):
                return self._file(C.STATIC / "index.html", head)
            if p.startswith("/__play/"):
                name = p[len("/__play/"):]
                if name in ("app.js", "app.css", "favicon.svg"):
                    return self._file(C.STATIC / name, head)
                return self._send(404, b"not found", "text/plain")
            if p.startswith("/api/"):
                return self._api_get(p, parse_qs(url.query))
            m = TOKEN_RE.match(p)
            if m:
                return self._rung_page(m.group(1), m.group(2) or "/", head)
            if p in ("/ladder/probe.js", "/ladder/marker.json"):
                return self._file(C.SHARED / ("ladder-probe.js" if p.endswith(".js") else "marker.json"), head)
            if p.startswith("/dataset/"):
                d = C.dataset_dir()
                rel = p[len("/dataset/"):]
                if d is None or not re.fullmatch(r"((1k|10k|50k)/)?items\.json|queries\.json", rel):
                    return self._send(404, b"no dataset: run node rungs/scripts/prepare.mjs", "text/plain")
                return self._file(d / rel, head)
            return self._rung_asset(p, head)

        def _rung_page(self, tok: str, rest: str, head: bool):
            info = state.tokens.get(tok)
            if info is None:
                return self._send(404, b"unknown or expired interval", "text/plain")
            rung = C.RUNGS[info["rung"]]
            if rest == "/__i.js":
                cfg = {k: info[k] for k in ("mode", "n", "enter", "edit", "marker")}
                body = f"window.__PLAY_CFG__={json.dumps(cfg)};\n".encode() + INJECT.read_bytes()
                return self._send(200, body, "text/javascript; charset=utf-8", head)
            if rest != "/":
                return self._rung_file(rung, rest, head)
            with state.lock:
                state.active_rung = info["rung"]
            html = rewrite_rung_html((rung.dist / "index.html").read_text())
            self._send(200, html.encode(), "text/html; charset=utf-8", head)

        def _rung_asset(self, p: str, head: bool):
            ref = urlsplit(self.headers.get("Referer") or "").path
            m = TOKEN_RE.match(ref)
            rid = state.tokens.get(m.group(1), {}).get("rung") if m else None
            rid = rid or state.active_rung
            if rid is None:
                return self._send(404, b"not found", "text/plain")
            return self._rung_file(C.RUNGS[rid], p, head)

        def _rung_file(self, rung: C.Rung, p: str, head: bool):
            root = rung.dist.resolve()
            f = (root / p.lstrip("/")).resolve()
            if root not in f.parents or f.name == "index.html":
                return self._send(404, b"not found", "text/plain")
            return self._file(f, head)

        # ------------------------------------------------------------- API
        def _api_get(self, p: str, qs: dict):
            if p == "/api/state":
                sessions = []
                for s in list_sessions(state.results_dir):
                    pr = s.progress()
                    if not pr["finished"]:
                        sessions.append({"id": s.id, "kind": s.kind, "size": s.config["dataset_size"], **pr,
                                         "created_at": s.header["created_at"]})
                return self._json({
                    "rungs": [{"id": r.id, "label": r.label, "available": r.available} for r in C.RUNGS.values()],
                    "sizes": list(C.SIZES), "dataset": C.dataset_dir() is not None,
                    "defaults": state.defaults, "sessions": sessions[-9:],
                    "jnd_levels": list(C.JND_LEVELS)})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            p = urlsplit(self.path).path
            try:
                body = self._body()
                return self._api_post(p, body)
            except KeyError as e:
                return self._json({"error": f"unknown: {e}"}, 404)
            except (ValueError, TypeError) as e:
                return self._json({"error": str(e)}, 400)

        def _api_post(self, p: str, b: dict):
            d = state.defaults
            if p == "/api/open":
                rung = b.get("rung", "r3")
                if rung not in C.RUNGS or not C.RUNGS[rung].available:
                    raise ValueError(f"rung {rung} not built")
                size = b.get("size", d["size"])
                n = int(b.get("added_ms") or 0)
                mode = b.get("mode") or d["delay_mode"]
                tok = state.token(rung, mode=mode if n > 0 else "pass", n=n, enter="pass", edit="all", marker="show")
                return self._json({"url": state.rung_url(tok, size), "rung": rung,
                                   "label": C.RUNGS[rung].label, "size": size, "added_ms": n,
                                   "mode": mode if n > 0 else "pass"})
            if p == "/api/sessions":
                kind = b.get("kind")
                need = {r for pair in C.BLIND_PAIRS for r in pair} if kind == "blind" else {"r3"}
                missing = [r for r in need if not C.RUNGS[r].available]
                if missing:
                    raise ValueError(f"rungs not built: {', '.join(missing)} (see playground/README.md)")
                s = Session.create(
                    state.results_dir, kind, seed=b.get("seed") or d.get("seed"), size=b.get("size") or d["size"],
                    trials_per_pair=int(b.get("trials_per_pair") or d["trials_per_pair"]),
                    method=b.get("method") or d["jnd_method"], reps=int(b.get("reps") or d["jnd_reps"]),
                    delay_mode=b.get("mode") or d["delay_mode"], levels=d.get("jnd_levels"),
                    allow_no_difference=bool(d["allow_no_difference"]), break_every=int(d["break_every"]),
                    allow_editing=bool(d.get("allow_backspace")), marker_hidden=not d.get("show_marker"),
                    timing={k: d[k] for k in ("ready_ms", "settle_ms", "post_ms")})
                with state.lock:
                    state.sessions[s.id] = s
                return self._json(self._session_info(s))
            m = re.fullmatch(r"/api/sessions/([^/]+)/(open|next|warmup|answer)", p)
            if not m:
                raise KeyError(p)
            s = state.session(m.group(1))
            action = m.group(2)
            if action == "open":  # a new sitting (resume)
                with state.lock:
                    if s.trials or getattr(s, "_opened", False):
                        s.sitting = max((t.get("sitting", 1) for t in s.trials.values()), default=0) + 1
                    s._opened = True
                return self._json(self._session_info(s))
            if action == "warmup":
                rungs = sorted({r for pair in (s.config.get("pairs") or [["r3"]]) for r in pair})
                state.rng.shuffle(rungs)
                o = state.interval_opts(s)
                return self._json({"urls": [state.rung_url(state.token(r, mode="pass", **o),
                                                           s.config["dataset_size"]) for r in rungs]})
            if action == "next":
                t = s.next_trial()
                if t is None:
                    return self._json({"done": True, "progress": s.progress()})
                toks = state.interval_tokens(s, t)
                return self._json({"done": False, "index": t["index"], "prompt": t["prompt"],
                                   "urls": [state.rung_url(k, s.config["dataset_size"]) for k in toks],
                                   "progress": s.progress()})
            if action == "answer":
                s.record(int(b["index"]), b)
                return self._json({"ok": True, "progress": s.progress()})
            raise KeyError(p)

        def _session_info(self, s: Session) -> dict:
            c = s.config
            return {"id": s.id, "kind": s.kind, "size": c["dataset_size"], "progress": s.progress(),
                    "sitting": s.sitting, "allow_no_difference": c.get("allow_no_difference", False),
                    "break_every": c.get("break_every", 20), "timing": c.get("timing") or {},
                    "allow_editing": State.interval_opts(s)["edit"] == "all",
                    "marker_hidden": State.interval_opts(s)["marker"] == "hide"}

    return Handler


def serve(host: str, port: int, results_dir: Path, defaults: dict, verbose: bool = False) -> ThreadingHTTPServer:
    state = State(results_dir, defaults)
    httpd = ThreadingHTTPServer((host, port), make_handler(state, verbose))
    httpd.daemon_threads = True
    httpd.state = state  # type: ignore[attr-defined]
    return httpd
