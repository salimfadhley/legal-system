"""Re-enrich re-applies folder metadata.yaml authority (claim_source) without re-ingest.

The ``goldberg re-enrich --raw-root`` path re-reads each document's folder
``metadata.yaml`` chain from the raw tree and re-applies its authoritative fields
(notably ``claim_source``, which overrides every claim's LLM-inferred ``asserted_by``)
before enriching — so a casework metadata edit takes effect without a full Docling
re-ingest. These tests exercise the extracted core (``re_enrich_document``) against a
fake ES ``_source`` and a tmp raw tree, plus the ``--raw-root`` resolution.
"""

from __future__ import annotations

from pathlib import Path

from goldberg_system.config import resolve_raw_root
from goldberg_system.enrichment.adapter import EnrichmentResult
from goldberg_system.extracted import enriched_from_es_source
from goldberg_system.metadata.schema import Claim
from goldberg_system.pipeline import re_enrich_document


class _FakeEnricher:
    """Returns claims whose asserted_by is the LLM's (context-blind) inference."""

    def enrich(self, request):  # type: ignore[no-untyped-def]
        return EnrichmentResult(
            summary="s",
            claims=[
                Claim(subject="prosecutor", predicate="is", object="ETP",
                      asserted_by="LLM Guess A"),
                Claim(subject="witness", predicate="saw", object="nothing",
                      asserted_by="LLM Guess B"),
            ],
        )


def _es_source(raw_path: str) -> dict:
    """A minimal ES `_source` for an already-extracted document (no claim_source)."""
    return {
        "doc_id": "d1",
        "raw_path": raw_path,
        "content": "some extracted body text",
        "author": "Inferred Author",
    }


def test_reenrich_applies_folder_claim_source_over_inferred(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / "analysis").mkdir(parents=True)
    (raw_root / "analysis" / "metadata.yaml").write_text(
        "claim_source: deep research\nauthor: Our Analyst\n"
    )
    (raw_root / "analysis" / "note.md").write_text("body")

    base = enriched_from_es_source(_es_source("analysis/note.md"))
    doc = re_enrich_document(base, _FakeEnricher(), raw_root=raw_root)

    # every claim now carries the folder's authoritative speaker, not the LLM guess
    assert [c.asserted_by for c in doc.metadata.claims] == ["deep research", "deep research"]
    assert doc.metadata.claim_source == "deep research"
    # a corrected folder author overrides the ES-stored inferred author too
    assert doc.metadata.author == "Our Analyst"


def test_reenrich_without_raw_root_keeps_inferred_attribution(tmp_path: Path) -> None:
    base = enriched_from_es_source(_es_source("analysis/note.md"))
    doc = re_enrich_document(base, _FakeEnricher(), raw_root=None)
    # today's behaviour: no folder re-resolution, LLM inference preserved
    assert [c.asserted_by for c in doc.metadata.claims] == ["LLM Guess A", "LLM Guess B"]
    assert doc.metadata.claim_source is None


def test_reenrich_folder_without_claim_source_is_noop_for_attribution(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / "emails").mkdir(parents=True)
    # a mixed-party folder deliberately sets no claim_source
    (raw_root / "emails" / "metadata.yaml").write_text("document_type: email\n")
    base = enriched_from_es_source(_es_source("emails/thread.eml"))
    doc = re_enrich_document(base, _FakeEnricher(), raw_root=raw_root)
    assert [c.asserted_by for c in doc.metadata.claims] == ["LLM Guess A", "LLM Guess B"]
    assert doc.metadata.document_type == "email"  # folder field still applied


def test_resolve_raw_root_prefers_explicit(tmp_path: Path) -> None:
    assert resolve_raw_root(tmp_path) == tmp_path
    assert resolve_raw_root(str(tmp_path)) == tmp_path


def test_resolve_raw_root_env_fallback(tmp_path: Path, monkeypatch) -> None:
    # With project config unresolvable, GOLDBERG_RAW_ROOT is the fallback.
    monkeypatch.setenv("GOLDBERG_PROJECTS_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("GOLDBERG_RAW_ROOT", str(tmp_path / "rawdir"))
    assert resolve_raw_root(None) == tmp_path / "rawdir"
    monkeypatch.delenv("GOLDBERG_RAW_ROOT")
    assert resolve_raw_root(None) is None
