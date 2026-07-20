"""Tests for the NATS event contracts (FR-005, NFR-001)."""

from __future__ import annotations

from goldberg_system.events import EventSource, IndexedEvent, RawIngestedEvent
from goldberg_system.metadata import DocumentMetadata


def test_subjects() -> None:
    assert RawIngestedEvent.subject == "goldberg.raw.ingested"
    assert IndexedEvent.subject == "goldberg.indexed"


def test_raw_ingested_defaults_to_watcher_source() -> None:
    ev = RawIngestedEvent(raw_path="evidence/x/a.pdf", raw_commit="abc")
    assert ev.source is EventSource.WATCHER
    assert ev.papra_document_id is None


def test_raw_ingested_from_papra_carries_document_id() -> None:
    ev = RawIngestedEvent(
        raw_path="evidence/x/a.pdf",
        raw_commit="abc",
        source=EventSource.PAPRA,
        papra_document_id="doc_123",
        mime_type="application/pdf",
    )
    assert ev.source is EventSource.PAPRA
    assert ev.papra_document_id == "doc_123"


def test_raw_ingested_round_trips_with_metadata() -> None:
    ev = RawIngestedEvent(
        raw_path="evidence/x/a.pdf",
        raw_commit="abc",
        doc_id="gb_deadbeef",
        metadata=DocumentMetadata(matters=["422500059892"]),
    )
    restored = RawIngestedEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev


def test_indexed_event_round_trips() -> None:
    ev = IndexedEvent(
        doc_id="gb_deadbeef",
        raw_path="evidence/x/a.pdf",
        raw_commit="abc",
        matters=["422500059892", "648MC011"],
    )
    restored = IndexedEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev
