"""Corpus query layer over the Elasticsearch index.

Retrieval primitives an agent (Claude Code) uses to answer questions about the
corpus with citations: full-text ``search`` (BM25 with filters), attributed
``claims`` search (the "who asserted what" nested query), ``get`` a document by
id, and ``facets`` for orientation. The agent runs these and synthesises an
attributed answer — the tools do retrieval, not answer-generation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DocHit(BaseModel):
    """A search hit — enough to cite the document."""

    doc_id: str
    raw_path: str | None = None
    summary: str | None = None
    document_type: str | None = None
    matters: list[str] = []
    author: str | None = None
    date: str | None = None
    ingested_at: str | None = None
    score: float | None = None
    highlights: list[str] = []


class ClaimHit(BaseModel):
    """A matched attributed claim, with its source document."""

    doc_id: str
    raw_path: str | None = None
    subject: str
    predicate: str
    object: str
    asserted_by: str | None = None


class WikiHit(BaseModel):
    """A hit in the SilverBullet concept wiki — a synthesised page, not raw evidence.

    The wiki is a *second representation* of the corpus (ADR 0007): curated,
    cross-linked concept/entity pages. Searching it alongside the document index
    surfaces synthesised context the raw documents don't state in one place.
    """

    path: str  # e.g. "entities/simon-goldberg.md"
    title: str | None = None
    layer: str | None = None  # entity | concept | comparison | query | summary | …
    type: str | None = None
    tags: list[str] = []
    sources: list[str] = []  # raw_path citations back into the corpus
    outbound_links: list[str] = []
    score: float | None = None
    highlights: list[str] = []


def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class CorpusQuery:
    """Query the goldberg Elasticsearch index."""

    def __init__(self, client: Any, index: str, wiki_index: str | None = None) -> None:
        self.client = client
        self.index = index
        self.wiki_index = wiki_index or "silverbullet-goldberg"

    @classmethod
    def from_env(cls) -> CorpusQuery:
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        from elasticsearch import Elasticsearch

        url = os.environ.get("GOLDBERG_ES_URL", "http://192.168.86.31:9200")
        index = os.environ.get("GOLDBERG_ES_INDEX", "goldberg_documents")
        wiki_index = os.environ.get("GOLDBERG_WIKI_INDEX", "silverbullet-goldberg")
        return cls(Elasticsearch(url), index, wiki_index)

    def search(
        self,
        text: str | None = None,
        *,
        matters: str | list[str] | None = None,
        author: str | None = None,
        document_type: str | None = None,
        size: int = 10,
    ) -> list[DocHit]:
        """Full-text search (BM25) over content/summary, with optional filters."""
        if text:
            must: list[dict[str, Any]] = [
                {
                    "multi_match": {
                        "query": text,
                        "fields": [
                            "content",
                            "summary^2",
                            "long_summary",
                            "keywords^2",
                            "entities",
                        ],
                    }
                }
            ]
        else:
            must = [{"match_all": {}}]
        filters: list[dict[str, Any]] = []
        if matters:
            filters.append({"terms": {"matters": _as_list(matters)}})
        if author:
            filters.append({"term": {"author": author}})
        if document_type:
            filters.append({"term": {"document_type": document_type}})

        resp = self.client.search(
            index=self.index,
            query={"bool": {"must": must, "filter": filters}},
            size=size,
            highlight={
                "fields": {"content": {"fragment_size": 160, "number_of_fragments": 2}}
            },
        )
        hits: list[DocHit] = []
        for hit in resp["hits"]["hits"]:
            src = hit.get("_source", {})
            hits.append(
                DocHit(
                    doc_id=src.get("doc_id", hit.get("_id", "")),
                    raw_path=src.get("raw_path"),
                    summary=src.get("summary"),
                    document_type=src.get("document_type"),
                    matters=src.get("matters", []),
                    author=src.get("author"),
                    date=src.get("date"),
                    ingested_at=src.get("ingested_at"),
                    score=hit.get("_score"),
                    highlights=hit.get("highlight", {}).get("content", []),
                )
            )
        return hits

    def wiki(
        self,
        text: str | None = None,
        *,
        layer: str | None = None,
        tags: str | list[str] | None = None,
        size: int = 10,
    ) -> list[WikiHit]:
        """Full-text search the SilverBullet concept wiki (the synthesised view).

        Searches ``silverbullet-goldberg`` over page title/body, optionally filtered
        by ``layer`` (entity/concept/comparison/…) or ``tags``. Excludes archived
        and raw-mirror pages so only synthesised knowledge is returned.
        """
        if text:
            must: list[dict[str, Any]] = [
                {
                    "multi_match": {
                        "query": text,
                        "fields": ["title^3", "body", "tags^2"],
                    }
                }
            ]
        else:
            must = [{"match_all": {}}]
        filters: list[dict[str, Any]] = []
        if layer:
            filters.append({"term": {"layer": layer}})
        if tags:
            filters.append({"terms": {"tags": _as_list(tags)}})
        must_not = [{"terms": {"layer": ["raw", "archive"]}}]

        resp = self.client.search(
            index=self.wiki_index,
            query={"bool": {"must": must, "filter": filters, "must_not": must_not}},
            size=size,
            highlight={
                "fields": {"body": {"fragment_size": 160, "number_of_fragments": 2}}
            },
        )
        hits: list[WikiHit] = []
        for hit in resp["hits"]["hits"]:
            src = hit.get("_source", {})
            hits.append(
                WikiHit(
                    path=src.get("path", hit.get("_id", "")),
                    title=src.get("title"),
                    layer=src.get("layer"),
                    type=src.get("type"),
                    tags=src.get("tags", []),
                    sources=src.get("sources", []),
                    outbound_links=src.get("outbound_links", []),
                    score=hit.get("_score"),
                    highlights=hit.get("highlight", {}).get("body", []),
                )
            )
        return hits

    def claims(
        self,
        *,
        asserted_by: str | None = None,
        subject: str | None = None,
        object: str | None = None,
        text: str | None = None,
        matters: str | list[str] | None = None,
        size: int = 20,
    ) -> list[ClaimHit]:
        """Search attributed claims (nested) — who asserted what about whom."""
        nested_must: list[dict[str, Any]] = []
        if asserted_by:
            nested_must.append({"term": {"claims.asserted_by": asserted_by}})
        if subject:
            nested_must.append({"match": {"claims.subject": subject}})
        if object:
            nested_must.append({"match": {"claims.object": object}})
        if text:
            nested_must.append(
                {
                    "multi_match": {
                        "query": text,
                        "fields": [
                            "claims.subject",
                            "claims.predicate",
                            "claims.object",
                        ],
                    }
                }
            )
        nested = {
            "nested": {
                "path": "claims",
                "query": {"bool": {"must": nested_must or [{"match_all": {}}]}},
                "inner_hits": {"size": 25},
            }
        }
        filters: list[dict[str, Any]] = []
        if matters:
            filters.append({"terms": {"matters": _as_list(matters)}})

        resp = self.client.search(
            index=self.index,
            query={"bool": {"must": [nested], "filter": filters}},
            size=size,
        )
        results: list[ClaimHit] = []
        for hit in resp["hits"]["hits"]:
            src = hit.get("_source", {})
            inner = (
                hit.get("inner_hits", {})
                .get("claims", {})
                .get("hits", {})
                .get("hits", [])
            )
            for claim_hit in inner:
                c = claim_hit.get("_source", {})
                results.append(
                    ClaimHit(
                        doc_id=src.get("doc_id", hit.get("_id", "")),
                        raw_path=src.get("raw_path"),
                        subject=c.get("subject", ""),
                        predicate=c.get("predicate", ""),
                        object=c.get("object", ""),
                        asserted_by=c.get("asserted_by"),
                    )
                )
        return results

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document's full source (content + metadata) by id."""
        if not self.client.exists(index=self.index, id=doc_id):
            return None
        return dict(self.client.get(index=self.index, id=doc_id)["_source"])

    def facets(
        self,
        fields: tuple[str, ...] = ("matters", "author", "document_type", "parties"),
        size: int = 20,
    ) -> dict[str, list[tuple[str, int]]]:
        """Terms aggregations over the given fields (for orientation)."""
        aggs = {f: {"terms": {"field": f, "size": size}} for f in fields}
        resp = self.client.search(index=self.index, size=0, aggs=aggs)
        out: dict[str, list[tuple[str, int]]] = {}
        for field in fields:
            buckets = resp.get("aggregations", {}).get(field, {}).get("buckets", [])
            out[field] = [(b["key"], b["doc_count"]) for b in buckets]
        return out
