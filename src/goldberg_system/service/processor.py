"""Process one document: fetch its content from Papra, enrich, write to sinks.

Papra fires ``document:created`` **immediately on ingest**, before Docling has
finished extracting — so on receipt the document's ``content`` is often still
empty. The processor therefore polls Papra for content (with backoff) before
enriching, up to a bounded number of attempts.
"""

from __future__ import annotations

import logging
import os
import time

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.papra.client import PapraClient, PapraDocument
from goldberg_system.pipeline import build_enriched_document, write_to_sinks
from goldberg_system.sinks.base import Sink

log = logging.getLogger("goldberg.service")


class Processor:
    """Enrich + index a single Papra document (the live-pipeline step)."""

    def __init__(
        self,
        papra: PapraClient,
        enricher: EnrichmentAdapter,
        sinks: list[Sink],
        *,
        content_poll_attempts: int | None = None,
        content_poll_interval: float | None = None,
        sleep: object = time.sleep,
    ) -> None:
        self.papra = papra
        self.enricher = enricher
        self.sinks = sinks
        self.content_poll_attempts = content_poll_attempts or int(
            os.environ.get("CONTENT_POLL_ATTEMPTS", "24")
        )
        self.content_poll_interval = content_poll_interval or float(
            os.environ.get("CONTENT_POLL_INTERVAL", "5")
        )
        self._sleep = sleep

    def _fetch_with_content(self, document_id: str) -> PapraDocument | None:
        """Poll Papra until the document has extracted content (or give up)."""
        last: PapraDocument | None = None
        for attempt in range(self.content_poll_attempts):
            last = self.papra.get_document(document_id)
            if (last.content or "").strip():
                return last
            if attempt < self.content_poll_attempts - 1:
                self._sleep(self.content_poll_interval)  # type: ignore[operator]
        return None if last is None or not (last.content or "").strip() else last

    def process(self, document_id: str) -> bool:
        """Fetch (waiting for extraction), enrich and index a document."""
        doc = self._fetch_with_content(document_id)
        if doc is None:
            log.info("skip (no content after polling): %s", document_id)
            return False
        content = doc.content or ""
        enriched = build_enriched_document(doc, content, self.enricher)
        results = write_to_sinks(enriched, self.sinks)
        ok = all(r.ok for r in results)
        log.info("processed %s -> %s (ok=%s)", doc.original_name, enriched.doc_id, ok)
        for r in results:
            if not r.ok:
                log.error("sink %s failed: %s", r.sink, r.detail)
        return ok
