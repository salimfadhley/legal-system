"""Provenance helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string (ingestion stamp)."""
    return datetime.now(timezone.utc).isoformat()
