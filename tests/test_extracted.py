"""Tests for the round-trip rebuild helpers (ADR 0015).

``enriched_from_es_source`` and ``enriched_from_frontmatter`` invert the two persisted
representations so goldberg-extracted can be populated from ES and ES rebuilt from
goldberg-extracted, with no re-extraction or re-enrichment.
"""

from __future__ import annotations

import pytest

from goldberg_system.extracted import (
    enriched_from_es_source,
    enriched_from_frontmatter,
)
from goldberg_system.identity import compute_doc_id
from goldberg_system.metadata import Claim, DocumentMetadata, Origin, Role
from goldberg_system.metadata.frontmatter import to_frontmatter_document
from goldberg_system.sinks import EnrichedDocument, to_es_document


def _doc() -> EnrichedDocument:
    return EnrichedDocument(
        doc_id="gb_abc",
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="c0ffee",
        markdown="The extracted body text about the prosecution.",
        metadata=DocumentMetadata(
            summary="short",
            matters=["422500059892", "648MC011"],
            author="Simon Goldberg",
            origin=Origin.RECEIVED,
            role=Role.INPUT,
            entities=["Simon Goldberg", "Empower the People"],
            raw_path="evidence/cps/x/msg.eml",
            raw_commit="c0ffee",
            raw_sha256="deadbeef",
            ingested_at="2026-07-21T10:00:00+00:00",
            claims=[
                Claim(
                    subject="prosecuting_entity",
                    predicate="is",
                    object="Empower the People",
                    asserted_by="Simon Goldberg",
                )
            ],
        ),
    )


def test_es_source_round_trip_preserves_content_and_rich_metadata() -> None:
    rebuilt = enriched_from_es_source(to_es_document(_doc()))
    assert rebuilt.doc_id == "gb_abc"
    assert rebuilt.raw_path == "evidence/cps/x/msg.eml"
    assert rebuilt.raw_commit == "c0ffee"
    assert rebuilt.markdown.startswith("The extracted")
    assert rebuilt.metadata.matters == ["422500059892", "648MC011"]
    assert rebuilt.metadata.author == "Simon Goldberg"
    assert rebuilt.metadata.origin is Origin.RECEIVED
    assert rebuilt.metadata.raw_sha256 == "deadbeef"
    assert rebuilt.metadata.claims[0].object == "Empower the People"
    assert rebuilt.metadata.claims[0].asserted_by == "Simon Goldberg"


def test_es_source_ignores_es_only_and_unknown_keys() -> None:
    source = to_es_document(_doc())
    source["content_hash"] = "should-be-ignored"
    source["_totally_unknown_future_field"] = {"nested": 1}
    # must not raise despite DocumentMetadata using extra="forbid"
    rebuilt = enriched_from_es_source(source)
    assert rebuilt.doc_id == "gb_abc"


def test_es_source_uses_fallback_doc_id_when_absent() -> None:
    source = to_es_document(_doc())
    del source["doc_id"]
    rebuilt = enriched_from_es_source(source, doc_id_fallback="from_es_id")
    assert rebuilt.doc_id == "from_es_id"


def test_frontmatter_round_trip_recomputes_matching_doc_id() -> None:
    doc = _doc()
    text = to_frontmatter_document(doc.metadata, doc.markdown)
    rebuilt = enriched_from_frontmatter(text)
    # raw_path + attributed claims survive the file round-trip
    assert rebuilt.raw_path == "evidence/cps/x/msg.eml"
    assert rebuilt.markdown.startswith("The extracted")
    assert rebuilt.metadata.claims[0].object == "Empower the People"
    # doc_id is not stored in the file; it is recomputed deterministically
    assert rebuilt.doc_id == compute_doc_id(
        "evidence/cps/x/msg.eml", doc.markdown.encode("utf-8")
    )


def test_frontmatter_without_raw_path_is_rejected() -> None:
    text = to_frontmatter_document(DocumentMetadata(summary="no anchor"), "body")
    with pytest.raises(ValueError, match="raw_path"):
        enriched_from_frontmatter(text)
