"""Tests for assembling the enriched extracted document (ADR 0004)."""

from __future__ import annotations

from goldberg_system.enrichment import (
    Claim,
    EnrichmentResult,
    assemble_enriched_document,
)
from goldberg_system.metadata import DocumentMetadata, parse_frontmatter_document


def test_merges_enrichment_into_base_and_renders_frontmatter() -> None:
    base = DocumentMetadata(
        matters=["422500059892"],
        keywords=["seed"],
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="abc123",
    )
    result = EnrichmentResult(
        summary="short",
        long_summary="longer",
        keywords=["cps"],
        entities=["Goldberg", "Fadhley"],
        author="Simon Goldberg",
        document_type="email",
        claims=[
            Claim(
                subject="prosecutor",
                predicate="is",
                object="ETP",
                asserted_by="Goldberg",
            )
        ],
    )
    doc = assemble_enriched_document(base, result, "Body text.")
    meta, body = parse_frontmatter_document(doc)

    assert body.strip() == "Body text."
    assert meta.summary == "short"
    assert meta.long_summary == "longer"
    assert set(meta.keywords) == {"seed", "cps"}  # union of base + enrichment
    assert meta.entities == ["Goldberg", "Fadhley"]
    assert meta.matters == ["422500059892"]  # base preserved
    assert meta.raw_path == "evidence/cps/x/msg.eml"  # provenance preserved
    assert meta.author == "Simon Goldberg"
    assert meta.document_type == "email"
    assert meta.claims[0].object == "ETP"


def test_enrichment_does_not_override_human_set_author() -> None:
    base = DocumentMetadata(author="Human Set")
    result = EnrichmentResult(summary="s", author="LLM Guess")
    meta, _ = parse_frontmatter_document(assemble_enriched_document(base, result, "b"))
    assert meta.author == "Human Set"
