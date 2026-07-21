"""Enrichment — the adapter boundary and the document assembly (ADR 0004)."""

from goldberg_system.enrichment.adapter import (
    Claim,
    EnrichmentAdapter,
    EnrichmentRequest,
    EnrichmentResult,
)
from goldberg_system.enrichment.assemble import (
    assemble_enriched_document,
    merge_enrichment,
)
from goldberg_system.enrichment.openai_enricher import OpenAIEnricher

__all__ = [
    "EnrichmentAdapter",
    "EnrichmentRequest",
    "EnrichmentResult",
    "Claim",
    "assemble_enriched_document",
    "merge_enrichment",
    "OpenAIEnricher",
]
