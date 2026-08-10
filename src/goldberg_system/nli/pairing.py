"""P1 — cross-audience candidate pairing for the NLI seam detector.

Generate the candidate PAIRS that P2's NLI pass will score. A candidate is two claims by
the SAME canonical speaker, from DIFFERENT audiences (the seam), that are plausibly about
the same thing — measured by subject similarity. Pairing within one audience is normal
repetition and is skipped; the cross-audience seam is the signal.

Pure and side-effect free: :func:`candidate_pairs` takes an in-memory list of
:class:`SeamClaim` and returns ranked :class:`CandidatePair`s. The similarity function is
PLUGGABLE — the default :func:`lexical_similarity` (token Jaccard, no API) is a zero-cost
first cut; the intended production scorer is embedding cosine over subject+object, swapped
in via the ``similarity`` argument once an embeddings endpoint is available. The pairing
structure (seam, same-speaker, threshold, ranking, output shape) is identical either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Callable

# Light stopword list so trivial function words ("the", "a", "of") don't inflate the
# lexical overlap. Deliberately small — this is a first-cut scorer, not the final one.
_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "by", "with",
     "is", "was", "are", "were", "be", "been", "that", "this", "it", "as", "at",
     "from", "his", "her", "their", "any", "no", "not"}
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str | None) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


@dataclass(frozen=True)
class SeamClaim:
    """One claim positioned on the cross-audience seam, ready to pair.

    ``audience`` and ``canonical_speaker`` are the seam coordinates; ``source_span`` is
    the quotable sentence P2 will run NLI over; ``doc_id``/``raw_path`` make every hit a
    one-click verification (guardrail 2).
    """

    doc_id: str
    raw_path: str | None
    audience: str
    canonical_speaker: str
    subject: str
    predicate: str
    object: str
    source_span: str | None = None
    claim_date: str | None = None


@dataclass(frozen=True)
class CandidatePair:
    """Two same-speaker, cross-audience claims plausibly about the same thing.

    A CANDIDATE for the NLI pass, NOT a contradiction — P2 decides that and casework
    verifies it. ``similarity`` is the subject-similarity score that surfaced the pair.
    """

    left: SeamClaim
    right: SeamClaim
    similarity: float

    @property
    def audiences(self) -> tuple[str, str]:
        return (self.left.audience, self.right.audience)


def lexical_similarity(a: SeamClaim, b: SeamClaim) -> float:
    """Token-Jaccard over each claim's subject+object — the default (no-API) scorer.

    A first-cut proxy for "are these about the same thing". It catches lexical overlap
    ("the Met inquiry" vs "the Met investigation") but MISSES paraphrase ("no action"
    vs "paused at his request") — which is exactly why P2 runs a semantic NLI pass and
    why an embedding scorer is the intended replacement for this function. Returns 0..1.
    """
    ta = _tokens(a.subject) | _tokens(a.object)
    tb = _tokens(b.subject) | _tokens(b.object)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def candidate_pairs(
    claims: list[SeamClaim],
    *,
    min_similarity: float = 0.2,
    similarity: Callable[[SeamClaim, SeamClaim], float] = lexical_similarity,
    max_pairs: int | None = None,
) -> list[CandidatePair]:
    """Rank cross-audience, same-speaker candidate pairs by subject similarity.

    A pair qualifies when the two claims share a ``canonical_speaker``, sit in DIFFERENT
    ``audience``s (the seam), and score ``>= min_similarity``. Results are sorted by
    similarity descending (highest-confidence candidates first); ``max_pairs`` caps the
    worklist P2 will score. Pure — no I/O, no API.
    """
    out: list[CandidatePair] = []
    for a, b in combinations(claims, 2):
        if a.canonical_speaker != b.canonical_speaker:
            continue
        if a.audience == b.audience:  # within-audience = repetition, not the seam
            continue
        score = similarity(a, b)
        if score >= min_similarity:
            out.append(CandidatePair(left=a, right=b, similarity=score))
    out.sort(key=lambda p: p.similarity, reverse=True)
    return out[:max_pairs] if max_pairs is not None else out
