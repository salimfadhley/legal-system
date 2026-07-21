"""Tests for the Elasticsearch indexer sink (via a fake ES client)."""

from __future__ import annotations

from typing import Any

from goldberg_system.metadata import Claim, DocumentMetadata, Origin, Role
from goldberg_system.sinks import (
    ElasticsearchIndexer,
    EnrichedDocument,
    INDEX_MAPPING,
    Sink,
    ensure_index,
    to_es_document,
)


class _FakeIndices:
    def __init__(self, exists: bool = False) -> None:
        self._exists = exists
        self.created: list[dict[str, Any]] = []

    def exists(self, index: str) -> bool:
        return self._exists

    def create(self, index: str, mappings: dict[str, Any]) -> None:
        self.created.append({"index": index, "mappings": mappings})
        self._exists = True


class _FakeES:
    def __init__(self, exists: bool = False) -> None:
        self.indices = _FakeIndices(exists)
        self.indexed: list[dict[str, Any]] = []

    def index(self, index: str, id: str, document: dict[str, Any]) -> None:
        self.indexed.append({"index": index, "id": id, "document": document})


def _doc() -> EnrichedDocument:
    return EnrichedDocument(
        doc_id="gb_abc",
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="c0ffee",
        markdown="The extracted body text about the prosecution.",
        metadata=DocumentMetadata(
            summary="short",
            matters=["422500059892", "648MC011"],
            author="Simon Goldberg",
            origin=Origin.RECEIVED,
            role=Role.INPUT,
            raw_path="evidence/cps/x/msg.eml",
            raw_commit="c0ffee",
            ingested_at="2026-07-21T10:00:00+00:00",
            claims=[
                Claim(
                    subject="prosecuting_entity",
                    predicate="is",
                    object="Empower the People",
                    asserted_by="Simon Goldberg",
                )
            ],
        ),
    )


def test_indexer_satisfies_sink_protocol() -> None:
    assert isinstance(ElasticsearchIndexer(_FakeES(), "goldberg_documents"), Sink)


def test_to_es_document_maps_the_rich_fields() -> None:
    es = to_es_document(_doc())
    assert es["doc_id"] == "gb_abc"
    assert es["content"].startswith("The extracted")
    assert es["content_hash"]  # computed
    assert es["matters"] == ["422500059892", "648MC011"]
    assert es["author"] == "Simon Goldberg"
    assert es["origin"] == "received"
    assert es["ingested_at"] == "2026-07-21T10:00:00+00:00"
    assert es["raw_path"] == "evidence/cps/x/msg.eml"
    # claims are structured (nested) with attribution
    assert es["claims"][0]["asserted_by"] == "Simon Goldberg"
    assert es["claims"][0]["object"] == "Empower the People"
    # handling flags present with safe defaults
    assert es["handling"]["cpia_s17"] is True


def test_write_uses_doc_id_as_es_id() -> None:
    es = _FakeES()
    indexer = ElasticsearchIndexer(es, "goldberg_documents")
    result = indexer.write(_doc())
    assert result.ok is True
    assert es.indexed[0]["id"] == "gb_abc"  # deterministic id → idempotent
    assert es.indexed[0]["index"] == "goldberg_documents"


def test_ensure_index_creates_when_absent() -> None:
    es = _FakeES(exists=False)
    assert ensure_index(es, "goldberg_documents") is True
    assert es.indices.created[0]["index"] == "goldberg_documents"
    # idempotent: second call sees it exists
    assert ensure_index(es, "goldberg_documents") is False


def test_mapping_has_nested_claims_and_keyword_matters() -> None:
    props = INDEX_MAPPING["mappings"]["properties"]
    assert props["claims"]["type"] == "nested"
    assert props["matters"]["type"] == "keyword"
    assert props["content"]["type"] == "text"
