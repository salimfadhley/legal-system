"""The enrichment adapter boundary — where Mind of Steele is reused.

M1 does not wire the real Mind of Steele implementation (that is M3). It defines
the *contract* the pipeline enriches against: an :class:`EnrichmentAdapter` that
turns an extracted document into an attributed :class:`EnrichmentResult` (summary,
keywords, entities, the ``author``/speaker, and comparable attributed *claims*
that power both attributed Q&A and contradiction detection).

See ``doc/reuse/mind_of_steele.md`` for how MoS is resolved.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from goldberg_system.metadata.schema import DocumentMetadata


class EnrichmentRequest(BaseModel):
    """Input to enrichment: an extracted document plus what we already know."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    markdown: str
    metadata: DocumentMetadata


class Claim(BaseModel):
    """An attributed assertion extracted from a document.

    Comparable across the corpus so contradictions (a party's account shifting
    over time) become queryable.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str
    asserted_by: str | None = None  # the speaker/author making the claim


class EnrichmentResult(BaseModel):
    """The attributed output of enrichment."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    keywords: list[str] = []
    entities: list[str] = []
    author: str | None = None  # source_party / speaker
    claims: list[Claim] = []


@runtime_checkable
class EnrichmentAdapter(Protocol):
    """The boundary M3 wires Mind of Steele's ``llm_support`` behind."""

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        """Produce an attributed :class:`EnrichmentResult` for ``request``."""
        ...
