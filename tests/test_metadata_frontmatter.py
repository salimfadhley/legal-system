"""Tests for markdown + YAML frontmatter serialization (ADR 0004)."""

from __future__ import annotations

from goldberg_system.metadata import (
    Claim,
    DocumentMetadata,
    Origin,
    Role,
    parse_frontmatter_document,
    to_frontmatter_document,
)


def test_round_trips_metadata_and_body() -> None:
    md = DocumentMetadata(
        summary="short summary",
        long_summary="the longer summary",
        keywords=["cps", "disclosure"],
        matters=["422500059892"],
        author="Simon Goldberg",
        origin=Origin.RECEIVED,
        role=Role.INPUT,
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="abc123",
        claims=[
            Claim(
                subject="prosecuting_entity",
                predicate="is",
                object="Empower the People",
                asserted_by="Simon Goldberg",
            )
        ],
    )
    doc = to_frontmatter_document(md, "The extracted body text.")
    assert doc.startswith("---")
    assert "The extracted body text." in doc

    parsed, body = parse_frontmatter_document(doc)
    assert body.strip() == "The extracted body text."
    assert parsed.summary == "short summary"
    assert parsed.claims[0].object == "Empower the People"
    assert parsed == md  # semantic round-trip


def test_defaults_are_omitted_from_frontmatter() -> None:
    doc = to_frontmatter_document(DocumentMetadata(summary="s"), "body")
    # non-default field present; default fields (skip, empty lists) omitted
    assert "summary:" in doc
    assert "skip:" not in doc
    assert "parties:" not in doc


def test_empty_metadata_still_renders_body() -> None:
    doc = to_frontmatter_document(DocumentMetadata(), "just a body")
    _, body = parse_frontmatter_document(doc)
    assert body.strip() == "just a body"
