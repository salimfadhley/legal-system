"""The auto-ingestion reconciler — the canonical automatic ingest path (M15).

A long-running daemon that periodically compares ``goldberg-raw`` against what is
already indexed and ingests the difference, **provenance-first**. Each reconcile
cycle:

1. **Refreshes provenance** — walks the allowlisted trees and, for every file whose
   content SHA-256 is not yet in the manifest, registers a provenance entry
   (``sha256`` + ``raw_commit`` + ``matters``/``document_type``/``origin`` from the
   tree ``metadata.yaml``) and persists ``config/provenance-manifest.json``. This
   happens **before** anything is indexed (C-002 / NFR-001) — the mistake the retired
   Papra-webhook service made was indexing without provenance.
2. **Computes the resume set** — the ``raw_sha256`` values already in Elasticsearch.
3. **Ingests the difference** — a bounded batch of not-yet-indexed, non-media files
   via the proven :func:`reingest_from_raw` path (direct Docling → enrich → index),
   emitting pipeline events.

The loop (:meth:`Reconciler.run_forever`) emits a heartbeat pipeline event every
cycle so observability infers liveness, sleeps ``interval``, and never dies on a
per-file error (bad docs dead-letter to the DLQ and the daemon continues). If Docling
is unreachable, text/passthrough files still flow (Docling is bypassed for them) while
OCR-needing files dead-letter and are retried on the next cycle — never indexed empty,
never crashing. See ADR 0011 and ``doc/runbooks/auto-ingestion-reconciler.md``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.extract.docling_client import DoclingClient
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import Manifest, ManifestEntry, build_entry
from goldberg_system.migrate.reingest import _SKIP_EXT, reingest_from_raw
from goldberg_system.observability.events import EventSink, PipelineEvent, safe_emit
from goldberg_system.provenance import now_iso
from goldberg_system.sinks.base import Sink

log = logging.getLogger("goldberg.reconcile")

# Statuses (from reingest's process_one) that mean "deterministically not indexable"
# — record the sha so we don't re-attempt it every cycle (which would clog the bounded
# batch). NB: extraction/enrichment *failures* are deliberately NOT here, so a
# Docling-down OCR file is retried on the next cycle.
_NON_INDEXABLE_STATUSES = ("skipped-empty", "skipped-media")


@dataclass
class ReconcileCycle:
    """Summary of one scan-and-ingest pass over ``goldberg-raw``."""

    started_at: str
    scanned: int  # candidate evidence files walked under goldberg-raw
    new: int  # provenance entries registered this cycle
    indexed: int  # documents indexed this cycle
    skipped: int  # already-indexed docs skipped (resume / idempotency)
    dead_lettered: int  # docs that failed extraction/enrich/index this cycle
    elapsed: float  # seconds

    def summary_line(self) -> str:
        return (
            f"[{self.started_at}] scanned={self.scanned} new={self.new} "
            f"indexed={self.indexed} skipped={self.skipped} "
            f"dead_lettered={self.dead_lettered} elapsed={self.elapsed:.1f}s"
        )


@dataclass
class ProvenanceRefresh:
    """Result of a provenance-refresh pass."""

    scanned: int
    new_entries: list[ManifestEntry] = field(default_factory=list)
    manifest: Manifest = field(default_factory=lambda: Manifest({}))


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a temp file + atomic rename.

    Atomic so a reader (or a crash) never sees a half-written manifest — important
    over the SMB-mounted corpus.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def refresh_provenance(
    raw_root: Path | str,
    allowlist: Allowlist,
    manifest_path: Path | str,
    *,
    with_commit: bool = True,
) -> ProvenanceRefresh:
    """Register provenance for every allowlisted file not already in the manifest.

    Reuses the per-file derivation :func:`build_entry` (sha256 + git ``raw_commit`` +
    ``metadata.yaml`` chain), so this shares one code path with ``goldberg migrate
    manifest`` rather than forking a parallel provenance derivation. Only genuinely
    new content hashes get a git-commit lookup, so the per-cycle cost is bounded to
    new files. Persists the manifest (atomically) only when something changed.
    """
    root = Path(raw_root)
    mpath = Path(manifest_path)
    existing: dict[str, Any] = {}
    if mpath.is_file():
        try:
            existing = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):  # a corrupt manifest → rebuild it
            existing = {}
    known = {k.lower() for k in existing}
    new_entries: list[ManifestEntry] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # cheap allowlist guard first (also what counts as a "scanned" evidence file)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if rel.name == "metadata.yaml" or allowlist.is_excluded_file(rel):
            continue
        if allowlist.tree_for(rel) is None:
            continue
        scanned += 1
        entry = build_entry(
            root, rel, allowlist, with_commit=with_commit, known_shas=known
        )
        if entry is None:  # already registered (sha in the manifest)
            continue
        new_entries.append(entry)
        existing[entry.sha256] = asdict(entry)
        known.add(entry.sha256)
    if new_entries:
        _atomic_write_json(mpath, existing)
    return ProvenanceRefresh(
        scanned=scanned, new_entries=new_entries, manifest=Manifest(existing)
    )


class Reconciler:
    """Runs reconcile cycles over ``goldberg-raw`` (see the module docstring)."""

    def __init__(
        self,
        *,
        raw_root: Path | str,
        manifest_path: Path | str,
        allowlist: Allowlist,
        docling: DoclingClient,
        enricher: EnrichmentAdapter,
        sinks: list[Sink],
        already_indexed: Callable[[], set[str]],
        events: EventSink | None = None,
        batch: int = 50,
        workers: int = 2,
        with_commit: bool = True,
        run_id_prefix: str = "reconcile",
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self.raw_root = Path(raw_root)
        self.manifest_path = Path(manifest_path)
        self.allowlist = allowlist
        self.docling = docling
        self.enricher = enricher
        self.sinks = sinks
        self.already_indexed = already_indexed
        self.events = events
        self.batch = batch
        self.workers = workers
        self.with_commit = with_commit
        self.run_id_prefix = run_id_prefix
        self._sleep = sleep
        self._clock = clock
        # content hashes proven non-indexable this run (empty/media) — excluded from
        # future batches so they never starve genuinely new work.
        self._non_indexable: set[str] = set()
        self.last_cycle: ReconcileCycle | None = None

    # ── provenance helpers ────────────────────────────────────────────────────

    def load_manifest(self) -> Manifest:
        """Load the current manifest from disk (empty if none yet)."""
        if self.manifest_path.is_file():
            try:
                return Manifest(json.loads(self.manifest_path.read_text()))
            except (json.JSONDecodeError, OSError):
                return Manifest({})
        return Manifest({})

    def _select_pending(
        self, manifest: Manifest, skip: set[str]
    ) -> list[tuple[str, dict]]:
        """The bounded batch of not-yet-indexed, ingestable entries for this cycle.

        Excludes already-indexed shas (resume), known-non-indexable shas (empty/media
        seen this run), and media extensions — so the batch is spent on real pending
        work and the daemon always makes forward progress (FR-007).
        """
        pending: list[tuple[str, dict]] = []
        for sha, entry in manifest.items():
            if sha in skip or sha in self._non_indexable:
                continue
            if Path(entry.get("raw_path", "")).suffix.lower() in _SKIP_EXT:
                continue
            pending.append((sha, entry))
            if len(pending) >= self.batch:
                break
        return pending

    def pending_count(self, manifest: Manifest, skip: set[str]) -> int:
        """Number of entries that would be ingested next cycle (for tests/inspection)."""
        return len(self._select_pending(manifest, skip))

    # ── the cycle ─────────────────────────────────────────────────────────────

    def _heartbeat(self, run_id: str) -> None:
        """Emit a pipeline event every cycle so observability infers liveness."""
        safe_emit(
            self.events,
            PipelineEvent.make(
                "reconciler",
                "received",
                "started",
                run_id=run_id,
                reason="reconcile-heartbeat",
            ),
        )

    def run_cycle(self) -> ReconcileCycle:
        """Run one scan-and-ingest pass; never raises on a per-document error."""
        started = self._clock()
        t0 = time.monotonic()
        run_id = f"{self.run_id_prefix}-{started}"
        self._heartbeat(run_id)

        # a. provenance BEFORE indexing (persisted to the manifest)
        refresh = refresh_provenance(
            self.raw_root,
            self.allowlist,
            self.manifest_path,
            with_commit=self.with_commit,
        )
        manifest = refresh.manifest

        # b. resume set — what is already indexed
        skip = set(self.already_indexed())

        # c. ingest a bounded batch of the difference
        pending = self._select_pending(manifest, skip)
        only = {entry.get("raw_path", "") for _, entry in pending}
        path_to_sha = {entry.get("raw_path", ""): sha for sha, entry in pending}

        indexed = 0
        dead = 0
        if only:

            def on_doc(raw_path: str, status: str) -> None:
                if status in _NON_INDEXABLE_STATUSES:
                    sha = path_to_sha.get(raw_path)
                    if sha:
                        self._non_indexable.add(sha)
                log.info("reconcile [%s] %s", status, raw_path)

            report = reingest_from_raw(
                self.raw_root,
                manifest,
                self.docling,
                self.enricher,
                self.sinks,
                events=self.events,
                run_id=run_id,
                only=only,
                skip_shas=skip or None,
                workers=self.workers,
                on_doc=on_doc,
            )
            indexed = report.indexed
            dead = report.failures + report.missing_file

        skipped = sum(1 for sha, _ in manifest.items() if sha in skip)
        cycle = ReconcileCycle(
            started_at=started,
            scanned=refresh.scanned,
            new=len(refresh.new_entries),
            indexed=indexed,
            skipped=skipped,
            dead_lettered=dead,
            elapsed=time.monotonic() - t0,
        )
        self.last_cycle = cycle
        return cycle

    def run_forever(
        self,
        interval: float,
        *,
        max_cycles: int | None = None,
        on_cycle: Callable[[ReconcileCycle], None] | None = None,
    ) -> None:
        """Run cycles forever (or ``max_cycles`` times), sleeping ``interval`` between.

        A cycle that raises is logged and swallowed — the daemon must never die (FR-008).
        """
        count = 0
        while True:
            try:
                cycle = self.run_cycle()
                if on_cycle is not None:
                    on_cycle(cycle)
            except Exception:  # noqa: BLE001 - a cycle failure must not kill the daemon
                log.exception("reconcile cycle failed; continuing")
            count += 1
            if max_cycles is not None and count >= max_cycles:
                return
            self._sleep(interval)
