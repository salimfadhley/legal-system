"""The event-driven ingest service (WP03).

Consumes raw-commit triggers from the NATS JetStream boundary (WP02) and ingests the
files each commit changed via the existing provenance-first pipeline
(:func:`reingest_from_raw` / ``process_one``) — with ack/nak/term + DLQ delivery
semantics and a bounded one-shot startup catch-up. Nothing here forks the
extract/enrich/index logic; it orchestrates the pieces the rest of the system already
provides (C-004).

Public surface:
    * :func:`changed_files` — resolve a commit's allowlisted, ingestable files.
    * :func:`run_catchup` / :class:`CatchupReport` — the one-shot startup catch-up.
    * :class:`IngestProcessor` / :class:`CommitResult` / :class:`FileResult` — the
      durable-consumer loop and its per-commit outcome types.
    * :func:`build_commit_processor` — wire commit → files → reingest for production.
    * :func:`make_health_server` — a stdlib ``GET /health`` endpoint.
"""

from goldberg_system.ingest.catchup import (
    CatchupReport,
    ProvenanceRefresh,
    refresh_provenance,
    run_catchup,
    select_pending,
)
from goldberg_system.ingest.commit_files import changed_files
from goldberg_system.ingest.processor import (
    CommitResult,
    FileResult,
    IngestProcessor,
    build_commit_processor,
    make_health_server,
)

__all__ = [
    "CatchupReport",
    "CommitResult",
    "FileResult",
    "IngestProcessor",
    "ProvenanceRefresh",
    "build_commit_processor",
    "changed_files",
    "make_health_server",
    "refresh_provenance",
    "run_catchup",
    "select_pending",
]
