"""System state — one canonical model, two renderers (ADR 0009).

``aggregate()`` reads the observability sources (the document index and the pipeline
event projection) and assembles a :class:`SystemState`. That one object is rendered
as a human table (``goldberg status``) and as YAML
(``goldberg status --yaml``) so an LLM can grok the whole system in a single read —
the two modes render the *same* data, so they never drift.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from pydantic import BaseModel

from goldberg_system.provenance import now_iso

# Default window for the "no_recent_failures" health check: a failure older than this
# is history, not a live problem (FR-009 / SC-004).
DEFAULT_FAILURE_WINDOW_HOURS = 24.0


class HealthCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class SystemState(BaseModel):
    generated_at: str
    health: dict[str, Any]  # {status: ok|degraded|down, checks: [HealthCheck]}
    corpus: dict[str, Any]  # {documents, by_matter, by_type}
    pipeline: dict[str, Any]  # {by_stage_status, last_indexed_at, recent_failures}
    dlq: dict[str, Any]  # {failed, skipped, recent}

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(), sort_keys=False, allow_unicode=True)


def _count(client: Any, index: str) -> int:
    try:
        return int(client.count(index=index)["count"])
    except Exception:  # noqa: BLE001 - a missing index is reported as 0, not a crash
        return 0


def _terms(client: Any, index: str, field: str, size: int = 20) -> dict[str, int]:
    try:
        resp = client.search(
            index=index, size=0, aggs={"t": {"terms": {"field": field, "size": size}}}
        )
        return {b["key"]: b["doc_count"] for b in resp["aggregations"]["t"]["buckets"]}
    except Exception:  # noqa: BLE001
        return {}


def _stage_status_counts(client: Any, events_index: str) -> dict[str, int]:
    """`{"received/ok": n, "extracted/skipped": n, "indexed/failed": n, …}`."""
    try:
        resp = client.search(
            index=events_index,
            size=0,
            aggs={
                "stage": {
                    "terms": {"field": "stage", "size": 20},
                    "aggs": {"status": {"terms": {"field": "status", "size": 10}}},
                }
            },
        )
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for s in resp["aggregations"]["stage"]["buckets"]:
        for st in s["status"]["buckets"]:
            out[f"{s['key']}/{st['key']}"] = st["doc_count"]
    return out


def _recent(
    client: Any, events_index: str, statuses: list[str], size: int = 10
) -> list[dict]:
    try:
        resp = client.search(
            index=events_index,
            query={"terms": {"status": statuses}},
            sort=[{"ts": {"order": "desc"}}],
            size=size,
            source_includes=["ts", "stage", "status", "raw_path", "doc_id", "reason"],
        )
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception:  # noqa: BLE001
        return []


def _recent_failure_count(client: Any, events_index: str, window_hours: float) -> int:
    """Count ``failed`` events within the last ``window_hours`` (FR-009).

    A range query on the event timestamp (``ts``) so an old, long-resolved failure
    does not keep the check permanently red — only *recent* failures count.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    try:
        resp = client.search(
            index=events_index,
            size=0,
            query={
                "bool": {
                    "filter": [
                        {"term": {"status": "failed"}},
                        {"range": {"ts": {"gte": since}}},
                    ]
                }
            },
        )
        total = resp["hits"]["total"]
        return int(total["value"] if isinstance(total, dict) else total)
    except Exception:  # noqa: BLE001 - an unreadable events index counts as 0
        return 0


def _last_indexed_at(client: Any, events_index: str) -> str | None:
    try:
        resp = client.search(
            index=events_index,
            query={
                "bool": {
                    "must": [{"term": {"stage": "indexed"}}, {"term": {"status": "ok"}}]
                }
            },
            sort=[{"ts": {"order": "desc"}}],
            size=1,
            source_includes=["ts"],
        )
        hits = resp["hits"]["hits"]
        return hits[0]["_source"]["ts"] if hits else None
    except Exception:  # noqa: BLE001
        return None


def aggregate(
    client: Any,
    *,
    documents_index: str = "goldberg_documents",
    events_index: str = "goldberg_pipeline_events",
    failure_window_hours: float = DEFAULT_FAILURE_WINDOW_HOURS,
    registry: Any | None = None,
) -> SystemState:
    """Assemble the canonical :class:`SystemState` from the observability indices.

    ``registry`` (default: the tracked :func:`ExclusionRegistry.load_default`) drives the
    live ``restricted_reingest_blocked`` check; inject a hermetic registry in tests so no
    real file is read.
    """
    from goldberg_system.observability.restricted_alert import (
        RESTRICTED_REINGEST_CHECK,
        find_indexed_restricted,
        partition_by_severity,
        summarize_exposures,
    )

    if registry is None:
        from goldberg_system.exclusion_registry import ExclusionRegistry

        registry = ExclusionRegistry.load_default()

    docs = _count(client, documents_index)
    stage_status = _stage_status_counts(client, events_index)
    failures = _recent(client, events_index, ["failed"])
    skips = _recent(client, events_index, ["skipped"])
    last_indexed = _last_indexed_at(client, events_index)
    recent_failed = _recent_failure_count(client, events_index, failure_window_hours)

    n_failed = sum(v for k, v in stage_status.items() if k.endswith("/failed"))
    n_skipped = sum(v for k, v in stage_status.items() if k.endswith("/skipped"))

    # First-class ALARM, driven by a LIVE index query — NOT the historical alert log.
    # A restricted document correctly present-in-raw-and-absent-from-index is ROUTINE
    # (the protection working) and produces no hit here, so it can never degrade health.
    # Only a deliberately-excluded / no_index document ACTUALLY IN THE INDEX right now —
    # the real CPR-32.12 exposure — appears, and it always turns the check red.
    # Severity-aware: a ``legally_obligatory`` exposure is an INCIDENT that fails the
    # check (degrades health); a ``housekeeping`` one is NOISE — surfaced in the detail
    # but not degrading — so the two classes are a distinct, machine-readable signal.
    exposures = find_indexed_restricted(client, documents_index, registry)
    blocked_incidents, _blocked_noise = partition_by_severity(exposures)

    window = int(failure_window_hours)
    checks = [
        HealthCheck(name="documents_indexed", ok=docs > 0, detail=f"{docs} docs"),
        HealthCheck(
            name="no_recent_failures",
            ok=recent_failed == 0,
            detail=(
                f"{recent_failed} failed events in last {window}h"
                if recent_failed
                else "none"
            ),
        ),
        HealthCheck(
            name="events_flowing",
            ok=bool(stage_status),
            detail=(last_indexed or "no indexed events yet"),
        ),
        HealthCheck(
            name=RESTRICTED_REINGEST_CHECK,
            ok=not blocked_incidents,  # only a legally_obligatory INCIDENT degrades health
            detail=summarize_exposures(exposures),
        ),
    ]
    status = "ok" if all(c.ok for c in checks) else "degraded"

    return SystemState(
        generated_at=now_iso(),
        health={"status": status, "checks": [c.model_dump() for c in checks]},
        corpus={
            "documents": docs,
            "by_matter": _terms(client, documents_index, "matters"),
            "by_type": _terms(client, documents_index, "document_type"),
        },
        pipeline={
            "by_stage_status": stage_status,
            "last_indexed_at": last_indexed,
            "recent_failures": failures,
        },
        dlq={
            "failed": n_failed,
            "skipped": n_skipped,
            "recent": (failures + skips)[:10],
        },
    )
