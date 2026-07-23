"""The live-index HTTP app (stdlib) — receives Papra webhooks and processes them.

.. deprecated:: M15
    **RETIRED / superseded.** This Papra-webhook live-index trigger is no longer the
    auto-ingestion path. It indexed off Papra's extraction *without* registering
    ``goldberg-raw`` provenance first, so documents landed with no ``raw_commit`` /
    ``matters`` (the flaw ADR 0006/0008 exposed). The canonical automatic ingest path
    is now the **event-driven ingest service** — ``goldberg ingest-serve`` — which
    consumes ``goldberg.raw.commit`` triggers from NATS and registers provenance from
    goldberg-raw + the manifest *before* indexing, extracting via direct Docling. See
    ADR 0005 (superseded), ADR 0013 (event-driven ingestion, supersedes the reconciler
    ADR 0011), and ``doc/runbooks/wiring-the-ingest-trigger.md``. Kept for reference;
    do not deploy.

`POST /webhooks/papra` accepts a Papra `document:created` event, returns 200
immediately, and processes the document in a background thread (fetch content →
enrich → index). `GET /health` is a liveness check. See ADR 0005.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from goldberg_system.service.processor import Processor
from goldberg_system.service.webhook import parse_papra_event

log = logging.getLogger("goldberg.service")


def _safe_process(processor: Processor, document_id: str) -> None:
    try:
        processor.process(document_id)
    except Exception:  # noqa: BLE001 - a bad document must not kill the worker
        log.exception("processing failed for %s", document_id)


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # quiet the default stderr logging
        pass

    @property
    def _processor(self) -> Processor:
        return self.server.processor  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/webhooks/papra":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            event, doc_id = parse_papra_event(body)
        except Exception as exc:  # noqa: BLE001 - malformed body → 400
            self._send(400, {"error": f"bad body: {exc}"})
            return
        if event == "document:created" and doc_id:
            threading.Thread(
                target=_safe_process, args=(self._processor, doc_id), daemon=True
            ).start()
            self._send(200, {"accepted": True, "document_id": doc_id})
        else:
            self._send(200, {"ignored": event})


def make_server(
    processor: Processor, host: str = "0.0.0.0", port: int = 8080
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.processor = processor  # type: ignore[attr-defined]
    return httpd


def run(processor: Processor, host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = make_server(processor, host, port)
    log.info("live-index listening on %s:%s", host, port)
    httpd.serve_forever()
