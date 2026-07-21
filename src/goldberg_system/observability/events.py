"""Pipeline events — the audit trail (ADR 0008 §1-3, §5).

Every stage boundary, for every document, emits a :class:`PipelineEvent`. This
increment projects events straight into an ES index (``goldberg_pipeline_events``);
the NATS JetStream backbone (durable streams + DLQ + replay) is a later increment
behind the same :class:`EventSink` interface, so wiring here does not change.

Emission is **best-effort**: a telemetry failure is swallowed, never allowed to break
the pipeline (a legal ingest must not fail because logging failed).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from goldberg_system.provenance import now_iso

# Pipeline stages, in order. A document should traverse these; where it *stops* is the
# answer to "why did X not ingest".
STAGES = ("received", "extracted", "enriched", "indexed", "wiki_authored")
# Event outcome at a stage.
STATUSES = ("started", "ok", "skipped", "failed")


class PipelineEvent(BaseModel):
    ts: str
    component: str  # live-service | backfill | wiki-sink | migrate
    stage: str  # one of STAGES
    status: str  # one of STATUSES
    run_id: str | None = None
    doc_id: str | None = None
    sha256: str | None = None
    raw_path: str | None = None
    attempt: int = 0
    reason: str | None = None  # human string for skipped/failed
    error: str | None = None  # structured error detail for failed

    @classmethod
    def make(
        cls, component: str, stage: str, status: str, **kw: Any
    ) -> "PipelineEvent":
        return cls(ts=now_iso(), component=component, stage=stage, status=status, **kw)


class EventSink(Protocol):
    """Where pipeline events go. ES now; NATS JetStream later (same interface)."""

    def emit(self, event: PipelineEvent) -> None: ...


class NullEventSink:
    """Default no-op sink — observability disabled, pipeline unaffected."""

    def emit(self, event: PipelineEvent) -> None:  # noqa: D401
        return None


EVENTS_INDEX_MAPPING: dict[str, Any] = {
    "properties": {
        "ts": {"type": "date"},
        "component": {"type": "keyword"},
        "stage": {"type": "keyword"},
        "status": {"type": "keyword"},
        "run_id": {"type": "keyword"},
        "doc_id": {"type": "keyword"},
        "sha256": {"type": "keyword"},
        "raw_path": {"type": "keyword"},
        "attempt": {"type": "integer"},
        "reason": {"type": "keyword"},  # keyword → aggregatable ("top failure reasons")
        "error": {"type": "text"},  # free-form detail, stays full-text
    }
}


class ElasticsearchEventSink:
    """Projects events into ``goldberg_pipeline_events`` (best-effort)."""

    def __init__(self, client: Any, index: str = "goldberg_pipeline_events") -> None:
        self.client = client
        self.index = index

    @classmethod
    def from_env(cls) -> "ElasticsearchEventSink":
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        from elasticsearch import Elasticsearch

        url = os.environ.get("GOLDBERG_ES_URL", "http://192.168.86.31:9200")
        index = os.environ.get("GOLDBERG_EVENTS_INDEX", "goldberg_pipeline_events")
        return cls(Elasticsearch(url), index)

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index):
            self.client.indices.create(index=self.index, mappings=EVENTS_INDEX_MAPPING)

    def emit(self, event: PipelineEvent) -> None:
        try:
            self.client.index(
                index=self.index, document=event.model_dump(exclude_none=True)
            )
        except Exception:  # noqa: BLE001 - telemetry must never break the pipeline
            pass


def safe_emit(sink: EventSink | None, event: PipelineEvent) -> None:
    """Emit if a sink is configured; never raise."""
    if sink is None:
        return
    try:
        sink.emit(event)
    except Exception:  # noqa: BLE001
        pass
