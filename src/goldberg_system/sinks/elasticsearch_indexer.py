"""Elasticsearch indexer sink (M4).

Indexes one enriched document per Elasticsearch document, using a mapping that
matches the frontmatter schema (ADR 0004) — unlike the legacy ``goldberg_files``
mapping, which predates ``claims``, ``matters``, ``author``, and the handling
flags. `claims` is a **nested** type so per-claim fields are queryable ("who
asserted what"); text fields (`content`/`summary`/`long_summary`) are `text` for
BM25; structured fields are `keyword` for filtering/faceting.

Indexing is idempotent: the ES ``_id`` is the deterministic ``doc_id`` (re-ingest
updates, never duplicates). A dense-vector field for semantic RAG (ADR 0001) is a
deliberate follow-up, not indexed here yet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from goldberg_system.exclusion_registry import ExclusionRegistry
from goldberg_system.identity import compute_content_hash
from goldberg_system.observability.restricted_alert import record_blocked_reingest
from goldberg_system.sinks.base import EnrichedDocument, SinkResult

log = logging.getLogger("goldberg.sink")

INDEX_MAPPING: dict[str, Any] = {
    # A document can carry many atomic claims; the contradiction detector retrieves
    # them via nested `inner_hits` with size up to _INNER_HITS_CLAIM_CAP (1000, in
    # query.py). ES caps that at `index.max_inner_result_window` (default 100) and
    # rejects a larger request with a 400 — so the detector silently fails on any
    # doc with >100 claims unless this is raised to match. Durable here so a rebuilt
    # index keeps it.
    "settings": {"index": {"max_inner_result_window": 1000}},
    "mappings": {
        "dynamic": False,  # unmapped fields are stored in _source but not indexed
        "properties": {
            "doc_id": {"type": "keyword"},
            "content": {"type": "text"},
            "content_hash": {"type": "keyword"},
            "summary": {"type": "text"},
            "long_summary": {"type": "text"},
            "keywords": {"type": "keyword"},
            "entities": {"type": "keyword"},
            "topic": {"type": "keyword"},
            "document_type": {"type": "keyword"},
            "party_role": {"type": "keyword"},
            "parties": {"type": "keyword"},
            "author": {"type": "keyword"},
            "matters": {"type": "keyword"},
            "primary_matter": {"type": "keyword"},
            "origin": {"type": "keyword"},
            "role": {"type": "keyword"},
            "date": {"type": "keyword"},  # document's own date: often free-form
            # --- per-file sidecar prose + receipt-provenance (doc/system/metadata.md) ---
            "notes": {"type": "text"},  # searchable casework annotation
            "method": {"type": "text"},  # free text: what was actually opened/checked
            "date_basis": {"type": "text"},  # how we know the date
            "date_uncertain": {"type": "boolean"},
            "source_channel": {"type": "keyword"},  # how the doc reached us (free text)
            "obtained_note": {"type": "text"},
            "superseded_by": {"type": "keyword"},  # machine-visible replacement pointer
            "metadata_error": {"type": "text"},  # LOUD-but-present bad-metadata marker
            "ingested_at": {"type": "date"},
            "raw_path": {"type": "keyword"},
            "raw_commit": {"type": "keyword"},
            "raw_sha256": {"type": "keyword"},  # correlation ID across the pipeline
            "papra_document_id": {"type": "keyword"},
            "relates_to": {"type": "keyword"},
            "handling": {
                "type": "object",
                "properties": {
                    "cpia_s17": {"type": "boolean"},
                    "privileged": {"type": "boolean"},
                    "sensitivity": {"type": "keyword"},
                    "disclosure_status": {"type": "keyword"},
                    "source_channel": {"type": "keyword"},
                    "reviewed": {"type": "boolean"},
                },
            },
            "claims": {
                "type": "nested",
                "properties": {
                    "subject": {
                        "type": "text",
                        "fields": {"kw": {"type": "keyword"}},
                    },
                    "predicate": {
                        "type": "text",
                        "fields": {"kw": {"type": "keyword"}},
                    },
                    "object": {
                        "type": "text",
                        "fields": {"kw": {"type": "keyword"}},
                    },
                    "asserted_by": {"type": "keyword"},
                    # --- claim-graph additions (additive; merged via put_mapping) ---
                    "polarity": {"type": "boolean"},
                    "epistemic_status": {"type": "keyword"},
                    "source_span": {"type": "text"},
                    "claim_date": {"type": "keyword"},
                    "confidence": {"type": "float"},
                    "derived_from": {"type": "keyword"},
                },
            },
        },
    }
}


def to_es_document(document: EnrichedDocument) -> dict[str, Any]:
    """Build the Elasticsearch source document from an enriched document."""
    meta = document.metadata
    es: dict[str, Any] = {
        "doc_id": document.doc_id,
        "content": document.markdown,
        "content_hash": compute_content_hash(document.markdown.encode("utf-8")),
        "summary": meta.summary,
        "long_summary": meta.long_summary,
        "keywords": meta.keywords,
        "entities": meta.entities,
        "topic": meta.topic,
        "document_type": meta.document_type,
        "party_role": meta.party_role,
        "parties": meta.parties,
        "author": meta.author,
        "matters": meta.matters,
        "primary_matter": meta.primary_matter,
        "origin": meta.origin.value if meta.origin else None,
        "role": meta.role.value if meta.role else None,
        "date": meta.date,
        "notes": meta.notes,
        "method": meta.method,
        "date_basis": meta.date_basis,
        "date_uncertain": meta.date_uncertain,
        "source_channel": meta.source_channel,
        "obtained_note": meta.obtained_note,
        "superseded_by": meta.superseded_by,
        "metadata_error": meta.metadata_error,
        "ingested_at": meta.ingested_at,
        "raw_path": meta.raw_path,
        "raw_commit": meta.raw_commit,
        "raw_sha256": meta.raw_sha256,
        "papra_document_id": meta.papra_document_id,
        "relates_to": meta.relates_to,
        "handling": meta.handling.model_dump(mode="json"),
        "claims": [c.model_dump(mode="json") for c in meta.claims],
    }
    return {k: v for k, v in es.items() if v is not None and v != []}


def ensure_index(client: Any, index: str) -> bool:
    """Create ``index`` with :data:`INDEX_MAPPING`, or add any new fields to it.

    Returns True if it was created, False if it already existed. When it already
    exists, new top-level fields in :data:`INDEX_MAPPING` are added via a mapping
    update (ES merges additive field mappings without a reindex) — so a schema
    addition like ``raw_sha256`` becomes queryable on the existing index. Existing
    documents pick up the new mapping when they are next (re)indexed.
    """
    if not client.indices.exists(index=index):
        client.indices.create(
            index=index,
            mappings=INDEX_MAPPING["mappings"],
            settings=INDEX_MAPPING["settings"],
        )
        return True
    client.indices.put_mapping(
        index=index, properties=INDEX_MAPPING["mappings"]["properties"]
    )
    # Idempotently apply the dynamic settings (e.g. max_inner_result_window) to an
    # existing index so a pre-existing index picks up the raised claim window too.
    client.indices.put_settings(index=index, settings=INDEX_MAPPING["settings"])
    return False


class ElasticsearchIndexer:
    """A :class:`~goldberg_system.sinks.base.Sink` that indexes into Elasticsearch."""

    def __init__(
        self,
        client: Any,
        index: str,
        *,
        registry: ExclusionRegistry | None = None,
        alert_log: Path | str | None = None,
    ) -> None:
        self.client = client
        self.index = index
        # The exclusion registry (defense in depth). ``None`` at direct construction
        # means "empty" (consulting it is a no-op) so unit tests stay side-effect-free;
        # ``from_env`` loads the tracked default so production is always guarded.
        self.registry = registry if registry is not None else ExclusionRegistry()
        self.alert_log = alert_log

    @property
    def name(self) -> str:
        return f"elasticsearch:{self.index}"

    def ensure_index(self) -> bool:
        return ensure_index(self.client, self.index)

    def _refusal(self, document: EnrichedDocument) -> tuple[str, str, str | None] | None:
        """``(alert_reason, sink_detail, category)`` if it must NOT be indexed, else None.

        Two loud guards, in order of authority:
        1. a registered **deliberate exclusion** (a purge under undertaking) — refused
           even when the document carries NO ``no_index`` sidecar (the class fault:
           sidecars patched the known four, but the RULE must hold for any future
           deliberate exclusion). The registry records no category, so it defaults to the
           safer ``legally_obligatory`` (a purge under undertaking IS legally obligatory);
           then
        2. a ``no_index`` sidecar on the document itself — carrying its resolved
           ``no_index_category`` so the alarm's severity matches the exclusion class.
        """
        hit = self.registry.match(
            raw_path=document.raw_path, raw_sha256=document.metadata.raw_sha256
        )
        if hit is not None:
            reason = f"deliberately excluded (registry): {hit.reason}"
            return reason, (
                f"refusing to index deliberately-excluded document: "
                f"{document.raw_path} ({hit.reason})"
            ), None  # None → legally_obligatory (a purge under undertaking)
        if document.metadata.no_index:
            why = document.metadata.no_index_reason or "(no reason given)"
            return f"no_index: {why}", (
                f"refusing to index no_index document: {document.raw_path} ({why})"
            ), document.metadata.no_index_category
        return None

    def write(self, document: EnrichedDocument) -> SinkResult:
        # Defense in depth (the LOUD path): a restricted / deliberately-excluded document
        # must never be written to the search index. If one reaches here the quiet ingest
        # skip was bypassed, so we refuse it as a FAILED result AND alarm (severity-aware:
        # CRITICAL for legally_obligatory, WARNING for housekeeping + a named status
        # alert) — silence on a re-add is itself the fault.
        refusal = self._refusal(document)
        if refusal is not None:
            alert_reason, detail, category = refusal
            record_blocked_reingest(
                document.raw_path,
                alert_reason,
                source=self.name,
                category=category,
                log_path=self.alert_log,
            )
            return SinkResult(sink=self.name, ok=False, detail=detail)
        try:
            self.client.index(
                index=self.index,
                id=document.doc_id,
                document=to_es_document(document),
            )
            return SinkResult(sink=self.name, ok=True)
        except Exception as exc:  # noqa: BLE001 - report any backend failure
            return SinkResult(sink=self.name, ok=False, detail=str(exc))

    def remove_by_raw_path(self, raw_path: str) -> int:
        """Delete every indexed document whose ``raw_path`` equals ``raw_path``.

        Used to tombstone a stale entry when its raw file has vanished from
        goldberg-raw. Returns the number of documents deleted (0 if none / on error).
        """
        try:
            resp = self.client.delete_by_query(
                index=self.index,
                query={"term": {"raw_path": raw_path}},
                refresh=True,
            )
            return int((resp or {}).get("deleted", 0))
        except Exception as exc:  # noqa: BLE001 - a failed tombstone must not crash ingest
            log.warning("stale-entry removal failed for %s: %s", raw_path, exc)
            return 0

    @classmethod
    def from_env(cls) -> ElasticsearchIndexer:
        """Build from ``GOLDBERG_ES_URL`` / ``GOLDBERG_ES_INDEX`` (with defaults).

        Loads the tracked exclusion registry and points alerts at the durable log so a
        production indexer is always guarded and always alarms visibly.
        """
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        from elasticsearch import Elasticsearch

        from goldberg_system.observability.restricted_alert import default_alert_log_path

        url = os.environ.get("GOLDBERG_ES_URL", "http://192.168.86.31:9200")
        index = os.environ.get("GOLDBERG_ES_INDEX", "goldberg_documents")
        return cls(
            Elasticsearch(url),
            index,
            registry=ExclusionRegistry.load_default(),
            alert_log=default_alert_log_path(),
        )
