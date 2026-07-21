"""Per-document trace (ADR 0008 §6) — *why did X not ingest?*

Reads the ``goldberg_pipeline_events`` projection for one document (matched by
``doc_id``, ``raw_path``, or ``sha256``) and returns its stage timeline in order, so
you can see where it stopped and why.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TraceEvent(BaseModel):
    ts: str
    stage: str
    status: str
    component: str | None = None
    reason: str | None = None
    error: str | None = None


def _match_query(key: str) -> dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"term": {"doc_id": key}},
                {"term": {"raw_path": key}},
                {"term": {"sha256": key}},
            ],
            "minimum_should_match": 1,
        }
    }


def read_trace(
    client: Any, key: str, *, index: str = "goldberg_pipeline_events", size: int = 100
) -> list[TraceEvent]:
    """Return the *complete* ordered event timeline for a document identified by ``key``.

    ``key`` matches any of ``doc_id`` / ``raw_path`` / ``sha256``. Because a document's
    stages carry different identifiers (the ``received`` event has the Papra filename;
    ``indexed`` has the real ``raw_path`` + ``doc_id``) but share a stable ``sha256``,
    we first resolve ``key`` to its ``sha256`` set, then fetch every event for those
    hashes — so the timeline is never fragmented by which identifier you looked up.
    """
    seed = client.search(
        index=index, query=_match_query(key), size=size, source_includes=["sha256"]
    )
    shas = {
        h["_source"].get("sha256")
        for h in seed["hits"]["hits"]
        if h["_source"].get("sha256")
    }
    if not shas:
        # no sha256 on the matched events — fall back to the direct match
        query: dict[str, Any] = _match_query(key)
    else:
        query = {"terms": {"sha256": list(shas)}}

    resp = client.search(
        index=index, query=query, sort=[{"ts": {"order": "asc"}}], size=size
    )
    return [TraceEvent(**hit["_source"]) for hit in resp["hits"]["hits"]]
