"""Tests for alerting — the reduced M12 phase 4 (ADR 0008)."""

from __future__ import annotations

from goldberg_system.observability.alerts import evaluate_alerts, exit_code
from goldberg_system.observability.reconcile import ReconciliationReport
from goldberg_system.observability.state import SystemState


def _state(*, status: str = "ok", failed: int = 0, skipped: int = 0) -> SystemState:
    checks = [{"name": "no_recent_failures", "ok": failed == 0, "detail": ""}]
    return SystemState(
        generated_at="t",
        health={"status": status, "checks": checks},
        corpus={"documents": 10, "by_matter": {}, "by_type": {}},
        pipeline={
            "by_stage_status": {},
            "last_indexed_at": None,
            "recent_failures": [],
        },
        dlq={"failed": failed, "skipped": skipped, "recent": []},
    )


def _recon(missing: list[str]) -> ReconciliationReport:
    return ReconciliationReport(
        expected_count=len(missing) + 5,
        actual_count=5,
        missing=missing,
        missing_by_matter={"422500059892": len(missing)} if missing else {},
    )


def test_clear_when_healthy_and_complete() -> None:
    alerts = evaluate_alerts(_state(), _recon([]))
    assert alerts == []
    assert exit_code(alerts) == 0


def test_missing_documents_is_critical() -> None:
    alerts = evaluate_alerts(_state(), _recon(["evidence/a.pdf", "evidence/b.pdf"]))
    codes = {a.code: a.level for a in alerts}
    assert codes["missing_documents"] == "critical"
    assert "2 expected" in next(
        a.message for a in alerts if a.code == "missing_documents"
    )
    assert exit_code(alerts) == 2


def test_pipeline_failures_critical_respecting_threshold() -> None:
    assert evaluate_alerts(_state(failed=1)) != []
    assert exit_code(evaluate_alerts(_state(failed=1))) == 2
    # tolerate up to max_failures
    assert evaluate_alerts(_state(failed=1), max_failures=1) == []


def test_health_degraded_is_warning() -> None:
    alerts = evaluate_alerts(_state(status="degraded"))
    assert any(a.code == "health_degraded" and a.level == "warning" for a in alerts)
    # warning-only → exit 1
    assert exit_code(alerts) == 1


def test_skipped_only_alerts_when_opted_in() -> None:
    assert evaluate_alerts(_state(skipped=3)) == []
    alerts = evaluate_alerts(_state(skipped=3), alert_on_skipped=True)
    assert any(a.code == "skipped_documents" for a in alerts)


def test_no_reconciliation_means_no_completeness_alert() -> None:
    # without a manifest we can't judge completeness → only health/failure alerts
    alerts = evaluate_alerts(_state(), None)
    assert all(a.code != "missing_documents" for a in alerts)
