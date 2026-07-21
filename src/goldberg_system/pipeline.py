"""Pipeline orchestration (M5, batch form).

Chains the stages for one document — pull the extracted content from Papra, enrich
it, build the enriched document, and write it to the sinks — plus a batch
``backfill_from_papra`` that runs this over the documents already in a Papra
organisation (the input-passthrough path of ADR 0003). The NATS event-loop form of
the service is M5 proper; this runnable core also powers backfill (M8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter, EnrichmentRequest
from goldberg_system.enrichment.assemble import merge_enrichment
from goldberg_system.identity import compute_doc_id
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.papra.client import PapraClient, PapraDocument
from goldberg_system.provenance import now_iso
from goldberg_system.sinks.base import EnrichedDocument, Sink, SinkResult

if TYPE_CHECKING:
    from goldberg_system.migrate.manifest import Manifest


def build_enriched_document(
    papra_doc: PapraDocument,
    content: str,
    enricher: EnrichmentAdapter,
    *,
    base: DocumentMetadata | None = None,
    ingested_at: str | None = None,
) -> EnrichedDocument:
    """Enrich ``content`` and assemble the :class:`EnrichedDocument` for the sinks."""
    raw_path = (
        (base.raw_path if base else None) or papra_doc.original_name or papra_doc.id
    )
    base = (base or DocumentMetadata()).model_copy(
        update={"raw_path": raw_path, "papra_document_id": papra_doc.id}
    )
    result = enricher.enrich(
        EnrichmentRequest(doc_id=papra_doc.id, markdown=content, metadata=base)
    )
    enriched = merge_enrichment(base, result, ingested_at=ingested_at or now_iso())
    doc_id = compute_doc_id(raw_path, content.encode("utf-8"))
    return EnrichedDocument(
        doc_id=doc_id,
        raw_path=raw_path,
        raw_commit=base.raw_commit or f"papra:{papra_doc.id}",
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


def backfill_from_papra(
    papra: PapraClient,
    enricher: EnrichmentAdapter,
    sinks: list[Sink],
    *,
    page_size: int = 100,
    max_docs: int | None = None,
    manifest: "Manifest | None" = None,
    on_doc: Callable[[PapraDocument, str], None] | None = None,
) -> BackfillReport:
    """Enrich + write every Papra document with extracted content to ``sinks``.

    When ``manifest`` is given, each document is joined to goldberg-raw by content
    SHA-256 to attach real provenance (raw_path/raw_commit) and matters (ADR 0006).
    """
    report = BackfillReport()
    for stub in papra.list_documents(page_size=page_size):
        if max_docs is not None and report.processed >= max_docs:
            break
        report.processed += 1
        try:
            full = papra.get_document(stub.id)
            content = full.content or ""
            if not content.strip():
                report.skipped_empty += 1
                if on_doc:
                    on_doc(stub, "skipped-empty")
                continue
            base = manifest.base_for(full) if manifest else None
            if base is not None:
                report.with_provenance += 1
            document = build_enriched_document(full, content, enricher, base=base)
            results = write_to_sinks(document, sinks)
            if all(r.ok for r in results):
                report.indexed += 1
                if on_doc:
                    on_doc(stub, "indexed")
            else:
                report.failures += 1
                if on_doc:
                    on_doc(stub, "sink-failed")
        except Exception as exc:  # noqa: BLE001 - one bad doc must not stop backfill
            report.failures += 1
            if on_doc:
                on_doc(stub, f"error: {exc}")
    return report
