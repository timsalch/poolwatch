"""A small local dashboard: latest image with its labels, current pump state, and history.

Standard library only (http.server), so it runs anywhere poolwatch does. It reads
the files EventStore writes, so it can run inside `poolwatch run`/`watch` or alone
with `poolwatch dashboard`.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .history import EventStore

log = logging.getLogger(__name__)


def _page() -> bytes:
    return resources.files("poolwatch").joinpath("dashboard.html").read_bytes()


def safe_image_path(raw: str, roots: list[Path]) -> Path | None:
    """Resolve a requested image and refuse anything outside the allowed folders."""
    if not raw:
        return None
    try:
        path = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        return None
    for root in roots:
        try:
            if path.is_relative_to(root.resolve()) and path.is_file():
                return path
        except OSError:
            continue
    return None


def make_handler(store: EventStore, image_roots: list[Path]):
    roots = [Path(r) for r in image_roots]

    class Handler(BaseHTTPRequestHandler):
        server_version = "poolwatch"

        def log_message(self, fmt, *args):  # keep the console for poolwatch's own log
            log.debug("web: " + fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):  # noqa: N802 (http.server naming)
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path.rstrip("/") or "/"
            if path == "/":
                return self._send(200, _page(), "text/html; charset=utf-8")
            if path == "/api/state":
                state = store.state() or {}
                image_id = state.get("latest_image_event_id")
                return self._json({"state": state or None,
                                   "latest": store.get(image_id) if image_id else None})
            if path == "/api/events":
                try:
                    limit = max(1, min(500, int(q.get("limit", ["40"])[0])))
                except ValueError:
                    limit = 40
                return self._json(store.recent(limit))
            if path.startswith("/api/events/"):
                try:
                    event_id = int(path.rsplit("/", 1)[1])
                except ValueError:
                    return self._json({"error": "bad id"}, 400)
                event = store.get(event_id)
                if event is None:
                    return self._json({"error": "not found"}, 404)
                prev, nxt = store.neighbors(event_id)
                return self._json({"event": event, "prev": prev, "next": nxt,
                                   "state": store.state()})
            if path == "/image":
                image = safe_image_path(q.get("p", [""])[0], roots)
                if image is None:
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
                return self._send(200, image.read_bytes(), ctype, cache=True)
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    return Handler


def serve(store: EventStore, image_roots: list[Path], host: str = "127.0.0.1",
          port: int = 8000, background: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store, image_roots))
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    log.info("dashboard: http://%s:%d", shown, server.server_address[1])
    if background:
        threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    else:
        server.serve_forever()
    return server
