"""Process one document: fetch its content from Papra, enrich, write to sinks."""

from __future__ import annotations

import logging

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.papra.client import PapraClient
from goldberg_system.pipeline import build_enriched_document, write_to_sinks
from goldberg_system.sinks.base import Sink

log = logging.getLogger("goldberg.service")


class Processor:
    """Enrich + index a single Papra document (the live-pipeline step)."""

    def __init__(
        self, papra: PapraClient, enricher: EnrichmentAdapter, sinks: list[Sink]
    ) -> None:
        self.papra = papra
        self.enricher = enricher
        self.sinks = sinks

    def process(self, document_id: str) -> bool:
        """Fetch, enrich and index a document. Returns True if all sinks succeeded."""
        doc = self.papra.get_document(document_id)
        content = doc.content or ""
        if not content.strip():
            log.info("skip (no content): %s (%s)", document_id, doc.original_name)
            return False
        enriched = build_enriched_document(doc, content, self.enricher)
        results = write_to_sinks(enriched, self.sinks)
        ok = all(r.ok for r in results)
        log.info("processed %s -> %s (ok=%s)", doc.original_name, enriched.doc_id, ok)
        for r in results:
            if not r.ok:
                log.error("sink %s failed: %s", r.sink, r.detail)
        return ok
