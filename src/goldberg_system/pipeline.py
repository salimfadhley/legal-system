"""Pipeline orchestration (M5, batch form).

Chains the stages for one document — take the extracted markdown for a raw file,
enrich it, build the enriched document, and write it to the sinks. The direct-Docling
reingest path (``goldberg_system.migrate.reingest``) drives these building blocks over
the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path

from goldberg_system.enrichment.adapter import EnrichmentAdapter, EnrichmentRequest
from goldberg_system.enrichment.assemble import merge_enrichment
from goldberg_system.identity import compute_doc_id
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.metadata.sidecar import append_annotation
from goldberg_system.migrate.manifest import _resolve_chain, folder_base_fields
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
    # The enricher sees the ORIGINAL body; the casework ``notes`` reach it as
    # authoritative context (openai_enricher._build_messages), never spliced into the
    # text it extracts from.
    result = enricher.enrich(
        EnrichmentRequest(doc_id=raw_path, markdown=content, metadata=base)
    )
    enriched = merge_enrichment(base, result, ingested_at=ingested_at or now_iso())
    # doc_id keys on the ORIGINAL extracted content (stable identity for the raw file),
    # so adding/editing a note never mints a new document. The note is then appended to
    # the stored/indexed body inside the immutable annotation fence — searchable, but
    # never mistakable for the document's own words.
    doc_id = compute_doc_id(raw_path, content.encode("utf-8"))
    stored_markdown = append_annotation(content, enriched.notes)
    return EnrichedDocument(
        doc_id=doc_id,
        raw_path=raw_path,
        raw_commit=base.raw_commit or "",
        markdown=stored_markdown,
        metadata=enriched,
    )


def re_enrich_document(
    base: EnrichedDocument,
    enricher: EnrichmentAdapter,
    *,
    raw_root: Path | str | None = None,
) -> EnrichedDocument:
    """Re-enrich an already-extracted document from its stored ``content`` (no Docling).

    When ``raw_root`` is given, the document's folder ``metadata.yaml`` chain is
    re-resolved from the raw tree (keyed on ``base.raw_path``) and its authoritative
    fields (``claim_source``, corrected ``author``/``matters``/…) are overlaid onto the
    ES-stored metadata BEFORE enrichment — so a casework ``metadata.yaml`` edit
    re-applies without a full re-ingest. When ``raw_root`` is ``None``, the stored
    metadata is used unchanged (today's behaviour).
    """
    meta = base.metadata
    if raw_root is not None and base.raw_path:
        overrides = folder_base_fields(_resolve_chain(Path(base.raw_path), Path(raw_root)))
        if overrides:
            meta = meta.model_copy(update=overrides)
    return build_enriched_from_raw(
        base.raw_path, base.markdown, enricher, base=meta, ingested_at=meta.ingested_at
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
