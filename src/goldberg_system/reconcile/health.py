"""A minimal ``GET /health`` endpoint for the reconciler daemon (FR-010 / NFR-004).

Stdlib HTTP server (same style as the retired ``service/app.py``) so Halob /
monitoring can liveness-check the daemon and read its last reconcile cycle. No web
framework dependency.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from goldberg_system.reconcile.reconciler import Reconciler


def health_payload(reconciler: "Reconciler") -> dict[str, Any]:
    """The JSON body served at ``/health`` — status + the last reconcile cycle."""
    last = reconciler.last_cycle
    return {
        "status": "ok",
        "last_cycle": asdict(last) if last is not None else None,
    }


def make_health_server(
    reconciler: "Reconciler", host: str = "0.0.0.0", port: int = 8080
) -> ThreadingHTTPServer:
    """Build (but do not start) a health server bound to ``host:port``.

    Run it with ``server.serve_forever()`` (typically on a daemon thread).
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet default stderr logging
            pass

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                self._send(200, health_payload(reconciler))
            else:
                self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), _Handler)
