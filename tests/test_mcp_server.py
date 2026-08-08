"""Unit tests for the hosted goldberg MCP server (ADR 0010).

The MCP tools are thin wrappers over the same core functions behind the CLI, so
these tests fake the core (no network, no Elasticsearch) and assert only that the
tool returns the expected structured shape. The ``component_health`` tool is the
doctor board exposed for MCP-capable agents (FR-003 / C-003): it must reuse
``observability.health.run_doctor`` and return the ``DoctorReport`` as a dict.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from goldberg_system.mcp import server
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree
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


# --------------------------------------------------------------------------- #
# raw_index_gap — the visibility tool (task #3): committed-to-raw but not indexed.
# --------------------------------------------------------------------------- #
def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _seed_raw(tmp_path: Path, bodies: dict[str, str]) -> Path:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    for name, body in bodies.items():
        (raw / "evidence" / name).write_text(body)
    return raw


def _fake_es(shas: list[str]):
    class _Client:
        def search(self, **kwargs):  # noqa: ANN003
            return {
                "hits": {
                    "total": {"value": len(shas)},
                    "hits": [{"_source": {"raw_sha256": s}} for s in shas],
                }
            }

    return _Client()


def _patch_gap_deps(monkeypatch, tmp_path: Path, raw: Path, indexed: list[str]) -> None:
    from goldberg_system import config

    monkeypatch.setattr(
        server, "_q", lambda: SimpleNamespace(client=_fake_es(indexed), index="idx")
    )
    monkeypatch.setattr(config, "project_path", lambda which: raw)
    monkeypatch.setattr(
        Allowlist,
        "load",
        classmethod(
            lambda cls, *a, **k: Allowlist(
                included={"evidence": IncludedTree("evidence", "received")},
                excluded={},
                exclude_globs=("**/*.mp4",),
            )
        ),
    )


def test_raw_index_gap_whole_corpus(monkeypatch, tmp_path: Path) -> None:
    raw = _seed_raw(tmp_path, {"present.txt": "indexed", "gap.txt": "missing"})
    _patch_gap_deps(monkeypatch, tmp_path, raw, [_hash("indexed")])

    out = server.raw_index_gap()

    assert out["complete"] is False
    assert out["gap_count"] == 1
    assert out["gap"][0]["raw_path"] == "evidence/gap.txt"
    assert out["by_tree"] == {"evidence": 1}


def test_raw_index_gap_single_path(monkeypatch, tmp_path: Path) -> None:
    raw = _seed_raw(tmp_path, {"gap.txt": "missing"})
    _patch_gap_deps(monkeypatch, tmp_path, raw, [])

    out = server.raw_index_gap(raw_path="evidence/gap.txt")

    assert out["in_raw"] is True
    assert out["in_index"] is False
    assert out["invisible_gap"] is True
    assert out["raw_sha256"] == _hash("missing")


def test_raw_index_gap_limit_truncates_inline_list(monkeypatch, tmp_path: Path) -> None:
    raw = _seed_raw(tmp_path, {f"d{i}.txt": f"body {i}" for i in range(5)})
    _patch_gap_deps(monkeypatch, tmp_path, raw, [])

    out = server.raw_index_gap(limit=2)

    assert out["gap_count"] == 5  # exact count preserved
    assert len(out["gap"]) == 2  # inline list bounded
    assert out["gap_truncated"] is True
