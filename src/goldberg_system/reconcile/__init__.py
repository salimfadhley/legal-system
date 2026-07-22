"""Auto-ingestion reconciler — the canonical automatic ingest path (goldberg-autoingest).

See :mod:`goldberg_system.reconcile.reconciler` and ADR 0011. Supersedes the
Papra-webhook live-index service (``goldberg_system.service``, ADR 0005).
"""

from __future__ import annotations

from goldberg_system.reconcile.health import health_payload, make_health_server
from goldberg_system.reconcile.reconciler import (
    ProvenanceRefresh,
    ReconcileCycle,
    Reconciler,
    refresh_provenance,
)

__all__ = [
    "ProvenanceRefresh",
    "ReconcileCycle",
    "Reconciler",
    "refresh_provenance",
    "health_payload",
    "make_health_server",
]
