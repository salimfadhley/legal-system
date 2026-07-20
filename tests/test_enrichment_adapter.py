"""Tests for the enrichment adapter boundary (FR-010)."""

from __future__ import annotations

from goldberg_system.enrichment import (
    EnrichmentAdapter,
    EnrichmentRequest,
    EnrichmentResult,
)
from goldberg_system.enrichment.adapter import Claim
from goldberg_system.metadata import DocumentMetadata


class FakeAdapter:
    """A stand-in for the Mind of Steele enrichment wiring (M3)."""

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        return EnrichmentResult(
            summary=f"summary of {request.doc_id}",
            keywords=["cps", "disclosure"],
            entities=["Goldberg", "Fadhley"],
            author="Simon Goldberg",
            claims=[
                Claim(
                    subject="prosecuting_entity",
                    predicate="is",
                    object="Empower the People",
                    asserted_by="Simon Goldberg",
                )
            ],
        )


def test_fake_adapter_satisfies_protocol() -> None:
    assert isinstance(FakeAdapter(), EnrichmentAdapter)


def test_enrich_returns_attributed_result() -> None:
    adapter: EnrichmentAdapter = FakeAdapter()
    request = EnrichmentRequest(
        doc_id="gb_x",
        markdown="# body",
        metadata=DocumentMetadata(matters=["422500059892"]),
    )
    result = adapter.enrich(request)
    assert result.author == "Simon Goldberg"
    assert result.claims[0].asserted_by == "Simon Goldberg"
    assert result.claims[0].object == "Empower the People"


def test_result_round_trips() -> None:
    result = EnrichmentResult(summary="s", keywords=["a"], entities=["b"])
    restored = EnrichmentResult.model_validate_json(result.model_dump_json())
    assert restored == result
