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
            "silverbullet-goldberg": 107,
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
    assert state.wiki["pages"] == 107
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
