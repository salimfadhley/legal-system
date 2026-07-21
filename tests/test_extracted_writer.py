"""Tests for the goldberg-extracted repository writer sink."""

from __future__ import annotations

from pathlib import Path

from goldberg_system.metadata import DocumentMetadata, parse_frontmatter_document
from goldberg_system.sinks import EnrichedDocument, ExtractedRepoWriter, Sink


def _doc() -> EnrichedDocument:
    return EnrichedDocument(
        doc_id="gb_abc",
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="c0ffee",
        markdown="Extracted body.",
        metadata=DocumentMetadata(
            summary="short",
            matters=["422500059892"],
            raw_path="evidence/cps/x/msg.eml",
            raw_commit="c0ffee",
        ),
    )


def test_writer_satisfies_sink_protocol(tmp_path: Path) -> None:
    assert isinstance(ExtractedRepoWriter(tmp_path), Sink)


def test_writes_frontmatter_file_mirroring_raw_path(tmp_path: Path) -> None:
    writer = ExtractedRepoWriter(tmp_path)
    result = writer.write(_doc())
    assert result.ok is True

    expected = tmp_path / "evidence/cps/x/msg.eml.md"
    assert expected.exists()
    meta, body = parse_frontmatter_document(expected.read_text())
    assert body.strip() == "Extracted body."
    assert meta.summary == "short"
    assert meta.matters == ["422500059892"]
    assert meta.raw_path == "evidence/cps/x/msg.eml"


def test_overwrite_is_idempotent(tmp_path: Path) -> None:
    writer = ExtractedRepoWriter(tmp_path)
    writer.write(_doc())
    writer.write(_doc())  # second write must not error
    assert (tmp_path / "evidence/cps/x/msg.eml.md").exists()
