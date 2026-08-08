"""The sink interface.

A *sink* is a destination for a fully enriched document: the goldberg-extracted
writer and the Elasticsearch indexer (built in M4) each
implement :class:`Sink`. Defining it here lets M4 writers and the M5 service be
built against a stable contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from goldberg_system.metadata.schema import DocumentMetadata


class EnrichedDocument(BaseModel):
    """A document ready to be persisted/indexed by a sink."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    raw_path: str
    raw_commit: str
    markdown: str
    metadata: DocumentMetadata


class SinkResult(BaseModel):
    """The outcome of writing one document to one sink."""

    model_config = ConfigDict(extra="forbid")

    sink: str
    ok: bool
    detail: str | None = None


@runtime_checkable
class Sink(Protocol):
    """A destination for enriched documents (implemented in M4)."""

    @property
    def name(self) -> str:
        """A stable identifier for this sink (used in logs/results)."""
        ...

    def write(self, document: EnrichedDocument) -> SinkResult:
        """Persist/index ``document``; return a :class:`SinkResult`."""
        ...


@runtime_checkable
class SupportsRawPathDeletion(Protocol):
    """A sink that can remove a stale record by its ``raw_path``.

    Used to tombstone an indexed document whose raw file has been deleted from
    goldberg-raw: an indexed doc citing a raw_path that can no longer be opened reads
    as fabrication, so the stale entry must be removed when the source vanishes.
    """

    def remove_by_raw_path(self, raw_path: str) -> int:
        """Delete every record whose ``raw_path`` equals ``raw_path``; return the count."""
        ...
