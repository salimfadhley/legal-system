"""Enrichment — the adapter boundary and the document assembly (ADR 0004)."""

from goldberg_system.enrichment.adapter import (
    Claim,
    EnrichmentAdapter,
    EnrichmentRequest,
    EnrichmentResult,
)
from goldberg_system.enrichment.assemble import assemble_enriched_document

__all__ = [
    "EnrichmentAdapter",
    "EnrichmentRequest",
    "EnrichmentResult",
    "Claim",
    "assemble_enriched_document",
]
