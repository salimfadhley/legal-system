"""Tests for pipeline events + trace (M12, ADR 0008)."""

from __future__ import annotations

from typing import Any

from goldberg_system.observability.events import (
    NullEventSink,
    PipelineEvent,
    safe_emit,
)
from goldberg_system.observability.trace import read_trace


def test_pipeline_event_make_sets_ts_and_fields() -> None:
    e = PipelineEvent.make("backfill", "indexed", "ok", doc_id="gb_1", raw_path="a.pdf")
    assert e.component == "backfill" and e.stage == "indexed" and e.status == "ok"
    assert e.doc_id == "gb_1" and e.raw_path == "a.pdf"
    assert e.ts  # ISO timestamp populated


def test_null_sink_and_safe_emit_never_raise() -> None:
    safe_emit(None, PipelineEvent.make("backfill", "received", "ok"))
    safe_emit(NullEventSink(), PipelineEvent.make("backfill", "received", "ok"))


class _RaisingSink:
    def emit(self, event: PipelineEvent) -> None:
        raise RuntimeError("es down")


def test_safe_emit_swallows_sink_errors() -> None:
    # telemetry failure must never break the pipeline
    safe_emit(_RaisingSink(), PipelineEvent.make("backfill", "indexed", "failed"))


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    def emit(self, event: PipelineEvent) -> None:
        self.events.append(event)


def test_capturing_sink_records_emitted_events() -> None:
    sink = _CapturingSink()
    safe_emit(
        sink, PipelineEvent.make("backfill", "extracted", "skipped", reason="empty")
    )
    assert len(sink.events) == 1
    assert sink.events[0].status == "skipped" and sink.events[0].reason == "empty"


class _TraceES:
    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self._sources = sources
        self.last: dict[str, Any] | None = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.last = kwargs
        return {"hits": {"hits": [{"_source": s} for s in self._sources]}}


def test_read_trace_returns_ordered_timeline_fallback_without_sha() -> None:
    # events with no sha256 → fall back to a direct match on the key
    es = _TraceES(
        [
            {"ts": "2026-07-21T10:00:00Z", "stage": "received", "status": "ok"},
            {
                "ts": "2026-07-21T10:00:05Z",
                "stage": "extracted",
                "status": "skipped",
                "reason": "no content from Papra/Docling",
            },
        ]
    )
    events = read_trace(es, "some.pdf")
    assert [e.stage for e in events] == ["received", "extracted"]
    assert events[1].status == "skipped"
    should = es.last["query"]["bool"]["should"]
    assert {"term": {"raw_path": "some.pdf"}} in should
    assert es.last["sort"] == [{"ts": {"order": "asc"}}]


def test_read_trace_resolves_by_sha256_for_full_timeline() -> None:
    # a document's stages carry the same sha256 but different raw_path/doc_id;
    # looking up by any identifier must return the WHOLE timeline via sha256.
    es = _TraceES(
        [
            {
                "ts": "t1",
                "stage": "received",
                "status": "ok",
                "sha256": "abc",
                "raw_path": "papra-name.pdf",
            },
            {
                "ts": "t2",
                "stage": "indexed",
                "status": "ok",
                "sha256": "abc",
                "raw_path": "evidence/real.pdf",
                "doc_id": "gb_1",
            },
        ]
    )
    events = read_trace(es, "papra-name.pdf")
    assert [e.stage for e in events] == ["received", "indexed"]
    # second (final) query fetches every event sharing the resolved sha256
    assert es.last["query"] == {"terms": {"sha256": ["abc"]}}
