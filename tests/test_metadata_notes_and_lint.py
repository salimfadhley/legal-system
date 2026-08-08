"""Notes → enricher context + fenced indexed content, and the ``metadata lint`` self-test.

Covers doc/system/metadata.md deliverables 3 and 5, plus the five casework acceptance
cases (encoded as tests where feasible; the enricher is stubbed so no network is used).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goldberg_system.enrichment.adapter import (
    EnrichmentRequest,
    EnrichmentResult,
)
from goldberg_system.enrichment.openai_enricher import OpenAIEnricher
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.metadata.sidecar import (
    ANNOTATION_CLOSE,
    ANNOTATION_OPEN,
    SelfTestError,
    append_annotation,
    build_annotation,
    lint_root,
    run_selftest,
)
from goldberg_system.pipeline import build_enriched_from_raw
from goldberg_system.sinks.elasticsearch_indexer import to_es_document


class _StubEnricher:
    """Records the request it saw and returns a fixed (or note-derived) result."""

    def __init__(self, result: EnrichmentResult | None = None) -> None:
        self.seen: EnrichmentRequest | None = None
        self._result = result or EnrichmentResult(summary="stub")

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        self.seen = request
        return self._result


# --- deliverable 3a: notes reach the enricher as authoritative context ------------


def test_notes_prepended_to_enricher_prompt() -> None:
    enricher = OpenAIEnricher(client=object(), model="gpt-4o-mini")
    req = EnrichmentRequest(
        doc_id="d",
        markdown="the document body",
        metadata=DocumentMetadata(notes="This is Exhibit SM/01; dates are the police timeline."),
    )
    messages = enricher._build_messages(req)
    user = messages[-1]["content"]
    assert "AUTHORITATIVE CONTEXT" in user
    assert "Exhibit SM/01" in user
    # the note comes BEFORE the document body in the prompt
    assert user.index("Exhibit SM/01") < user.index("the document body")


def test_no_notes_means_no_context_block() -> None:
    enricher = OpenAIEnricher(client=object(), model="gpt-4o-mini")
    req = EnrichmentRequest(
        doc_id="d", markdown="body", metadata=DocumentMetadata()
    )
    user = enricher._build_messages(req)[-1]["content"]
    assert "AUTHORITATIVE CONTEXT" not in user


# --- deliverable 3b: notes appended to indexed content, FENCED ---------------------


def test_annotation_fence_exact_format() -> None:
    fenced = build_annotation("hello")
    assert fenced == f"{ANNOTATION_OPEN}\nhello\n{ANNOTATION_CLOSE}"
    assert fenced.startswith("[ANNOTATION — casework, not part of this document]")
    assert fenced.endswith("[/ANNOTATION]")


def test_append_annotation_noop_without_notes() -> None:
    assert append_annotation("body", None) == "body"
    assert append_annotation("body", "   ") == "body"


def test_notes_fenced_into_indexed_content() -> None:
    enricher = _StubEnricher()
    doc = build_enriched_from_raw(
        "evidence/a.txt",
        "the original extracted text",
        enricher,
        base=DocumentMetadata(notes="Treat garbled figures as OCR noise."),
    )
    # the fence is present in the stored/indexed markdown, after the body
    assert ANNOTATION_OPEN in doc.markdown
    assert "Treat garbled figures as OCR noise." in doc.markdown
    assert doc.markdown.index("original extracted text") < doc.markdown.index(ANNOTATION_OPEN)
    # the note is also a first-class stored field
    assert doc.metadata.notes == "Treat garbled figures as OCR noise."
    # ES indexes the note as its own searchable field
    es = to_es_document(doc)
    assert es["notes"] == "Treat garbled figures as OCR noise."
    assert ANNOTATION_OPEN in es["content"]


def test_doc_id_stable_across_note_change() -> None:
    enricher = _StubEnricher()
    without = build_enriched_from_raw(
        "evidence/a.txt", "body text", enricher, base=DocumentMetadata()
    )
    withnote = build_enriched_from_raw(
        "evidence/a.txt", "body text", enricher, base=DocumentMetadata(notes="a note")
    )
    # adding a note must not mint a new document identity
    assert without.doc_id == withnote.doc_id


# --- deliverable 5: metadata lint + its self-test ---------------------------------


def test_lint_selftest_passes_on_real_code() -> None:
    # the embedded self-test proves the linter flags bad and passes good
    run_selftest()  # must not raise


def test_lint_flags_bad_and_passes_good(tmp_path: Path) -> None:
    (tmp_path / "good.pdf").write_text("x")
    (tmp_path / "good.pdf.metadata.yaml").write_text("author: A\nnotes: n\n")
    (tmp_path / "bad.pdf").write_text("x")
    (tmp_path / "bad.pdf.metadata.yaml").write_text("authour: A\n")
    # orphan sidecar (no target file)
    (tmp_path / "gone.pdf.metadata.yaml").write_text("author: A\n")
    # no_index without reason
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "metadata.yaml").write_text("no_index: true\n")

    findings = lint_root(tmp_path)
    by_path = {f.path: f for f in findings}
    assert by_path["good.pdf.metadata.yaml"].ok is True
    assert by_path["bad.pdf.metadata.yaml"].ok is False
    assert "unknown key" in by_path["bad.pdf.metadata.yaml"].detail
    assert by_path["gone.pdf.metadata.yaml"].ok is False
    assert "orphan" in by_path["gone.pdf.metadata.yaml"].detail
    assert by_path["folder/metadata.yaml"].ok is False
    assert "no_index_reason" in by_path["folder/metadata.yaml"].detail


def test_lint_warns_on_authoritative_field_without_method(tmp_path: Path) -> None:
    # author/claim_source set WITHOUT a method → a warning (ok stays True, level == "warn"),
    # because an unexplained override is a guess indistinguishable from a check.
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "a.pdf.metadata.yaml").write_text("author: Someone\n")
    (tmp_path / "b.pdf").write_text("x")
    (tmp_path / "b.pdf.metadata.yaml").write_text("claim_source: Someone\nmethod: read at source\n")

    findings = lint_root(tmp_path)
    by_path = {f.path: f for f in findings}
    warn = by_path["a.pdf.metadata.yaml"]
    assert warn.ok is True and warn.level == "warn"
    assert "without a method" in warn.detail
    # with a method present, no warning — the file is clean
    assert by_path["b.pdf.metadata.yaml"].level == "error"  # the default "ok" finding
    assert by_path["b.pdf.metadata.yaml"].ok is True


def test_lint_error_suppresses_the_method_warning(tmp_path: Path) -> None:
    # a hard error must not be overwritten by the warning in a path→finding map
    (tmp_path / "bad.pdf").write_text("x")
    (tmp_path / "bad.pdf.metadata.yaml").write_text("author: Someone\nbadkey: 1\n")
    findings = lint_root(tmp_path)
    by_path = {f.path: f for f in findings}
    assert by_path["bad.pdf.metadata.yaml"].ok is False


def test_lint_refuses_to_report_if_selftest_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import goldberg_system.metadata.sidecar as sc

    def _boom() -> None:
        raise SelfTestError("simulated broken linter")

    monkeypatch.setattr(sc, "_selftest_lint", _boom)
    with pytest.raises(SelfTestError):
        sc.run_selftest()
