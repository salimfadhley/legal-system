"""The restricted-reingest ALARM — "nothing alarmed" was itself the fault.

When the healer or the indexer encounters a document that is registered as a
deliberate exclusion (or carries a ``no_index`` sidecar) and that WOULD otherwise have
been (re-)added to the index, that event must be **impossible to miss**. Casework's
words: *"a deliberately excluded document CANNOT be re-added by an automated process
without something loudly saying so … the absence of an alert is itself the fault."*

Two surfaces, one record:

* an immediate **CRITICAL** log line (operators / journald), and
* a durable, append-only JSONL alert log that :func:`goldberg_system.observability.state.aggregate`
  reads to raise a first-class, NAMED health check — :data:`RESTRICTED_REINGEST_CHECK`
  (``restricted_reingest_blocked``) — that turns ``legal_system status`` degraded. Not
  a buried DLQ line: a named failing check that names WHAT was blocked and WHY.

The alert carries the ``raw_path`` + ``reason`` so a human immediately sees which
restricted document an automated process tried to re-add, and under what authority it
was excluded.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from goldberg_system.config import config_dir
from goldberg_system.metadata.sidecar import (
    CATEGORY_HOUSEKEEPING,
    CATEGORY_LEGALLY_OBLIGATORY,
    RECOGNISED_NO_INDEX_CATEGORIES,
)
from goldberg_system.provenance import now_iso

log = logging.getLogger("goldberg.restricted")

# The NAMED health check surfaced by ``legal_system status`` (state.aggregate).
RESTRICTED_REINGEST_CHECK = "restricted_reingest_blocked"

ALERT_LOG_FILENAME = "restricted-reingest-alerts.jsonl"

# Severity per exclusion class. A ``legally_obligatory`` accidental (re-)index is an
# INCIDENT (court undertaking / privilege / statutory restriction breached); a
# ``housekeeping`` one is NOISE (a build artefact / duplicate almost re-added).
SEVERITY_INCIDENT = "incident"
SEVERITY_NOISE = "noise"


def normalise_category(category: str | None) -> str:
    """Coerce a stored/None category to a recognised class — safer reading on doubt.

    A missing or UNKNOWN category resolves to ``legally_obligatory`` so an unclassified
    exclusion alarms at the higher severity rather than being quietly treated as noise.
    """
    value = (category or "").strip()
    return value if value in RECOGNISED_NO_INDEX_CATEGORIES else CATEGORY_LEGALLY_OBLIGATORY


def severity_for_category(category: str | None) -> str:
    """``incident`` for ``legally_obligatory`` (the safe default), else ``noise``."""
    return (
        SEVERITY_NOISE
        if normalise_category(category) == CATEGORY_HOUSEKEEPING
        else SEVERITY_INCIDENT
    )


@dataclass(frozen=True)
class BlockedReingest:
    """One blocked attempt to (re-)add a restricted/registered-excluded document."""

    raw_path: str
    reason: str
    source: str  # e.g. "catchup", "elasticsearch_indexer"
    timestamp: str
    category: str = CATEGORY_LEGALLY_OBLIGATORY  # safer default when unrecorded

    @property
    def severity(self) -> str:
        """``incident`` (legally_obligatory) vs ``noise`` (housekeeping)."""
        return severity_for_category(self.category)

    @property
    def is_incident(self) -> bool:
        return self.severity == SEVERITY_INCIDENT

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "BlockedReingest":
        return cls(
            raw_path=str(data.get("raw_path") or ""),
            reason=str(data.get("reason") or ""),
            source=str(data.get("source") or ""),
            timestamp=str(data.get("timestamp") or ""),
            category=normalise_category(data.get("category")),
        )


def default_alert_log_path() -> Path:
    """The durable alert log the status check reads (``<config_dir>/...``)."""
    return config_dir() / ALERT_LOG_FILENAME


def record_blocked_reingest(
    raw_path: str,
    reason: str,
    *,
    source: str,
    category: str | None = None,
    log_path: Path | str | None = None,
    logger: logging.Logger | None = None,
) -> BlockedReingest:
    """LOUDLY record that a restricted document was blocked from (re-)ingestion.

    The log SEVERITY is category-aware: a ``legally_obligatory`` block (the safe default
    for a missing/unknown category) is an INCIDENT logged at CRITICAL; a ``housekeeping``
    block is NOISE logged at WARNING. Appends a durable JSONL alert **only** when
    ``log_path`` is given (production wiring passes :func:`default_alert_log_path`), so
    unit-level callers can assert the logging behaviour without writing a file.
    """
    alert = BlockedReingest(
        raw_path=raw_path,
        reason=reason or "(no reason recorded)",
        source=source,
        timestamp=now_iso(),
        category=normalise_category(category),
    )
    emit = (logger or log).critical if alert.is_incident else (logger or log).warning
    emit(
        "BLOCKED re-ingestion of restricted document %s (%s) — source=%s severity=%s "
        "category=%s. A deliberately-excluded document was almost re-added by an "
        "automated process.",
        alert.raw_path,
        alert.reason,
        alert.source,
        alert.severity,
        alert.category,
    )
    if log_path is not None:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(alert.to_json() + "\n")
    return alert


def load_blocked_reingests(
    log_path: Path | str | None = None,
) -> list[BlockedReingest]:
    """Load recorded alerts (default: :func:`default_alert_log_path`). Missing → []."""
    p = Path(log_path) if log_path is not None else default_alert_log_path()
    if not p.is_file():
        return []
    alerts: list[BlockedReingest] = []
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            alerts.append(BlockedReingest.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("restricted-alert log: skipping malformed line: %s", line[:120])
    return alerts


def partition_by_severity(
    alerts: Iterable[BlockedReingest],
) -> tuple[list[BlockedReingest], list[BlockedReingest]]:
    """Split alerts into ``(incidents, noise)`` — legally_obligatory vs housekeeping."""
    items = list(alerts)
    incidents = [a for a in items if a.is_incident]
    noise = [a for a in items if not a.is_incident]
    return incidents, noise


def summarize_blocked(alerts: Iterable[BlockedReingest]) -> str:
    """A one-line detail string for the health check, naming path + reason + severity.

    Leads with the INCIDENT (``legally_obligatory``) count so the distinct, higher-severity
    signal is unmissable, then notes any ``housekeeping`` noise separately.
    """
    incidents, noise = partition_by_severity(alerts)
    items = incidents + noise
    if not items:
        return "none"
    shown = "; ".join(f"{a.raw_path} ({a.reason}) [{a.severity}]" for a in items[:5])
    if len(items) > 5:
        shown += f"; +{len(items) - 5} more"
    return (
        f"{len(incidents)} INCIDENT (legally_obligatory) + {len(noise)} noise "
        f"(housekeeping) blocked restricted re-ingestion(s): {shown}"
    )
