"""Tests for the sink interface (FR-006)."""

from __future__ import annotations

from goldberg_system.metadata import DocumentMetadata
from goldberg_system.sinks import EnrichedDocument, Sink, SinkResult


class FakeSink:
    """A minimal in-memory sink used to prove the Protocol is implementable."""

    def __init__(self) -> None:
        self.written: list[EnrichedDocument] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document: EnrichedDocument) -> SinkResult:
        self.written.append(document)
        return SinkResult(sink=self.name, ok=True)


def _doc() -> EnrichedDocument:
    return EnrichedDocument(
        doc_id="gb_x",
        raw_path="evidence/x/a.pdf",
        raw_commit="abc",
        markdown="# hello",
        metadata=DocumentMetadata(matters=["422500059892"]),
    )


def test_fake_sink_satisfies_protocol() -> None:
    sink = FakeSink()
    assert isinstance(sink, Sink)


def test_sink_write_returns_result_and_records() -> None:
    sink = FakeSink()
    result = sink.write(_doc())
    assert result.ok is True
    assert result.sink == "fake"
    assert len(sink.written) == 1


def test_non_sink_is_not_an_instance() -> None:
    assert not isinstance(object(), Sink)
