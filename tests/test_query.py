"""Tests for the corpus query layer (via a fake ES client)."""

from __future__ import annotations

from typing import Any

from goldberg_system.query import CorpusQuery


class _FakeES:
    def __init__(
        self,
        search_resp: dict[str, Any] | None = None,
        get_resp: dict[str, Any] | None = None,
        exists: bool = True,
    ) -> None:
        self._search_resp = search_resp or {"hits": {"hits": []}}
        self._get_resp = get_resp or {"_source": {}}
        self._exists = exists
        self.last_search: dict[str, Any] | None = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.last_search = kwargs
        return self._search_resp

    def exists(self, index: str, id: str) -> bool:
        return self._exists

    def get(self, index: str, id: str) -> dict[str, Any]:
        return self._get_resp


def test_search_parses_hits_and_highlights() -> None:
    resp = {
        "hits": {
            "hits": [
                {
                    "_id": "gb_1",
                    "_score": 1.5,
                    "_source": {
                        "doc_id": "gb_1",
                        "raw_path": "evidence/a.pdf",
                        "summary": "a summary",
                        "matters": ["422500059892"],
                        "author": "Goldberg",
                        "document_type": "email",
                    },
                    "highlight": {"content": ["frag one", "frag two"]},
                }
            ]
        }
    }
    hits = CorpusQuery(_FakeES(resp), "idx").search("prosecutor")
    assert len(hits) == 1
    assert hits[0].doc_id == "gb_1"
    assert hits[0].author == "Goldberg"
    assert hits[0].matters == ["422500059892"]
    assert hits[0].score == 1.5
    assert hits[0].highlights == ["frag one", "frag two"]


def test_search_applies_filters() -> None:
    es = _FakeES()
    CorpusQuery(es, "idx").search(
        "x", matters=["422500059892"], author="Goldberg", document_type="email"
    )
    assert es.last_search is not None
    filters = es.last_search["query"]["bool"]["filter"]
    assert {"terms": {"matters": ["422500059892"]}} in filters
    assert {"term": {"author": "Goldberg"}} in filters
    assert {"term": {"document_type": "email"}} in filters


def test_claims_parses_inner_hits() -> None:
    resp = {
        "hits": {
            "hits": [
                {
                    "_id": "gb_1",
                    "_source": {"doc_id": "gb_1", "raw_path": "evidence/a.pdf"},
                    "inner_hits": {
                        "claims": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": {
                                            "subject": "prosecuting_entity",
                                            "predicate": "is",
                                            "object": "Empower the People",
                                            "asserted_by": "Goldberg",
                                        }
                                    }
                                ]
                            }
                        }
                    },
                }
            ]
        }
    }
    results = CorpusQuery(_FakeES(resp), "idx").claims(asserted_by="Goldberg")
    assert len(results) == 1
    assert results[0].asserted_by == "Goldberg"
    assert results[0].object == "Empower the People"
    assert results[0].doc_id == "gb_1"
    assert results[0].raw_path == "evidence/a.pdf"


def test_claims_builds_nested_query() -> None:
    es = _FakeES()
    CorpusQuery(es, "idx").claims(asserted_by="Goldberg", subject="prosecutor")
    assert es.last_search is not None
    nested = es.last_search["query"]["bool"]["must"][0]["nested"]
    assert nested["path"] == "claims"
    must = nested["query"]["bool"]["must"]
    assert {"term": {"claims.asserted_by": "Goldberg"}} in must


def test_get_returns_source_or_none() -> None:
    q_found = CorpusQuery(
        _FakeES(get_resp={"_source": {"doc_id": "gb_1", "content": "hi"}}, exists=True),
        "idx",
    )
    assert q_found.get("gb_1") == {"doc_id": "gb_1", "content": "hi"}

    q_missing = CorpusQuery(_FakeES(exists=False), "idx")
    assert q_missing.get("nope") is None


def test_facets_parses_buckets() -> None:
    resp = {
        "hits": {"hits": []},
        "aggregations": {
            "matters": {"buckets": [{"key": "422500059892", "doc_count": 3}]},
            "author": {"buckets": []},
            "document_type": {"buckets": [{"key": "email", "doc_count": 2}]},
            "parties": {"buckets": []},
        },
    }
    facets = CorpusQuery(_FakeES(resp), "idx").facets()
    assert facets["matters"] == [("422500059892", 3)]
    assert facets["document_type"] == [("email", 2)]
