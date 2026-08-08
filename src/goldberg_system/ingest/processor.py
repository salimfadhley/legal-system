"""The durable event-driven ingest processor (WP03, FR-005 / FR-006 / FR-009).

Consumes raw-commit triggers from the WP02 messaging boundary and, for each commit,
resolves the changed allowlisted files (:func:`changed_files`) and ingests them via the
**existing** provenance-first :func:`reingest_from_raw` (direct Docling → enrich →
index). It never forks the extract/enrich/index logic and emits the same pipeline
events, so ``status`` / ``dlq`` / ``trace`` keep working.

Delivery guarantees:

* **Ack after terminal.** A commit message is acked only once *every* one of its files
  reaches a terminal state (indexed / skipped). A crash mid-work therefore redelivers
  the whole commit rather than losing it.
* **Retry then dead-letter.** A transient per-file failure (Docling down, sink write
  failed, unexpected error) ``nak``s the message for redelivery; once the broker's
  ``max_deliver`` is reached the message is ``term``-inated and a terminal ``failed``
  (DLQ) pipeline event is emitted (FR-009).
* **Unresolved commit ⇒ nak, never ack.** If the commit's changed files cannot be
  resolved at all (:class:`GitResolutionError` from :func:`changed_files` — unknown
  sha, ``.git`` glitch, or the combined-diff-of-a-merge edge case), ``process_commit``
  raises; that is caught here and treated as a transient failure → nak (then
  term + DLQ at ``max_deliver``). A "resolved, zero allowlisted files" outcome is a
  *successful* empty result and acks. This distinction is the guard against silently
  dropping a legal document (FR-002 / NFR-003).
* **Idempotent.** ``skip_shas = already_indexed()`` plus deterministic ``doc_id`` mean a
  redelivery indexes nothing new (FR-006).
* **Never dies.** A per-document error dead-letters that document; the loop continues.

The processor is broker-agnostic: it drives an injected consumer exposing
``fetch``/``ack``/``nak``/``term`` (the WP02 :class:`PullConsumer` in production, a fake
in tests) and an injected ``process_commit`` callable (built by
:func:`build_commit_processor` in production, a stub in tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.extract.docling_client import DoclingClient
from goldberg_system.ingest.catchup import refresh_provenance
from goldberg_system.ingest.commit_files import changed_files
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import Manifest
from goldberg_system.migrate.reingest import reingest_from_raw
from goldberg_system.observability.events import EventSink, PipelineEvent, safe_emit
from goldberg_system.provenance import now_iso
from goldberg_system.sinks.base import Sink

log = logging.getLogger("goldberg.ingest")

# A per-file status that means "done, do not retry". Everything else (sink-failed,
# extract-failed, error, and the transient "missing" = not-yet-processed/unpulled-LFS)
# is treated as transient → retry then dead-letter.
_TERMINAL_OK = frozenset(
    {"indexed", "skipped-indexed", "skipped-media", "skipped-empty", "skipped-no-index"}
)
# TERMINAL-but-not-success: the raw file is gone from goldberg-raw. This must NOT be
# retried forever (casework saw the same path fail every 20-40 min); reingest has already
# tombstoned the stale index entry. We ack (stop the loop) rather than nak indefinitely.
_TERMINAL_MISSING_FILE = frozenset({"missing-file"})


@dataclass(frozen=True)
class FileResult:
    """The terminal (or transient) outcome of one file within a commit."""

    raw_path: str
    status: str
    sha256: str | None = None

    @property
    def ok(self) -> bool:
        # "ok" here means "terminal, do not retry" — both a clean outcome and a
        # terminal missing-file (already tombstoned) stop redelivery.
        return self.status in _TERMINAL_OK or self.status in _TERMINAL_MISSING_FILE


@dataclass
class CommitResult:
    """The per-file outcomes of processing one commit message."""

    commit_sha: str
    results: list[FileResult] = field(default_factory=list)

    @property
    def failed(self) -> list[FileResult]:
        return [r for r in self.results if not r.ok]

    @property
    def has_failure(self) -> bool:
        return any(not r.ok for r in self.results)


def _stage_for_status(status: str) -> str:
    """Map a reingest failure status to the pipeline stage it failed at."""
    if status.startswith("extract-failed"):
        return "extracted"
    if status.startswith("error"):
        return "enriched"
    if status == "sink-failed":
        return "indexed"
    return "received"  # "missing" and anything else


def _num_delivered(msg: Any) -> int:
    """How many times the broker has delivered this message (>=1)."""
    meta = getattr(msg, "metadata", None)
    val = getattr(meta, "num_delivered", None)
    try:
        return int(val) if val is not None else 1
    except (TypeError, ValueError):
        return 1


class IngestProcessor:
    """Drives an injected pull consumer, applying ack/nak/term + DLQ semantics."""

    def __init__(
        self,
        *,
        consumer: Any,
        process_commit: Callable[[str, str], CommitResult],
        max_deliver: int,
        events: EventSink | None = None,
        run_id_prefix: str = "ingest",
        clock: Callable[[], str] = now_iso,
        batch: int = 50,
        fetch_timeout: float = 5.0,
    ) -> None:
        self.consumer = consumer
        self.process_commit = process_commit
        self.max_deliver = max_deliver
        self.events = events
        self.run_id_prefix = run_id_prefix
        self.clock = clock
        self.batch = batch
        self.fetch_timeout = fetch_timeout
        self.last_activity: str | None = None
        self.last_result: CommitResult | None = None
        self.handled = 0
        self.dead_lettered = 0

    # ── one message ───────────────────────────────────────────────────────────
    def _parse_commit_sha(self, msg: Any) -> str | None:
        try:
            payload = json.loads(msg.data)
            sha = payload["sha"]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return str(sha) if sha else None

    async def _handle(self, msg: Any) -> None:
        run_id = f"{self.run_id_prefix}-{self.clock()}"
        commit_sha = self._parse_commit_sha(msg)
        if commit_sha is None:  # unparseable poison — never redeliver
            await self.consumer.term(msg)
            self._emit_poison(run_id)
            self.dead_lettered += 1
            return

        result: CommitResult | None = None
        exc: Exception | None = None
        try:
            # process_commit does blocking I/O (Docling/ES); run off the event loop so
            # the NATS connection keeps servicing its heartbeat during long extracts.
            result = await asyncio.to_thread(self.process_commit, commit_sha, run_id)
        except Exception as e:  # noqa: BLE001 - one commit must never kill the loop
            exc = e
            log.exception("process_commit failed for %s", commit_sha)

        self.last_activity = self.clock()
        self.last_result = result
        self.handled += 1

        # success: every file terminal-ok (or nothing to do) → ack
        if exc is None and result is not None and not result.has_failure:
            await self.consumer.ack(msg)
            return

        # failure: retry until max_deliver, then terminate + dead-letter
        delivered = _num_delivered(msg)
        if delivered >= self.max_deliver:
            await self.consumer.term(msg)
            self._emit_dlq(run_id, commit_sha, result, exc, delivered)
            self.dead_lettered += 1
        else:
            await self.consumer.nak(msg)

    # ── DLQ / poison events ─────────────────────────────────────────────────────
    def _emit_dlq(
        self,
        run_id: str,
        commit_sha: str,
        result: CommitResult | None,
        exc: Exception | None,
        delivered: int,
    ) -> None:
        reason = "dead-lettered: max_deliver reached"
        if result is None or not result.failed:
            safe_emit(
                self.events,
                PipelineEvent.make(
                    "ingest",
                    "received",
                    "failed",
                    run_id=run_id,
                    reason=reason,
                    error=str(exc) if exc else f"commit {commit_sha}",
                    attempt=delivered,
                ),
            )
            return
        for fr in result.failed:
            safe_emit(
                self.events,
                PipelineEvent.make(
                    "ingest",
                    _stage_for_status(fr.status),
                    "failed",
                    run_id=run_id,
                    raw_path=fr.raw_path,
                    sha256=fr.sha256,
                    reason=reason,
                    error=fr.status,
                    attempt=delivered,
                ),
            )

    def _emit_poison(self, run_id: str) -> None:
        safe_emit(
            self.events,
            PipelineEvent.make(
                "ingest",
                "received",
                "failed",
                run_id=run_id,
                reason="poison: unparseable commit event",
            ),
        )

    # ── the loop ────────────────────────────────────────────────────────────────
    async def process_batch(self) -> int:
        """Fetch one batch and handle every message; return how many were handled."""
        try:
            msgs = await self.consumer.fetch(self.batch, self.fetch_timeout)
        except Exception:  # noqa: BLE001 - fetch timeout / transient broker hiccup
            return 0
        for msg in msgs:
            await self._handle(msg)
        return len(msgs)

    async def run_forever(
        self,
        *,
        should_stop: Callable[[], bool] | None = None,
        idle_sleep: float = 1.0,
    ) -> None:
        """Consume batches until ``should_stop`` returns True; never dies on error."""
        stop = should_stop or (lambda: False)
        while not stop():
            try:
                handled = await self.process_batch()
            except Exception:  # noqa: BLE001 - the loop must never die (FR-008)
                log.exception("processor iteration failed; continuing")
                handled = 0
            if handled == 0 and idle_sleep > 0 and not stop():
                await asyncio.sleep(idle_sleep)


def build_commit_processor(
    *,
    raw_root: Path | str,
    manifest_path: Path | str,
    allowlist: Allowlist,
    docling: DoclingClient,
    enricher: EnrichmentAdapter,
    sinks: list[Sink],
    already_indexed: Callable[[], set[str]],
    events: EventSink | None = None,
    workers: int = 2,
    with_commit: bool = True,
) -> Callable[[str, str], CommitResult]:
    """Build the production ``process_commit`` that wires commit → files → reingest.

    Returned callable: given a commit sha it refreshes provenance (so brand-new files
    are manifested BEFORE indexing, C-002), resolves the commit's changed allowlisted
    files, and ingests exactly those via :func:`reingest_from_raw` (reusing the proven
    ``process_one``), returning per-file :class:`FileResult`s for ack/nak/DLQ.
    """
    root = Path(raw_root)
    mpath = Path(manifest_path)

    def _load_manifest() -> Manifest:
        if mpath.is_file():
            try:
                return Manifest(json.loads(mpath.read_text()))
            except (json.JSONDecodeError, OSError):
                return Manifest({})
        return Manifest({})

    def process_commit(commit_sha: str, run_id: str) -> CommitResult:
        # provenance-first: register any files this commit introduced.
        refresh_provenance(root, allowlist, mpath, with_commit=with_commit)
        # changed_files raises GitResolutionError if the commit cannot be resolved;
        # we deliberately let it propagate so _handle naks (retry) instead of acking
        # an empty result — an empty list here is only a *genuine* zero-file commit.
        files = changed_files(root, commit_sha, allowlist)
        if not files:
            return CommitResult(commit_sha=commit_sha, results=[])

        manifest = _load_manifest()
        path_to_sha = {e.get("raw_path", ""): sha for sha, e in manifest.items()}
        skip = set(already_indexed())
        statuses: dict[str, str] = {}

        reingest_from_raw(
            root,
            manifest,
            docling,
            enricher,
            sinks,
            events=events,
            run_id=run_id,
            only=set(files),
            skip_shas=skip or None,
            workers=workers,
            on_doc=lambda rp, st: statuses.__setitem__(rp, st),
        )

        results = [
            FileResult(raw_path=rp, status=st, sha256=path_to_sha.get(rp))
            for rp, st in statuses.items()
        ]
        # A changed file with no recorded status was not manifested/processed — treat it
        # as a transient "missing" so it is retried (it may be an unpulled LFS object).
        for f in files:
            if f not in statuses:
                results.append(
                    FileResult(raw_path=f, status="missing", sha256=path_to_sha.get(f))
                )
        return CommitResult(commit_sha=commit_sha, results=results)

    return process_commit


# --------------------------------------------------------------------------- #
# Health endpoint (stdlib, no web framework) — FR-010 / NFR-004
# --------------------------------------------------------------------------- #
def make_health_server(
    payload_fn: Callable[[], dict[str, Any]],
    host: str = "0.0.0.0",
    port: int = 8098,
) -> ThreadingHTTPServer:
    """Build (but do not start) a ``GET /health`` server serving ``payload_fn()``.

    Run it with ``server.serve_forever()`` (typically on a daemon thread).
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet default stderr logging
            pass

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                body = json.dumps(payload_fn()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    return ThreadingHTTPServer((host, port), _Handler)
