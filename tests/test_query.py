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


def test_contradictions_include_analysis_by_default() -> None:
    # analysis/ (our own work-product) is IN scope by default so the detector can flag
    # a deliverable that contradicts the evidence — no must_not on the query.
    es = _FakeES()
    CorpusQuery(es, "idx").contradictions()
    assert es.last_search is not None
    assert "must_not" not in es.last_search["query"]["bool"]


def test_contradictions_can_opt_out_of_analysis() -> None:
    es = _FakeES()
    CorpusQuery(es, "idx").contradictions(exclude_analysis=True)
    assert es.last_search is not None
    assert {"prefix": {"raw_path": "analysis/"}} in es.last_search["query"]["bool"][
        "must_not"
    ]


def _claim_doc(doc_id: str, raw_path: str, claim: dict[str, Any]) -> dict[str, Any]:
    """A search hit carrying one nested claim via inner_hits (contradiction fixture)."""
    return {
        "_id": doc_id,
        "_source": {"doc_id": doc_id, "raw_path": raw_path},
        "inner_hits": {"claims": {"hits": {"hits": [{"_source": claim}]}}},
    }


def test_contradictions_splits_within_speaker_from_contested() -> None:
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_a",
                    "evidence/a.pdf",
                    {
                        "subject": "s.5 PfHA",
                        "predicate": "was",
                        "object": "in force",
                        "asserted_by": "Goldberg",
                        "polarity": True,
                        "claim_date": "2026-01-01",
                    },
                ),
                _claim_doc(
                    "gb_b",
                    "evidence/b.pdf",
                    {
                        "subject": "s.5 PfHA",
                        "predicate": "was",
                        "object": "in force",
                        "asserted_by": "Goldberg",
                        "polarity": False,  # same speaker, opposite polarity
                        "claim_date": "2026-03-01",
                    },
                ),
                _claim_doc(
                    "gb_c",
                    "evidence/c.pdf",
                    {
                        "subject": "s.5 PfHA",
                        "predicate": "was",
                        "object": "in force",
                        "asserted_by": "Defence",  # different speaker → contested
                        "polarity": False,
                        "claim_date": "2026-02-01",
                    },
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()

    # Goldberg's own account flipped polarity over time → an integrity signal.
    assert len(result.within_speaker) == 1
    within = result.within_speaker[0]
    assert within.asserted_by == "Goldberg"
    assert within.kind == "opposite_polarity"
    assert {within.left.doc_id, within.right.doc_id} == {"gb_a", "gb_b"}
    assert {within.left.claim_date, within.right.claim_date} == {
        "2026-01-01",
        "2026-03-01",
    }

    # Goldberg (asserted) vs Defence (negated) → contested, NOT a defect.
    assert len(result.contested) == 1
    contested = result.contested[0]
    assert contested.asserted_by is None  # cross-speaker: no single owner
    assert {contested.left.doc_id, contested.right.doc_id} == {"gb_a", "gb_c"}


def test_contradictions_run_across_speaker_aliases(monkeypatch: Any) -> None:
    # The same person under two matter-specific labels; a within-speaker hunt must run
    # ACROSS them — that seam (criminal vs civil) is where the contradiction lives.
    # The alias map is loaded at runtime from an untracked config file (no real names in
    # the repo), so the test injects the aliases it needs directly.
    import goldberg_system.query as query_mod

    monkeypatch.setattr(
        query_mod,
        "_SPEAKER_ALIASES",
        {"simon goldberg": "goldberg", "simon john goldberg": "goldberg"},
    )
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_crim", "evidence/simon_goldberg/statement.pdf",
                    {
                        "subject": "the bundle", "predicate": "was", "object": "served",
                        "asserted_by": "Simon Goldberg", "polarity": True,
                        "source_span": "The bundle was served on 20 July.",
                    },
                ),
                _claim_doc(
                    "gb_civ", "evidence/deacon_v_goldberg/counterclaim.pdf",
                    {
                        "subject": "the bundle", "predicate": "was", "object": "served",
                        "asserted_by": "Simon John Goldberg", "polarity": False,
                        "source_span": "The bundle was NOT served.",
                    },
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert len(result.within_speaker) == 1  # caught as one person's shift, not contested
    assert len(result.contested) == 0
    pair = result.within_speaker[0]
    assert pair.kind == "opposite_polarity"
    # each side keeps its real (distinct) label, and both carry the quotable span
    assert {pair.left.asserted_by, pair.right.asserted_by} == {
        "Simon Goldberg", "Simon John Goldberg",
    }
    assert pair.left.source_span and pair.right.source_span


def test_contradictions_conflicting_object_within_speaker() -> None:
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_1",
                    "own/1.md",
                    {
                        "subject": "bundle size",
                        "predicate": "is",
                        "object": "384 pages",
                        "asserted_by": "us",
                        "polarity": True,
                    },
                ),
                _claim_doc(
                    "gb_2",
                    "own/2.md",
                    {
                        "subject": "Bundle Size",  # normalization: same subject
                        "predicate": "is",
                        "object": "76 pages",  # same polarity, different object
                        "asserted_by": "us",
                        "polarity": True,
                    },
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert len(result.within_speaker) == 1
    assert result.within_speaker[0].kind == "conflicting_object"
    assert result.contested == []


def test_contradictions_agreeing_claims_are_not_flagged() -> None:
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_1",
                    "a.md",
                    {
                        "subject": "x",
                        "predicate": "is",
                        "object": "y",
                        "asserted_by": "us",
                        "polarity": True,
                    },
                ),
                _claim_doc(
                    "gb_2",
                    "b.md",
                    {
                        "subject": "x",
                        "predicate": "is",
                        "object": "y",
                        "asserted_by": "us",
                        "polarity": True,
                    },
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert result.within_speaker == []
    assert result.contested == []


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


def test_load_speaker_aliases_empty_when_file_absent(tmp_path: Any) -> None:
    from goldberg_system.query import load_speaker_aliases

    missing = tmp_path / "does-not-exist.yaml"
    assert load_speaker_aliases(missing) == {}


def test_load_speaker_aliases_reads_and_normalizes(tmp_path: Any) -> None:
    from goldberg_system.query import load_speaker_aliases

    path = tmp_path / "speaker-aliases.yaml"
    # aliases with mixed case / whitespace must normalize to lowercase collapsed form
    path.write_text('"Alpha  Beta": Canonical Key\nGamma: Canonical Key\n')
    aliases = load_speaker_aliases(path)
    assert aliases == {"alpha beta": "canonical key", "gamma": "canonical key"}


def test_canonical_speaker_degrades_to_normalization_with_empty_map(
    monkeypatch: Any,
) -> None:
    # With no alias config, a name simply normalizes to itself (no real names required).
    import goldberg_system.query as query_mod

    monkeypatch.setattr(query_mod, "_SPEAKER_ALIASES", {})
    assert query_mod._canonical_speaker("  Some  Name ") == "some name"
    assert query_mod._canonical_speaker(None) is None


def test_fetch_claim_refs_uses_high_inner_hits_cap() -> None:
    # A doc can decompose into many atomic claims; the nested inner_hits cap must be high
    # enough not to silently drop them (regression: was 100).
    from goldberg_system.query import _INNER_HITS_CLAIM_CAP

    assert _INNER_HITS_CLAIM_CAP >= 1000
    es = _FakeES()
    CorpusQuery(es, "idx")._fetch_claim_refs()
    nested = es.last_search["query"]["bool"]["must"][0]["nested"]
    assert nested["inner_hits"]["size"] == _INNER_HITS_CLAIM_CAP


# --------------------------------------------------------------------------------- #
# precision guards (2026-08-10): suppress same-representation + negation-diff-object
# --------------------------------------------------------------------------------- #
def test_doc_stem_collapses_representations() -> None:
    from goldberg_system.query import _doc_stem

    base = "evidence/x/statement_of_costs"
    assert _doc_stem("evidence/x/statement_of_costs.md") == base
    assert _doc_stem("evidence/x/statement_of_costs_ocr.pdf") == base
    assert _doc_stem("evidence/x/pdf_original/statement_of_costs.PDF") == base
    # stacked extension
    assert _doc_stem("evidence/x/objection.ocr.txt") == "evidence/x/objection"
    # genuinely different leaves (page splits) stay distinct
    assert _doc_stem("evidence/x/ocr_deacon/p-1.txt") != _doc_stem(
        "evidence/x/ocr_deacon/p-16.txt"
    )


def test_same_document_stem_opposite_polarity_is_suppressed() -> None:
    # The identical claim extracted from a .pdf and its _ocr.pdf, with polarity flipped
    # by the OCR difference — a transcription artifact, not a contradiction.
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_pdf", "evidence/x/statement_of_costs.pdf",
                    {"subject": "the applicant", "predicate": "is",
                     "object": "VAT registered", "asserted_by": "Simon Goldberg",
                     "polarity": True, "source_span": "The Applicant is not VAT registered and cannot recover VAT on fees."},
                ),
                _claim_doc(
                    "gb_ocr", "evidence/x/statement_of_costs_ocr.pdf",
                    {"subject": "the applicant", "predicate": "is",
                     "object": "VAT registered", "asserted_by": "Simon Goldberg",
                     "polarity": False, "source_span": "The Applicant is not VAT registered and cannot recover VAT on fees or disbursements."},
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert result.within_speaker == []  # same document, different encoding → suppressed
    assert result.contested == []


def test_identical_source_span_across_paths_is_suppressed() -> None:
    # Different paths (page-split vs whole), but the SAME quoted sentence → same source.
    span = "the material sought is not necessary, relevant or proportionate."
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_1", "evidence/x/objection.pdf",
                    {"subject": "the material sought", "predicate": "is",
                     "object": "necessary", "asserted_by": "Katrina Jean Deacon",
                     "polarity": False, "source_span": span},
                ),
                _claim_doc(
                    "gb_2", "evidence/x/ocr_deacon/p-1.txt",
                    {"subject": "the material sought", "predicate": "is",
                     "object": "necessary", "asserted_by": "Katrina Jean Deacon",
                     "polarity": True, "source_span": span},
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert result.within_speaker == []


def test_negation_predicate_with_different_objects_is_suppressed() -> None:
    # "Goldberg does not identify X" vs "…does not identify Y" — two distinct
    # observations, not an opposite-polarity contradiction.
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "an_1", "analysis/a.tex",
                    {"subject": "goldberg", "predicate": "does not identify",
                     "object": "a false statement by Fadhley", "asserted_by": "deep research",
                     "polarity": True},
                ),
                _claim_doc(
                    "an_2", "analysis/b.tex",
                    {"subject": "goldberg", "predicate": "does not identify",
                     "object": "which meetings Deacon attended", "asserted_by": "deep research",
                     "polarity": False},
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert result.within_speaker == []


def test_negation_predicate_same_object_still_flagged() -> None:
    # A REAL contradiction on a negation predicate keeps the SAME object with opposite
    # polarity — must still surface (guard (b) must not over-suppress).
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_1", "evidence/police/statement.pdf",
                    {"subject": "goldberg", "predicate": "does not allege",
                     "object": "a joint enterprise", "asserted_by": "Simon Goldberg",
                     "polarity": True},
                ),
                _claim_doc(
                    "gb_2", "evidence/court/case_summary.pdf",
                    {"subject": "goldberg", "predicate": "does not allege",
                     "object": "a joint enterprise", "asserted_by": "Simon Goldberg",
                     "polarity": False},
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert len(result.within_speaker) == 1
    assert result.within_speaker[0].kind == "opposite_polarity"


def test_real_cross_document_contradiction_still_flagged() -> None:
    # Different documents, same object, opposite polarity, non-negation predicate —
    # the target signal; must survive both guards.
    resp = {
        "hits": {
            "hits": [
                _claim_doc(
                    "gb_police", "evidence/police/mg6c.pdf",
                    {"subject": "the campaign", "predicate": "was",
                     "object": "one orchestrated enterprise", "asserted_by": "Simon Goldberg",
                     "polarity": False, "source_span": "The reports are per-person and per-force."},
                ),
                _claim_doc(
                    "gb_court", "evidence/court/witness_statement.pdf",
                    {"subject": "the campaign", "predicate": "was",
                     "object": "one orchestrated enterprise", "asserted_by": "Simon Goldberg",
                     "polarity": True, "source_span": "It was one orchestrated campaign by three people."},
                ),
            ]
        }
    }
    result = CorpusQuery(_FakeES(resp), "idx").contradictions()
    assert len(result.within_speaker) == 1
    assert result.within_speaker[0].kind == "opposite_polarity"
    assert {result.within_speaker[0].left.doc_id, result.within_speaker[0].right.doc_id} == {
        "gb_police", "gb_court",
    }
