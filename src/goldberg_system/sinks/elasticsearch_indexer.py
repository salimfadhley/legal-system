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

from typing import Any

from goldberg_system.identity import compute_content_hash
from goldberg_system.sinks.base import EnrichedDocument, SinkResult

INDEX_MAPPING: dict[str, Any] = {
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
            "ingested_at": {"type": "date"},
            "raw_path": {"type": "keyword"},
            "raw_commit": {"type": "keyword"},
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
        "ingested_at": meta.ingested_at,
        "raw_path": meta.raw_path,
        "raw_commit": meta.raw_commit,
        "papra_document_id": meta.papra_document_id,
        "relates_to": meta.relates_to,
        "handling": meta.handling.model_dump(mode="json"),
        "claims": [c.model_dump(mode="json") for c in meta.claims],
    }
    return {k: v for k, v in es.items() if v is not None and v != []}


def ensure_index(client: Any, index: str) -> bool:
    """Create ``index`` with :data:`INDEX_MAPPING` if it does not exist.

    Returns True if it was created, False if it already existed.
    """
    if client.indices.exists(index=index):
        return False
    client.indices.create(index=index, mappings=INDEX_MAPPING["mappings"])
    return True


class ElasticsearchIndexer:
    """A :class:`~goldberg_system.sinks.base.Sink` that indexes into Elasticsearch."""

    def __init__(self, client: Any, index: str) -> None:
        self.client = client
        self.index = index

    @property
    def name(self) -> str:
        return f"elasticsearch:{self.index}"

    def ensure_index(self) -> bool:
        return ensure_index(self.client, self.index)

    def write(self, document: EnrichedDocument) -> SinkResult:
        try:
            self.client.index(
                index=self.index,
                id=document.doc_id,
                document=to_es_document(document),
            )
            return SinkResult(sink=self.name, ok=True)
        except Exception as exc:  # noqa: BLE001 - report any backend failure
            return SinkResult(sink=self.name, ok=False, detail=str(exc))

    @classmethod
    def from_env(cls) -> ElasticsearchIndexer:
        """Build from ``GOLDBERG_ES_URL`` / ``GOLDBERG_ES_INDEX`` (with defaults)."""
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        from elasticsearch import Elasticsearch

        url = os.environ.get("GOLDBERG_ES_URL", "http://192.168.86.31:9200")
        index = os.environ.get("GOLDBERG_ES_INDEX", "goldberg_documents")
        return cls(Elasticsearch(url), index)
