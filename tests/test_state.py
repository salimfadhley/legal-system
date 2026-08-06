"""Tests for the SystemState aggregator (M12/M13, ADR 0009)."""

from __future__ import annotations

from typing import Any

import yaml

from goldberg_system.observability.state import SystemState, aggregate


class _StateES:
    """A fake ES that routes by index + shape of the request."""

    def __init__(self) -> None:
        self.counts = {
            "goldberg_documents": 42,
            "goldberg_pipeline_events": 200,
        }

    def count(self, index: str) -> dict[str, Any]:
        return {"count": self.counts.get(index, 0)}

    def search(self, **kw: Any) -> dict[str, Any]:
        aggs = kw.get("aggs") or {}
        # stage/status nested agg
        if "stage" in aggs:
            return {
                "aggregations": {
                    "stage": {
                        "buckets": [
                            {
                                "key": "indexed",
                                "status": {"buckets": [{"key": "ok", "doc_count": 40}]},
                            },
                            {
                                "key": "extracted",
                                "status": {
                                    "buckets": [{"key": "skipped", "doc_count": 2}]
                                },
                            },
                            {
                                "key": "indexed",
                                "status": {
                                    "buckets": [{"key": "failed", "doc_count": 1}]
                                },
                            },
                        ]
                    }
                }
            }
        # terms agg (by_matter / by_type / by_layer)
        if "t" in aggs:
            return {
                "aggregations": {
                    "t": {"buckets": [{"key": "422500059892", "doc_count": 30}]}
                }
            }
        # recent failures/skips, or last_indexed
        q = kw.get("query", {})
        # windowed failure count (bool filter with a ts range) → hits.total
        bool_q = q.get("bool", {})
        clauses = bool_q.get("filter", []) + bool_q.get("must", [])
        if any("range" in c for c in clauses):
            return {"hits": {"total": {"value": 1}}}
        if q.get("terms", {}).get("status") == ["failed"]:
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "ts": "t",
                                "stage": "indexed",
                                "status": "failed",
                                "raw_path": "a.pdf",
                                "reason": "boom",
                            }
                        }
                    ]
                }
            }
        if q.get("terms", {}).get("status") == ["skipped"]:
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "ts": "t",
                                "stage": "extracted",
                                "status": "skipped",
                                "raw_path": "b.eml",
                            }
                        }
                    ]
                }
            }
        # last_indexed_at
        return {"hits": {"hits": [{"_source": {"ts": "2026-07-21T12:00:00Z"}}]}}


def test_aggregate_builds_system_state() -> None:
    state = aggregate(_StateES())
    assert isinstance(state, SystemState)
    assert state.corpus["documents"] == 42
    assert state.pipeline["by_stage_status"]["indexed/ok"] == 40
    assert state.pipeline["by_stage_status"]["indexed/failed"] == 1
    assert state.pipeline["last_indexed_at"] == "2026-07-21T12:00:00Z"
    # a failure present → health degraded
    assert state.health["status"] == "degraded"
    assert state.dlq["failed"] == 1 and state.dlq["skipped"] == 2


def test_system_state_yaml_mode_roundtrips() -> None:
    state = aggregate(_StateES())
    loaded = yaml.safe_load(state.to_yaml())
    # the LLM-readable mode is the same data as the model
    assert loaded["corpus"]["documents"] == 42
    assert loaded["health"]["status"] == "degraded"
    assert "by_stage_status" in loaded["pipeline"]


def test_aggregate_healthy_when_no_failures() -> None:
    es = _StateES()

    # a variant with no failed events → health ok
    def search(**kw: Any) -> dict[str, Any]:
        if (kw.get("aggs") or {}).get("stage"):
            return {
                "aggregations": {
                    "stage": {
                        "buckets": [
                            {
                                "key": "indexed",
                                "status": {"buckets": [{"key": "ok", "doc_count": 40}]},
                            }
                        ]
                    }
                }
            }
        if (kw.get("aggs") or {}).get("t"):
            return {"aggregations": {"t": {"buckets": []}}}
        if kw.get("query", {}).get("terms", {}).get("status") in (
            ["failed"],
            ["skipped"],
        ):
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [{"_source": {"ts": "2026-07-21T12:00:00Z"}}]}}

    es.search = search  # type: ignore[method-assign]
    state = aggregate(es)
    assert state.health["status"] == "ok"


def _check(state: SystemState, name: str) -> dict[str, Any]:
    return next(c for c in state.health["checks"] if c["name"] == name)


def test_no_recent_failures_ignores_old_failures_outside_window() -> None:
    """Historical failed events must NOT trip the check — only recent ones (FR-009)."""
    es = _StateES()

    def search(**kw: Any) -> dict[str, Any]:
        aggs = kw.get("aggs") or {}
        if "stage" in aggs:
            # there ARE historical failures in the stage/status counts …
            return {
                "aggregations": {
                    "stage": {
                        "buckets": [
                            {
                                "key": "indexed",
                                "status": {
                                    "buckets": [{"key": "failed", "doc_count": 7}]
                                },
                            }
                        ]
                    }
                }
            }
        if "t" in aggs:
            return {"aggregations": {"t": {"buckets": []}}}
        q = kw.get("query", {})
        bool_q = q.get("bool", {})
        clauses = bool_q.get("filter", []) + bool_q.get("must", [])
        if any("range" in c for c in clauses):
            # … but zero of them fall inside the recent window
            return {"hits": {"total": {"value": 0}}}
        if q.get("terms", {}).get("status") in (["failed"], ["skipped"]):
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [{"_source": {"ts": "2026-07-21T12:00:00Z"}}]}}

    es.search = search  # type: ignore[method-assign]
    state = aggregate(es)
    assert _check(state, "no_recent_failures")["ok"] is True
    assert state.dlq["failed"] == 7  # historical count still surfaced in the DLQ view
    assert state.health["status"] == "ok"


def test_no_recent_failures_trips_on_recent_failure() -> None:
    """A failure inside the window trips the check and shows the window (FR-009)."""
    state = aggregate(_StateES())  # fake returns total=1 for the windowed query
    check = _check(state, "no_recent_failures")
    assert check["ok"] is False
    assert "24h" in check["detail"]
