"""Tests for P1 cross-audience candidate pairing."""

from __future__ import annotations

from goldberg_system.audience import COURT, NEIDLE, POLICE
from goldberg_system.nli.pairing import (
    SeamClaim,
    candidate_pairs,
    lexical_similarity,
)


def _c(doc_id: str, audience: str, subject: str, object: str, *, speaker: str = "simon goldberg", predicate: str = "says") -> SeamClaim:
    return SeamClaim(
        doc_id=doc_id, raw_path=f"evidence/{doc_id}", audience=audience,
        canonical_speaker=speaker, subject=subject, predicate=predicate, object=object,
        source_span=f"{subject} {predicate} {object}",
    )


def test_lexical_similarity_overlap_and_stopwords() -> None:
    a = _c("a", POLICE, "the Met inquiry", "was paused")
    b = _c("b", COURT, "the Met investigation", "no action")
    # shares "met"; "the"/"was"/"no" are stopwords → non-trivial but < 1
    s = lexical_similarity(a, b)
    assert 0.0 < s < 1.0
    # identical subject+object → 1.0
    assert lexical_similarity(_c("x", POLICE, "police action", "none"),
                              _c("y", COURT, "police action", "none")) == 1.0
    # disjoint → 0.0
    assert lexical_similarity(_c("x", POLICE, "vat registration", "no"),
                              _c("y", COURT, "trial bundle", "late")) == 0.0


def test_only_cross_audience_same_speaker_pairs() -> None:
    claims = [
        _c("p1", POLICE, "the police inquiry", "was paused at his request"),
        _c("c1", COURT, "the police inquiry", "took no effective action"),
        _c("p2", POLICE, "the police inquiry", "was paused again"),  # same audience as p1
        _c("n1", NEIDLE, "the police inquiry", "was ongoing", speaker="dan neidle"),  # other speaker
    ]
    pairs = candidate_pairs(claims, min_similarity=0.2)
    # p1<->c1 is the only cross-audience, same-speaker, similar pair
    ids = {(p.left.doc_id, p.right.doc_id) for p in pairs}
    flat = {frozenset(t) for t in ids}
    assert frozenset({"p1", "c1"}) in flat
    # p1<->p2 excluded (same audience); n1 excluded (different speaker)
    assert frozenset({"p1", "p2"}) not in flat
    assert all("n1" not in t for t in flat)


def test_pairs_ranked_and_capped() -> None:
    claims = [
        _c("p1", POLICE, "loan repayment terms", "seven days"),
        _c("c1", COURT, "loan repayment terms", "twenty one days"),  # high overlap
        _c("c2", COURT, "loan repayment schedule", "monthly"),        # lower overlap
    ]
    pairs = candidate_pairs(claims, min_similarity=0.1, max_pairs=1)
    assert len(pairs) == 1  # capped
    # the highest-similarity pair (shared "loan repayment terms") wins the single slot
    assert {pairs[0].left.doc_id, pairs[0].right.doc_id} == {"p1", "c1"}


def test_pluggable_similarity_function() -> None:
    # a custom scorer (e.g. an embedding cosine stub) is honoured
    claims = [_c("p1", POLICE, "x", "y"), _c("c1", COURT, "totally", "different")]
    always = candidate_pairs(claims, min_similarity=0.5, similarity=lambda a, b: 0.9)
    assert len(always) == 1
    never = candidate_pairs(claims, min_similarity=0.5, similarity=lambda a, b: 0.1)
    assert never == []
