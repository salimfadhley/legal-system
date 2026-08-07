"""Corpus query layer over the Elasticsearch index.

Retrieval primitives an agent (Claude Code) uses to answer questions about the
corpus with citations: full-text ``search`` (BM25 with filters), attributed
``claims`` search (the "who asserted what" nested query), ``get`` a document by
id, and ``facets`` for orientation. The agent runs these and synthesises an
attributed answer — the tools do retrieval, not answer-generation.
"""

from __future__ import annotations

import os
from itertools import combinations
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# Max atomic claims retrieved per document via nested ``inner_hits``. A document that
# decomposes into more than this many claims would silently lose the overflow, dropping
# candidate claims from contradiction detection. 1000 is a safe high cap for legal
# documents (full nested pagination is out of scope — raising the cap is the accepted
# fix). NB the OUTER ``size`` (candidate *documents*, default 2000) is a separate cap.
_INNER_HITS_CLAIM_CAP = 1000


def _norm(text: str | None) -> str:
    """Normalize a subject/predicate/object for comparison: lowercased, whitespace-collapsed."""
    return " ".join((text or "").lower().split())


def _conflict_kind(a: Any, b: Any) -> str | None:
    """Why two same-(subject,predicate) claims conflict, or None if they agree.

    ``opposite_polarity`` — one asserts, the other negates (the deterministic
    contradiction). ``conflicting_object`` — both assert (same polarity) but name
    different objects, i.e. the account changed. Same polarity + same object = no
    conflict (mutually corroborating).
    """
    if a.polarity != b.polarity:
        return "opposite_polarity"
    if _norm(a.object) != _norm(b.object):
        return "conflicting_object"
    return None


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


# Aliases of one person → a canonical key, so a within-speaker contradiction hunt runs
# ACROSS the labels that fragment a single account (a party may appear under several
# matter-specific labels, and the seam between the labels is exactly where the
# cross-matter contradictions live). Unknown names fall through to their normalized form.
#
# PUBLIC-REPO HYGIENE: real party names must NEVER be committed here. The map is loaded
# at runtime from an untracked (gitignored) config file; when the file is absent the map
# is EMPTY and ``_canonical_speaker`` degrades to plain normalization (names map to
# themselves). Ship no real names in code or in any tracked file.
_SPEAKER_ALIASES_ENV = "GOLDBERG_SPEAKER_ALIASES"
_DEFAULT_ALIASES_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "speaker-aliases.yaml"
)


def load_speaker_aliases(path: Path | str | None = None) -> dict[str, str]:
    """Load the alias→canonical speaker map (normalized) from a config file.

    The file is a simple YAML mapping of ``alias: canonical`` (e.g. a party's several
    labels all mapping to one canonical key). Keys and values are normalized with
    :func:`_norm`. Returns ``{}`` when the file is absent — the safe public default, so
    no real names are required for the query layer to work. An explicit ``path`` (or the
    ``GOLDBERG_SPEAKER_ALIASES`` env var) overrides the repo-root default location.
    """
    if path is None:
        path = os.environ.get(_SPEAKER_ALIASES_ENV) or _DEFAULT_ALIASES_PATH
    p = Path(path)
    if not p.exists():
        return {}
    import yaml

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    return {_norm(str(k)): _norm(str(v)) for k, v in raw.items() if k and v}


# Loaded once at import from the untracked config file (empty by default — see above).
_SPEAKER_ALIASES: dict[str, str] = load_speaker_aliases()


def _canonical_speaker(name: str | None) -> str | None:
    if not name:
        return None
    return _SPEAKER_ALIASES.get(_norm(name), _norm(name))


class ClaimRef(BaseModel):
    """A single claim with just enough to cite and compare it (contradiction detection)."""

    doc_id: str
    raw_path: str | None = None
    subject: str
    predicate: str
    object: str
    asserted_by: str | None = None
    polarity: bool = True
    claim_date: str | None = None
    source_span: str | None = None  # the sentence the claim came from (casework must quote it)


class ContradictionPair(BaseModel):
    """Two claims on the same normalized (subject, predicate) that conflict.

    ``kind`` is ``opposite_polarity`` (one asserts, one negates) or
    ``conflicting_object`` (same subject+predicate, incompatible object).
    """

    asserted_by: str | None = None  # the shared speaker (within-speaker) or None (contested)
    subject: str
    predicate: str
    kind: str
    left: ClaimRef
    right: ClaimRef


class Contradictions(BaseModel):
    """The result of :meth:`CorpusQuery.contradictions`.

    ``within_speaker`` is one speaker's account shifting over time — an integrity
    signal worth surfacing loudly. ``contested`` is two *different* speakers
    disagreeing, which is normal adversarial reality, **not a defect** — returned
    separately and clearly labelled so it is never mistaken for a data problem.
    """

    within_speaker: list[ContradictionPair] = []
    contested: list[ContradictionPair] = []


def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class CorpusQuery:
    """Query the goldberg Elasticsearch index."""

    def __init__(self, client: Any, index: str) -> None:
        self.client = client
        self.index = index

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
        return cls(Elasticsearch(url), index)

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

    def _fetch_claim_refs(
        self,
        *,
        subject: str | None = None,
        matters: str | list[str] | None = None,
        size: int = 2000,
        exclude_analysis: bool = False,
        paths: str | list[str] | None = None,
    ) -> list[ClaimRef]:
        """Retrieve candidate claims (with polarity/claim_date) for pairing in Python.

        A single nested ``match_all`` query returns every document's claims via
        ``inner_hits``; ``subject``/``matters`` narrow the candidate set. Retrieval is
        ES; the pairing is Python (see :meth:`contradictions` for why).
        """
        nested_must: list[dict[str, Any]] = []
        if subject:
            nested_must.append({"match": {"claims.subject": subject}})
        nested = {
            "nested": {
                "path": "claims",
                "query": {"bool": {"must": nested_must or [{"match_all": {}}]}},
                "inner_hits": {"size": _INNER_HITS_CLAIM_CAP},
            }
        }
        filters: list[dict[str, Any]] = []
        if matters:
            filters.append({"terms": {"matters": _as_list(matters)}})
        if paths:
            filters.append({
                "bool": {
                    "should": [{"prefix": {"raw_path": p}} for p in _as_list(paths)],
                    "minimum_should_match": 1,
                }
            })
        bool_q: dict[str, Any] = {"must": [nested], "filter": filters}
        if exclude_analysis:
            # Opt-out only. Our own work product (analysis/) is INCLUDED by default so
            # the detector can flag where a deliverable contradicts the evidence — the
            # highest-value check. Callers pass exclude_analysis=True to hide drafting
            # noise when they only want evidence-vs-evidence conflicts.
            bool_q["must_not"] = [{"prefix": {"raw_path": "analysis/"}}]
        resp = self.client.search(
            index=self.index,
            query={"bool": bool_q},
            size=size,
        )
        refs: list[ClaimRef] = []
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
                refs.append(
                    ClaimRef(
                        doc_id=src.get("doc_id", hit.get("_id", "")),
                        raw_path=src.get("raw_path"),
                        subject=c.get("subject", ""),
                        predicate=c.get("predicate", ""),
                        object=c.get("object", ""),
                        asserted_by=c.get("asserted_by"),
                        polarity=c.get("polarity", True),
                        claim_date=c.get("claim_date"),
                        source_span=c.get("source_span"),
                    )
                )
        return refs

    def contradictions(
        self,
        *,
        subject: str | None = None,
        matters: str | list[str] | None = None,
        size: int = 2000,
        exclude_analysis: bool = False,
        paths: str | list[str] | None = None,
    ) -> Contradictions:
        """Find conflicting claims on the same normalized (subject, predicate).

        **Approach — ES retrieval, Python pairing.** Elasticsearch has no self-join,
        so a "same (subject, predicate), opposite polarity" conflict cannot be
        expressed as a single query that returns the *pair*. We therefore pull the
        candidate claims with one nested query (:meth:`_fetch_claim_refs`) and pair
        them in Python: group by normalized (subject, predicate), then within each
        group flag any two claims that either have **opposite polarity** or an
        **incompatible object**.

        The split is by ``asserted_by``:

        - **within a single speaker** → ``within_speaker`` — that speaker's account
          shifted over time; an integrity signal worth surfacing.
        - **across speakers** → ``contested`` — normal adversarial disagreement,
          returned separately and labelled "contested (not a defect)".

        Limits: pairing is O(n²) within a (subject, predicate) group, so it relies on
        those groups being small (they are — a subject+predicate is specific). It
        needs ``asserted_by`` populated to attribute a shift; claims with no speaker
        are skipped for the within-speaker pass. It is only as good as the extracted
        ``polarity`` and the surface-form normalization — paraphrase that changes the
        subject/predicate wording will not group (a later embedding/NLI stage, ADR
        0001, is the follow-up for fuzzy paraphrase).
        """
        refs = self._fetch_claim_refs(
            subject=subject, matters=matters, size=size,
            exclude_analysis=exclude_analysis, paths=paths,
        )

        # Group by the normalized (subject, predicate) — the axis a contradiction
        # lives on. Object/polarity are what then conflict *within* a group.
        groups: dict[tuple[str, str], list[ClaimRef]] = {}
        for ref in refs:
            groups.setdefault((_norm(ref.subject), _norm(ref.predicate)), []).append(ref)

        within: list[ContradictionPair] = []
        contested: list[ContradictionPair] = []
        for (subj, pred), members in groups.items():
            for a, b in combinations(members, 2):
                kind = _conflict_kind(a, b)
                if kind is None:
                    continue
                a_can = _canonical_speaker(a.asserted_by)
                b_can = _canonical_speaker(b.asserted_by)
                # Same *person* — across alias labels — is a within-speaker shift.
                same_speaker = a_can is not None and a_can == b_can
                if same_speaker:
                    within.append(
                        ContradictionPair(
                            # display the left side's original label; left/right carry
                            # each side's real label (they may differ across aliases).
                            asserted_by=a.asserted_by,
                            subject=subj,
                            predicate=pred,
                            kind=kind,
                            left=a,
                            right=b,
                        )
                    )
                else:
                    contested.append(
                        ContradictionPair(
                            asserted_by=None,
                            subject=subj,
                            predicate=pred,
                            kind=kind,
                            left=a,
                            right=b,
                        )
                    )
        return Contradictions(within_speaker=within, contested=contested)

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
