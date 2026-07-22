"""Tests for component-health probes and the doctor report (goldberg-doctor).

Every probe is exercised with FAKES (monkeypatched ``requests`` / injected fake ES
and Docling clients) so the unit run never touches the network. We cover the UP /
DEGRADED / DOWN / timeout paths per component, the concurrency time-bound, the
``DoctorReport.overall`` worst-status logic, and the CLI exit-code mapping.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from goldberg_system.observability import health
from goldberg_system.observability.health import (
    ComponentHealth,
    ComponentStatus,
    DoctorReport,
    probe_docling,
    probe_elasticsearch,
    probe_enricher,
    probe_live_index_watcher,
    probe_mcp_server,
    probe_wiki_synthesis,
    run_doctor,
    worst_status,
)

# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeCluster:
    def __init__(self, health_resp: Any) -> None:
        self._resp = health_resp

    def health(self) -> Any:
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


class _FakeES:
    """A fake elasticsearch client covering cluster health, count and search."""

    def __init__(
        self,
        *,
        cluster: Any = None,
        counts: dict[str, int] | None = None,
        missing: set[str] | None = None,
        newest_event_ts: str | None = None,
        search_error: Exception | None = None,
    ) -> None:
        self.cluster = _FakeCluster(
            cluster if cluster is not None else {"status": "green"}
        )
        self._counts = counts or {}
        self._missing = missing or set()
        self._newest_event_ts = newest_event_ts
        self._search_error = search_error

    def count(self, index: str) -> dict[str, int]:
        if index in self._missing:
            raise RuntimeError(f"index_not_found_exception: {index}")
        return {"count": self._counts.get(index, 0)}

    def search(self, **kw: Any) -> dict[str, Any]:
        if self._search_error is not None:
            raise self._search_error
        # live-index-watcher freshness query (sorted, size 1)
        if self._newest_event_ts is None:
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [{"_source": {"ts": self._newest_event_ts}}]}}


class _FakeResp:
    def __init__(
        self, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _FakeDocling:
    def __init__(self, ok: bool = True, base_url: str = "http://docling") -> None:
        self._ok = ok
        self.base_url = base_url

    def health(self) -> bool:
        return self._ok


# ── elasticsearch probe ─────────────────────────────────────────────────────


def test_es_up_when_all_indices_present() -> None:
    es = _FakeES(
        cluster={"status": "green"},
        counts={
            "goldberg_documents": 5000,
            "silverbullet-goldberg": 120,
            "goldberg_pipeline_events": 9000,
        },
    )
    h = probe_elasticsearch(es)
    assert h.status is ComponentStatus.UP
    assert "5000" in h.detail and "green" in h.detail
    assert h.latency_ms is not None


def test_es_degraded_when_index_missing() -> None:
    es = _FakeES(
        cluster={"status": "yellow"},
        counts={"goldberg_documents": 5000, "goldberg_pipeline_events": 9000},
        missing={"silverbullet-goldberg"},
    )
    h = probe_elasticsearch(es)
    assert h.status is ComponentStatus.DEGRADED
    assert "silverbullet-goldberg" in h.detail


def test_es_down_when_cluster_unreachable() -> None:
    es = _FakeES(cluster=ConnectionError("no route to host"))
    h = probe_elasticsearch(es)
    assert h.status is ComponentStatus.DOWN
    assert "no route to host" in h.detail


# ── docling probe ───────────────────────────────────────────────────────────


def test_docling_up() -> None:
    h = probe_docling(_FakeDocling(ok=True))
    assert h.status is ComponentStatus.UP


def test_docling_down_when_health_false() -> None:
    h = probe_docling(_FakeDocling(ok=False))
    assert h.status is ComponentStatus.DOWN


def test_docling_down_when_client_raises() -> None:
    class _Boom:
        base_url = "http://docling"

        def health(self) -> bool:
            raise RuntimeError("kaboom")

    h = probe_docling(_Boom())
    assert h.status is ComponentStatus.DOWN
    assert "kaboom" in h.detail


# ── enricher (OpenAI) probe ─────────────────────────────────────────────────


def test_enricher_degraded_without_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    h = probe_enricher()
    assert h.status is ComponentStatus.DEGRADED
    assert "OPENAI_API_KEY" in h.detail


def test_enricher_up_on_200(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kw: Any) -> _FakeResp:
        captured["url"] = url
        captured["headers"] = kw.get("headers", {})
        return _FakeResp(200)

    monkeypatch.setattr(health.requests, "get", fake_get)
    h = probe_enricher(api_key="sk-test")
    assert h.status is ComponentStatus.UP
    # NFR-003: must be the metadata/list endpoint, never a completion
    assert captured["url"].endswith("/v1/models")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_enricher_down_on_401(monkeypatch) -> None:
    monkeypatch.setattr(health.requests, "get", lambda *a, **k: _FakeResp(401))
    h = probe_enricher(api_key="sk-bad")
    assert h.status is ComponentStatus.DOWN
    assert "401" in h.detail


def test_enricher_down_on_timeout(monkeypatch) -> None:
    def boom(*a: Any, **k: Any) -> _FakeResp:
        raise health.requests.Timeout("timed out")

    monkeypatch.setattr(health.requests, "get", boom)
    h = probe_enricher(api_key="sk-test")
    assert h.status is ComponentStatus.DOWN


# ── mcp server probe ────────────────────────────────────────────────────────


def test_mcp_up_on_200_with_session(monkeypatch) -> None:
    def fake_post(url: str, **kw: Any) -> _FakeResp:
        assert kw["headers"]["Accept"].startswith("application/json")
        return _FakeResp(200, headers={"Mcp-Session-Id": "abc123"})

    monkeypatch.setattr(health.requests, "post", fake_post)
    h = probe_mcp_server(host="192.168.86.31", port=8765)
    assert h.status is ComponentStatus.UP


def test_mcp_down_on_connection_refused(monkeypatch) -> None:
    def boom(*a: Any, **k: Any) -> _FakeResp:
        raise health.requests.ConnectionError("connection refused")

    monkeypatch.setattr(health.requests, "post", boom)
    h = probe_mcp_server(host="192.168.86.31", port=8765)
    assert h.status is ComponentStatus.DOWN
    assert "refused" in h.detail


def test_mcp_falls_back_from_wildcard_host(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResp:
        seen["url"] = url
        return _FakeResp(200, headers={"Mcp-Session-Id": "x"})

    monkeypatch.setattr(health.requests, "post", fake_post)
    probe_mcp_server(host="0.0.0.0", port=8765)
    assert "192.168.86.31" in seen["url"]


def test_mcp_down_on_non_200(monkeypatch) -> None:
    monkeypatch.setattr(health.requests, "post", lambda *a, **k: _FakeResp(500))
    h = probe_mcp_server(host="192.168.86.31", port=8765)
    assert h.status is ComponentStatus.DOWN


# ── live-index watcher probe (inferred) ─────────────────────────────────────


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).isoformat()


def test_watcher_up_when_events_fresh() -> None:
    es = _FakeES(newest_event_ts=_iso(60))  # 1 min ago
    h = probe_live_index_watcher(es, freshness_seconds=15 * 60)
    assert h.status is ComponentStatus.UP
    assert h.inferred is True


def test_watcher_down_when_events_stale() -> None:
    es = _FakeES(newest_event_ts=_iso(3600))  # 1 hour ago
    h = probe_live_index_watcher(es, freshness_seconds=15 * 60)
    assert h.status is ComponentStatus.DOWN
    assert h.inferred is True
    assert "watcher likely paused" in h.detail


def test_watcher_down_when_no_events() -> None:
    es = _FakeES(newest_event_ts=None)
    h = probe_live_index_watcher(es, freshness_seconds=15 * 60)
    assert h.status is ComponentStatus.DOWN
    assert h.inferred is True


def test_watcher_down_and_inferred_on_error() -> None:
    es = _FakeES(search_error=RuntimeError("es down"))
    h = probe_live_index_watcher(es)
    assert h.status is ComponentStatus.DOWN
    assert h.inferred is True


# ── wiki synthesis probe ────────────────────────────────────────────────────


def test_wiki_down_when_index_missing() -> None:
    es = _FakeES(missing={"silverbullet-goldberg"})
    h = probe_wiki_synthesis(es)
    assert h.status is ComponentStatus.DOWN


def test_wiki_degraded_when_present_but_not_wired() -> None:
    es = _FakeES(counts={"silverbullet-goldberg": 42, "goldberg_documents": 5000})
    h = probe_wiki_synthesis(es, synthesis_wired=False)
    assert h.status is ComponentStatus.DEGRADED
    assert "42" in h.detail and "not refreshed" in h.detail


def test_wiki_up_when_present_and_wired() -> None:
    es = _FakeES(counts={"silverbullet-goldberg": 42, "goldberg_documents": 5000})
    h = probe_wiki_synthesis(es, synthesis_wired=True)
    assert h.status is ComponentStatus.UP


# ── report / worst-status ────────────────────────────────────────────────────


def _c(status: ComponentStatus) -> ComponentHealth:
    return ComponentHealth(name="x", status=status, detail="")


def test_worst_status_ordering() -> None:
    assert worst_status([ComponentStatus.UP, ComponentStatus.UP]) is ComponentStatus.UP
    assert (
        worst_status([ComponentStatus.UP, ComponentStatus.DEGRADED])
        is ComponentStatus.DEGRADED
    )
    assert (
        worst_status([ComponentStatus.DEGRADED, ComponentStatus.DOWN])
        is ComponentStatus.DOWN
    )
    assert worst_status([]) is ComponentStatus.UP


def test_doctor_report_yaml_roundtrips() -> None:
    report = DoctorReport(
        generated_at="2026-07-22T00:00:00Z",
        components=[_c(ComponentStatus.UP), _c(ComponentStatus.DOWN)],
        overall=ComponentStatus.DOWN,
    )
    import yaml

    loaded = yaml.safe_load(report.to_yaml())
    assert loaded["overall"] == "DOWN"
    assert loaded["components"][0]["status"] == "UP"


# ── run_doctor concurrency + timeout ─────────────────────────────────────────


def _patch_all_probes(monkeypatch, delay: float = 0.0) -> None:
    """Replace every probe with a fast fake that returns UP (optionally sleeping)."""
    for pname in (
        "probe_elasticsearch",
        "probe_docling",
        "probe_enricher",
        "probe_mcp_server",
        "probe_live_index_watcher",
        "probe_wiki_synthesis",
    ):
        name = pname.removeprefix("probe_")

        def make(cname: str):  # noqa: ANN202
            def fn(*a: Any, **k: Any) -> ComponentHealth:
                if delay:
                    time.sleep(delay)
                return ComponentHealth(
                    name=cname, status=ComponentStatus.UP, detail="ok", latency_ms=1.0
                )

            return fn

        monkeypatch.setattr(health, pname, make(name))


def test_run_doctor_runs_probes_concurrently(monkeypatch) -> None:
    # 6 probes each sleeping 0.2s: sequential would be ~1.2s, concurrent ~0.2s.
    _patch_all_probes(monkeypatch, delay=0.2)
    start = time.perf_counter()
    report = run_doctor(es_client=object())
    elapsed = time.perf_counter() - start
    assert len(report.components) == 6
    assert report.overall is ComponentStatus.UP
    assert elapsed < 0.8, f"probes not concurrent (took {elapsed:.2f}s)"


def test_run_doctor_marks_hung_probe_down(monkeypatch) -> None:
    _patch_all_probes(monkeypatch, delay=0.0)

    def hang(*a: Any, **k: Any) -> ComponentHealth:
        time.sleep(5.0)
        return ComponentHealth(name="docling", status=ComponentStatus.UP, detail="ok")

    monkeypatch.setattr(health, "probe_docling", hang)
    start = time.perf_counter()
    report = run_doctor(es_client=object(), board_timeout=0.4)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"board did not honour its budget (took {elapsed:.2f}s)"
    by_name = {c.name: c for c in report.components}
    assert by_name["docling"].status is ComponentStatus.DOWN
    assert "timed out" in by_name["docling"].detail
    # the other components still reported correctly
    assert by_name["elasticsearch"].status is ComponentStatus.UP
    assert report.overall is ComponentStatus.DOWN


def test_run_doctor_overall_degraded(monkeypatch) -> None:
    _patch_all_probes(monkeypatch, delay=0.0)

    def degraded(*a: Any, **k: Any) -> ComponentHealth:
        return ComponentHealth(
            name="wiki_synthesis", status=ComponentStatus.DEGRADED, detail="stale"
        )

    monkeypatch.setattr(health, "probe_wiki_synthesis", degraded)
    report = run_doctor(es_client=object())
    assert report.overall is ComponentStatus.DEGRADED


# ── CLI exit-code mapping ────────────────────────────────────────────────────


def _report(overall: ComponentStatus) -> DoctorReport:
    return DoctorReport(
        generated_at="2026-07-22T00:00:00Z",
        components=[ComponentHealth(name="elasticsearch", status=overall, detail="x")],
        overall=overall,
    )


def test_doctor_cli_exit_codes(monkeypatch) -> None:
    from click.testing import CliRunner

    from goldberg_system import cli

    runner = CliRunner()

    for overall, expected in (
        (ComponentStatus.UP, 0),
        (ComponentStatus.DEGRADED, 1),
        (ComponentStatus.DOWN, 1),
    ):
        monkeypatch.setattr(health, "run_doctor", lambda **k: _report(overall))
        result = runner.invoke(cli.main, ["doctor"])
        assert result.exit_code == expected, (overall, result.output)


def test_doctor_cli_yaml_output(monkeypatch) -> None:
    from click.testing import CliRunner

    from goldberg_system import cli

    monkeypatch.setattr(health, "run_doctor", lambda **k: _report(ComponentStatus.UP))
    result = CliRunner().invoke(cli.main, ["doctor", "--yaml"])
    assert result.exit_code == 0
    assert "overall: UP" in result.output
    assert "components:" in result.output
