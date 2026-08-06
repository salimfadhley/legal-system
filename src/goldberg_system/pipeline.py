"""Pipeline orchestration (M5, batch form).

Chains the stages for one document — take the extracted markdown for a raw file,
enrich it, build the enriched document, and write it to the sinks. The direct-Docling
reingest path (``goldberg_system.migrate.reingest``) drives these building blocks over
the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass

from goldberg_system.enrichment.adapter import EnrichmentAdapter, EnrichmentRequest
from goldberg_system.enrichment.assemble import merge_enrichment
from goldberg_system.identity import compute_doc_id
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.provenance import now_iso
from goldberg_system.sinks.base import EnrichedDocument, Sink, SinkResult


def build_enriched_from_raw(
    raw_path: str,
    content: str,
    enricher: EnrichmentAdapter,
    *,
    base: DocumentMetadata,
    ingested_at: str | None = None,
) -> EnrichedDocument:
    """Assemble an enriched document from a raw file's extracted ``content`` + its
    manifest ``base`` (the direct-Docling bulk path — no Papra involved)."""
    base = base.model_copy(update={"raw_path": raw_path})
    result = enricher.enrich(
        EnrichmentRequest(doc_id=raw_path, markdown=content, metadata=base)
    )
    enriched = merge_enrichment(base, result, ingested_at=ingested_at or now_iso())
    doc_id = compute_doc_id(raw_path, content.encode("utf-8"))
    return EnrichedDocument(
        doc_id=doc_id,
        raw_path=raw_path,
        raw_commit=base.raw_commit or "",
        markdown=content,
        metadata=enriched,
    )


def write_to_sinks(document: EnrichedDocument, sinks: list[Sink]) -> list[SinkResult]:
    return [sink.write(document) for sink in sinks]


@dataclass
class BackfillReport:
    processed: int = 0
    indexed: int = 0
    skipped_empty: int = 0
    failures: int = 0
    with_provenance: int = 0  # docs matched to a manifest entry (real raw_path/matters)
