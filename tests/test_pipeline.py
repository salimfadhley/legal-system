"""Tests for the pipeline orchestration / Papra backfill."""

from __future__ import annotations

from goldberg_system.enrichment import Claim, EnrichmentResult
from goldberg_system.papra import PapraDocument
from goldberg_system.pipeline import (
    backfill_from_papra,
    build_enriched_document,
)
from goldberg_system.sinks import EnrichedDocument, SinkResult


class _FakeEnricher:
    def enrich(self, request):  # type: ignore[no-untyped-def]
        return EnrichmentResult(
            summary=f"summary of {request.doc_id}",
            keywords=["k"],
            entities=["E"],
            author="Speaker",
            claims=[
                Claim(subject="s", predicate="p", object="o", asserted_by="Speaker")
            ],
        )


class _FakeSink:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.written: list[EnrichedDocument] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document: EnrichedDocument) -> SinkResult:
        self.written.append(document)
        return SinkResult(sink=self.name, ok=self.ok)


class _FakePapra:
    def __init__(self, docs: dict[str, str]) -> None:
        # id -> content ("" means empty/unextracted)
        self._docs = docs

    def list_documents(self, *, page_index=0, page_size=200, search=None):  # type: ignore[no-untyped-def]
        return [PapraDocument(id=i, original_name=f"{i}.pdf") for i in self._docs]

    def get_document(self, document_id: str) -> PapraDocument:
        return PapraDocument(
            id=document_id,
            original_name=f"{document_id}.pdf",
            content=self._docs[document_id],
        )


def test_build_enriched_document_sets_provenance_and_enriches() -> None:
    papra_doc = PapraDocument(id="doc_1", original_name="letter.pdf")
    ed = build_enriched_document(papra_doc, "Body text.", _FakeEnricher())
    assert ed.metadata.raw_path == "letter.pdf"
    assert ed.metadata.papra_document_id == "doc_1"
    assert ed.metadata.summary == "summary of doc_1"
    assert ed.metadata.claims[0].asserted_by == "Speaker"
    assert ed.doc_id.startswith("gb_")
    # deterministic
    ed2 = build_enriched_document(papra_doc, "Body text.", _FakeEnricher())
    assert ed.doc_id == ed2.doc_id


def test_backfill_indexes_docs_with_content_and_skips_empty() -> None:
    papra = _FakePapra({"a": "content a", "b": "", "c": "content c"})
    sink = _FakeSink()
    report = backfill_from_papra(papra, _FakeEnricher(), [sink])
    assert report.processed == 3
    assert report.indexed == 2
    assert report.skipped_empty == 1
    assert report.failures == 0
    assert {d.metadata.papra_document_id for d in sink.written} == {"a", "c"}


def test_backfill_counts_sink_failures() -> None:
    papra = _FakePapra({"a": "content a"})
    report = backfill_from_papra(papra, _FakeEnricher(), [_FakeSink(ok=False)])
    assert report.indexed == 0
    assert report.failures == 1


def test_backfill_respects_max_docs() -> None:
    papra = _FakePapra({"a": "x", "b": "y", "c": "z"})
    report = backfill_from_papra(papra, _FakeEnricher(), [_FakeSink()], max_docs=1)
    assert report.processed == 1
