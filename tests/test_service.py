"""Tests for the live-index service (webhook parsing, processor, HTTP app)."""

from __future__ import annotations

import http.client
import json
import threading
import time

from goldberg_system.enrichment import EnrichmentResult
from goldberg_system.papra import PapraDocument
from goldberg_system.service import Processor, parse_papra_event
from goldberg_system.service.app import make_server
from goldberg_system.sinks import EnrichedDocument, SinkResult

# --- webhook parsing -------------------------------------------------------


def test_parse_document_created() -> None:
    body = json.dumps({"type": "document:created", "data": {"id": "doc_1"}}).encode()
    assert parse_papra_event(body) == ("document:created", "doc_1")


def test_parse_alternative_id_key() -> None:
    body = json.dumps({"event": "document:created", "documentId": "doc_9"}).encode()
    assert parse_papra_event(body) == ("document:created", "doc_9")


def test_parse_non_object() -> None:
    assert parse_papra_event(b"[]") == (None, None)


# --- processor -------------------------------------------------------------


class _FakeEnricher:
    def enrich(self, request):  # type: ignore[no-untyped-def]
        return EnrichmentResult(summary="s", author="A")


class _FakeSink:
    def __init__(self) -> None:
        self.written: list[EnrichedDocument] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document: EnrichedDocument) -> SinkResult:
        self.written.append(document)
        return SinkResult(sink=self.name, ok=True)


class _FakePapra:
    def __init__(self, content: str) -> None:
        self._content = content

    def get_document(self, document_id: str) -> PapraDocument:
        return PapraDocument(
            id=document_id, original_name="d.pdf", content=self._content
        )


def test_processor_indexes_document_with_content() -> None:
    sink = _FakeSink()
    proc = Processor(_FakePapra("Body."), _FakeEnricher(), [sink])
    assert proc.process("doc_1") is True
    assert sink.written[0].metadata.papra_document_id == "doc_1"


def test_processor_skips_empty_content() -> None:
    sink = _FakeSink()
    proc = Processor(_FakePapra("   "), _FakeEnricher(), [sink])
    assert proc.process("doc_1") is False
    assert sink.written == []


# --- HTTP app (end to end) -------------------------------------------------


def test_webhook_endpoint_triggers_processing() -> None:
    calls: list[str] = []

    class _RecordingProcessor:
        def process(self, document_id: str) -> bool:
            calls.append(document_id)
            return True

    httpd = make_server(_RecordingProcessor(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/health")
        assert conn.getresponse().status == 200

        body = json.dumps({"type": "document:created", "data": {"id": "doc_42"}})
        conn.request(
            "POST",
            "/webhooks/papra",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read())["accepted"] is True

        for _ in range(100):
            if calls:
                break
            time.sleep(0.02)
        assert calls == ["doc_42"]
    finally:
        httpd.shutdown()
