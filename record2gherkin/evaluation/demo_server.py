"""Local HTTP server for the MiniShop demo app (spec §3).

A ``ThreadingHTTPServer`` bound to ``127.0.0.1:8461`` by default.  The port is an experiment
parameter (the recorded features bake ``http://127.0.0.1:8461/`` into their Given/navigate steps),
so the server never silently falls back to another port: a busy port is a hard error.

Endpoints:

* ``GET /healthz``                  -> ``{"mutation": ..., "seed": ...}``
* ``POST /__control``               -> body ``{"mutation": "M3", "seed": 42}``, updates state, 400 on bad input
* ``GET /`` (any other path)        -> the full document for the current state, ``Cache-Control: no-store``

CLI: ``python -m record2gherkin.evaluation.demo_server [--host H] [--port P]``.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from record2gherkin.evaluation.demo_app import MUTATIONS, render_page
from testzeus_hercules.utils.logger import logger

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8461

HEALTH_PATH = "/healthz"
CONTROL_PATH = "/__control"


class PortInUseError(RuntimeError):
    """Raised when the configured port cannot be bound (spec §3.1: never auto-switch ports)."""


class DemoState:
    """In-process single-valued ``(mutation, seed)`` state (spec §3.3)."""

    def __init__(self, mutation: str = "M0", seed: int = 0) -> None:
        self._lock = threading.Lock()
        self.set(mutation, seed)

    def set(self, mutation: str, seed: int) -> None:
        if mutation not in MUTATIONS:
            raise ValueError(f"unknown mutation: {mutation!r}")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError(f"seed must be a non-negative int, got {seed!r}")
        with self._lock:
            self._mutation = mutation
            self._seed = seed

    def get(self) -> tuple[str, int]:
        with self._lock:
            return self._mutation, self._seed

    def as_dict(self) -> dict[str, Any]:
        mutation, seed = self.get()
        return {"mutation": mutation, "seed": seed}

    def render(self) -> str:
        mutation, seed = self.get()
        return render_page(mutation, seed)


def _make_handler(state: DemoState) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "MiniShopDemo/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            logger.debug("demo_server: %s", format % args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            path = self.path.split("?", 1)[0]
            if path == HEALTH_PATH:
                self._send_json(200, state.as_dict())
                return
            document = state.render().encode("utf-8")
            self._send(200, document, "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if path != CONTROL_PATH:
                self._send_json(404, {"error": "not found", "path": path})
                return
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json body"})
                return
            if not isinstance(payload, Mapping):
                self._send_json(400, {"error": "body must be a json object"})
                return
            try:
                mutation, seed = _parse_control(payload)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc), **state.as_dict()})
                return
            state.set(mutation, seed)
            self._send_json(200, state.as_dict())

    return DemoHandler


def _parse_control(payload: Mapping[str, Any]) -> tuple[str, int]:
    """Validate a ``/__control`` body; the current state is left untouched on any error."""
    mutation = payload.get("mutation")
    seed = payload.get("seed", 0)
    if not isinstance(mutation, str) or mutation not in MUTATIONS:
        raise ValueError(f"invalid mutation: {mutation!r}")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"invalid seed: {seed!r}")
    return mutation, seed


class DemoServer:
    """Threaded demo server; ``start()`` binds the port (raising :class:`PortInUseError` if busy)."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, mutation: str = "M0", seed: int = 0) -> None:
        self.host = host
        self._requested_port = port
        self.state = DemoState(mutation, seed)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self, *, background: bool = False) -> None:
        """Bind and start serving; ``background=True`` runs ``serve_forever`` in a daemon thread."""
        if self._httpd is not None:
            return
        try:
            self._httpd = ThreadingHTTPServer((self.host, self._requested_port), _make_handler(self.state))
        except OSError as exc:
            raise PortInUseError(f"cannot bind {self.host}:{self._requested_port} ({exc}); free the port or pick another one explicitly") from exc
        self._httpd.daemon_threads = True
        if background:
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="mini-shop-demo", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._httpd = None

    def serve_forever(self) -> None:
        if self._httpd is None:
            self.start()
        assert self._httpd is not None  # for type checkers
        self._httpd.serve_forever()

    # -- introspection -----------------------------------------------------------------------

    @property
    def port(self) -> int:
        """Bound port (resolved when ``port=0`` was requested)."""
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self._requested_port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> "DemoServer":
        self.start(background=True)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MiniShop demo server (spec §3)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mutation", default="M0", choices=list(MUTATIONS))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    server = DemoServer(args.host, args.port, args.mutation, args.seed)
    try:
        server.start()
    except PortInUseError as exc:
        logger.error("demo_server: %s", exc)
        return 2
    logger.info("demo_server: serving %s (%s, seed %s)", server.base_url, args.mutation, args.seed)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("demo_server: interrupted, shutting down")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
