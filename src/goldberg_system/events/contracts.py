"""The NATS event contracts exchanged by the pipeline.

``goldberg.raw.ingested`` is published when raw content is ready to process. It is
**source-agnostic**: it may be produced by the Halob filesystem watcher or bridged
from Papra's ``document:created`` webhook (ADR 0003), in which case it also carries
the Papra ``document_id``. ``goldberg.indexed`` is published once a document is
fully processed.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from goldberg_system.metadata.schema import DocumentMetadata


class EventSource(str, Enum):
    """Which producer emitted a ``goldberg.raw.ingested`` event."""

    WATCHER = "watcher"  # Halob filesystem watcher
    PAPRA = "papra"  # bridged from a Papra document:created webhook


class RawIngestedEvent(BaseModel):
    """Published when a raw document is available to process."""

    model_config = ConfigDict(extra="forbid")

    subject: ClassVar[str] = "goldberg.raw.ingested"

    raw_path: str
    raw_commit: str
    mime_type: str | None = None
    source: EventSource = EventSource.WATCHER
    papra_document_id: str | None = None
    doc_id: str | None = None
    metadata: DocumentMetadata | None = None


class IndexedEvent(BaseModel):
    """Published when a document has been fully extracted, enriched and indexed."""

    model_config = ConfigDict(extra="forbid")

    subject: ClassVar[str] = "goldberg.indexed"

    doc_id: str
    raw_path: str
    raw_commit: str
    matters: list[str] = []
