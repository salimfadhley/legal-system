"""Unit tests for the hosted goldberg MCP server (ADR 0010).

The MCP tools are thin wrappers over the same core functions behind the CLI, so
these tests fake the core (no network, no Elasticsearch) and assert only that the
tool returns the expected structured shape. The ``component_health`` tool is the
doctor board exposed for MCP-capable agents (FR-003 / C-003): it must reuse
``observability.health.run_doctor`` and return the ``DoctorReport`` as a dict.
"""

from __future__ import annotations

from types import SimpleNamespace

from goldberg_system.mcp import server
from goldberg_system.observability.health import (
    ComponentHealth,
    ComponentStatus,
    DoctorReport,
)


def _fake_report() -> DoctorReport:
    return DoctorReport(
        generated_at="2026-07-22T00:00:00Z",
        components=[
            ComponentHealth(
                name="elasticsearch",
                status=ComponentStatus.UP,
                detail="cluster green; 42 documents",
                latency_ms=3.0,
            ),
            ComponentHealth(
                name="docling",
                status=ComponentStatus.DEGRADED,
                detail="health check failed (http://docling:5001)",
                latency_ms=5.0,
            ),
        ],
        overall=ComponentStatus.DEGRADED,
    )


def test_component_health_reuses_run_doctor(monkeypatch) -> None:
    """The tool calls run_doctor (no reimplemented probes) and returns its board."""
    calls: dict[str, object] = {}
    fake_client = object()

    monkeypatch.setattr(server, "_q", lambda: SimpleNamespace(client=fake_client))

    def fake_run_doctor(**kwargs):
        calls.update(kwargs)
        return _fake_report()

    monkeypatch.setattr(server, "run_doctor", fake_run_doctor)

    result = server.component_health()

    # It delegated to run_doctor, passing the shared ES client (FR-004 / C-003).
    assert calls["es_client"] is fake_client

    # It returned the full board as a plain, JSON-serialisable dict.
    assert isinstance(result, dict)
    assert result["overall"] == "DEGRADED"
    names = [c["name"] for c in result["components"]]
    assert names == ["elasticsearch", "docling"]
    assert result["components"][0]["status"] == "UP"
    assert result["components"][1]["status"] == "DEGRADED"
