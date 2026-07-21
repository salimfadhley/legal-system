"""Alerting — the reduced M12 phase 4 (ADR 0008 §7, scoped down).

Evaluates the system state (and, if a manifest is given, reconciliation) against a
few conditions and returns the alerts that fire. This closes the "silent drop" loop:
instead of remembering to run ``goldberg audit``, a scheduler (cron on Halob, or
Jenkins) runs ``goldberg alert`` and notifies on a non-zero exit.

Deliberately NOT built: OpenTelemetry/Grafana (overkill for a single-user LAN tool)
and the durable NATS DLQ (the ES projection + idempotent re-run cover reprocessing).
"""

from __future__ import annotations

from pydantic import BaseModel

from goldberg_system.observability.reconcile import ReconciliationReport
from goldberg_system.observability.state import SystemState

CRITICAL = "critical"
WARNING = "warning"


class Alert(BaseModel):
    level: str  # critical | warning
    code: str
    message: str


def evaluate_alerts(
    state: SystemState,
    reconciliation: ReconciliationReport | None = None,
    *,
    max_failures: int = 0,
    alert_on_skipped: bool = False,
) -> list[Alert]:
    """Return the alerts that fire for ``state`` (+ optional ``reconciliation``)."""
    alerts: list[Alert] = []

    # The core concern: documents that should be in the corpus but never ingested.
    if reconciliation is not None and reconciliation.missing:
        by_matter = ", ".join(
            f"{m}:{n}" for m, n in reconciliation.missing_by_matter.items()
        )
        alerts.append(
            Alert(
                level=CRITICAL,
                code="missing_documents",
                message=(
                    f"{len(reconciliation.missing)} expected document(s) did not "
                    f"ingest — corpus is incomplete ({by_matter})"
                ),
            )
        )

    failed = int(state.dlq.get("failed", 0))
    if failed > max_failures:
        alerts.append(
            Alert(
                level=CRITICAL,
                code="pipeline_failures",
                message=f"{failed} pipeline stage failure(s) — see `goldberg dlq`",
            )
        )

    if state.health.get("status") != "ok":
        failing = [c["name"] for c in state.health.get("checks", []) if not c["ok"]]
        alerts.append(
            Alert(
                level=WARNING,
                code="health_degraded",
                message=f"health degraded: {', '.join(failing) or 'unknown'}",
            )
        )

    skipped = int(state.dlq.get("skipped", 0))
    if alert_on_skipped and skipped:
        alerts.append(
            Alert(
                level=WARNING,
                code="skipped_documents",
                message=f"{skipped} document(s) skipped (e.g. empty extraction / .eml)",
            )
        )

    return alerts


def exit_code(alerts: list[Alert]) -> int:
    """2 if any critical, 1 if only warnings, 0 if clear — for cron/Jenkins."""
    if any(a.level == CRITICAL for a in alerts):
        return 2
    if alerts:
        return 1
    return 0
