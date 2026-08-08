"""Bounded, one-shot startup catch-up over goldberg-raw (WP03, FR-007 / NFR-002).

When the ingest service starts it may have missed commit events (downtime, a fresh
deploy). :func:`run_catchup` closes that gap in a **single bounded pass** — it never
loops, so it does not reintroduce the polling the event-driven design removed
(NFR-002). One pass:

1. **refresh provenance** — register a manifest entry for every allowlisted file not
   yet known (provenance BEFORE indexing, C-002 / NFR-001);
2. **compute the resume set** — the raw SHA-256s already indexed;
3. **select the bounded difference** — not-yet-indexed, non-media entries, capped at
   ``batch``;
4. **process** each via the reused :func:`reingest_from_raw` (direct Docling → enrich
   → index), emitting the same pipeline events under a ``catchup-<ts>`` run id.

The provenance-refresh and diff-selection logic is **extracted here** from the polling
reconciler (``reconcile/reconciler.py``, removed in WP05) so it is built only on stable
primitives — :func:`build_entry`, :class:`Manifest`, and the reingest ``_SKIP_EXT``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.exclusion_registry import ExclusionEntry, ExclusionRegistry
from goldberg_system.extract.docling_client import DoclingClient
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import (
    Manifest,
    ManifestEntry,
    build_entry,
    entry_is_no_index,
)
from goldberg_system.migrate.reingest import _SKIP_EXT, reingest_from_raw
from goldberg_system.observability.events import EventSink
from goldberg_system.observability.restricted_alert import record_blocked_reingest
from goldberg_system.provenance import now_iso
from goldberg_system.sinks.base import Sink

log = logging.getLogger("goldberg.ingest")


@dataclass
class ProvenanceRefresh:
    """Result of a provenance-refresh pass over goldberg-raw."""

    scanned: int
    new_entries: list[ManifestEntry] = field(default_factory=list)
    manifest: Manifest = field(default_factory=lambda: Manifest({}))


@dataclass
class CatchupReport:
    """Summary of the one-shot catch-up pass (surfaced on ``/health``)."""

    run_id: str
    scanned: int  # allowlisted evidence files walked
    new: int  # provenance entries registered this pass
    pending: int  # entries selected for ingest this pass (bounded by batch)
    indexed: int  # documents indexed this pass
    skipped: int  # already-indexed entries skipped (resume / idempotency)
    dead_lettered: int  # entries that failed extract/enrich/index this pass
    elapsed: float  # seconds
    remaining_pending: int = 0  # TRUE pending NOT reached this bounded pass (backlog)
    no_index_skipped: int = 0  # restricted (no_index) docs excluded from ingestion
    deliberately_excluded: int = 0  # registered exclusions the healer refused to re-add

    @property
    def degraded(self) -> bool:
        """True when this bounded pass left un-processed pending work (a backlog).

        A single catch-up pass is bounded to ``batch`` (NFR-002: no polling loop). If
        more than ``batch`` files were missed while down, the surplus have a manifest
        entry but no index/DLQ record — a hole in the "no silently dropped document"
        guarantee. We surface it as ``remaining_pending`` and mark health degraded so
        ops run another ``goldberg ingest catchup`` (or let the live stream drain it),
        instead of leaving it silent (FR-007).
        """
        return self.remaining_pending > 0

    def summary_line(self) -> str:
        return (
            f"[{self.run_id}] scanned={self.scanned} new={self.new} "
            f"pending={self.pending} indexed={self.indexed} "
            f"skipped={self.skipped} dead_lettered={self.dead_lettered} "
            f"remaining_pending={self.remaining_pending} "
            f"no_index_skipped={self.no_index_skipped} "
            f"deliberately_excluded={self.deliberately_excluded} "
            f"degraded={self.degraded} elapsed={self.elapsed:.1f}s"
        )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as pretty JSON via a temp file + atomic rename.

    Atomic so a concurrent reader (or a crash) never sees a half-written manifest —
    important over the SMB-mounted corpus.
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
    the ``metadata.yaml`` chain), so this shares one code path with ``goldberg migrate
    manifest``. Only genuinely new content hashes trigger a git-commit lookup, so the
    per-pass cost is bounded to new files. Persists the manifest (atomically) only when
    something changed.
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


def entry_is_ingestable(
    entry: dict, registry: ExclusionRegistry | None = None
) -> bool:
    """The single select-layer predicate: is this entry eligible to be indexed?

    ONE skip list, shared by catch-up selection (:func:`select_pending` /
    :func:`count_pending`) AND the raw-vs-index reconciler
    (:mod:`goldberg_system.ingest.reconcile`), so neither can drift from the other.
    It excludes exactly what catch-up excludes at selection time:

    * **deliberately-excluded** entries — a ``raw_path``/``raw_sha256`` in the
      exclusion registry (a purge under a court undertaking, etc.). This is the
      class-fault fix: a registered exclusion is NEVER a gap to heal, even if it
      carries no ``no_index`` sidecar;
    * restricted (``no_index``) subtrees — provenance-tracked but never indexed; and
    * media extensions (:data:`_SKIP_EXT`) — no OCR-able text.

    The *walk* layer (``.git`` internals, folder ``metadata.yaml``, ``exclude_globs``
    matches, files outside any allowlisted tree) is applied earlier by
    :func:`build_entry` / :func:`refresh_provenance`, not here.
    """
    if registry is not None and registry.excludes_entry(entry):  # deliberate purge
        return False
    if entry_is_no_index(entry):  # restricted subtree — never index (recursive)
        return False
    if Path(entry.get("raw_path", "")).suffix.lower() in _SKIP_EXT:  # media
        return False
    return True


def select_pending(
    manifest: Manifest,
    skip: set[str],
    batch: int,
    *,
    non_indexable: frozenset[str] = frozenset(),
    registry: ExclusionRegistry | None = None,
) -> list[tuple[str, dict]]:
    """The bounded batch of not-yet-indexed, ingestable manifest entries.

    Extracted from the reconciler's diff selection: excludes already-indexed shas
    (resume / idempotency), any ``non_indexable`` shas (empty/media proven this run),
    and everything :func:`entry_is_ingestable` rejects (deliberately-excluded +
    restricted + media) — so the batch is spent on real pending work, the pass always
    makes forward progress (FR-007), and a registered exclusion can never be selected.
    """
    pending: list[tuple[str, dict]] = []
    for sha, entry in manifest.items():
        if sha in skip or sha in non_indexable:
            continue
        if not entry_is_ingestable(entry, registry):
            continue
        pending.append((sha, entry))
        if len(pending) >= batch:
            break
    return pending


def count_pending(
    manifest: Manifest,
    skip: set[str],
    *,
    non_indexable: frozenset[str] = frozenset(),
    registry: ExclusionRegistry | None = None,
) -> int:
    """The TRUE total of not-yet-indexed, ingestable manifest entries (no batch cap).

    Same predicate as :func:`select_pending` but unbounded — used to compute
    ``remaining_pending`` so a backlog larger than one catch-up ``batch`` is reported
    (and health marked degraded) rather than silently deferred (FR-007).
    """
    total = 0
    for sha, entry in manifest.items():
        if sha in skip or sha in non_indexable:
            continue
        if not entry_is_ingestable(entry, registry):  # same shared skip list
            continue
        total += 1
    return total


def detect_blocked_reingests(
    manifest: Manifest,
    skip: set[str],
    registry: ExclusionRegistry,
) -> list[tuple[str, dict, ExclusionEntry]]:
    """Registered exclusions the healer WOULD have (re-)added this pass — to alarm on.

    An entry qualifies when it is not already indexed (``sha not in skip`` — so it is a
    genuine re-add attempt, the exact failing scenario), is not a media file (which
    legitimately never indexes anyway), and matches the exclusion registry. Everything
    returned is logged CRITICAL and recorded as a first-class alert by :func:`run_catchup`;
    it is the "something loudly saying so" casework required.
    """
    blocked: list[tuple[str, dict, ExclusionEntry]] = []
    for sha, entry in manifest.items():
        if sha in skip:  # already indexed — not a re-add attempt this pass
            continue
        if Path(entry.get("raw_path", "")).suffix.lower() in _SKIP_EXT:
            continue  # media never indexes; not a meaningful "would have added"
        match = registry.match_entry(entry)
        if match is not None:
            blocked.append((sha, entry, match))
    return blocked


def log_no_index_skips(manifest: Manifest) -> int:
    """Log every ``no_index`` manifest entry at INFO and return the count.

    The restricted-subtree skip is an *expected* outcome, not a failure — but it must
    never be silent (same philosophy as dead-lettering an un-decryptable doc, only
    quieter). We emit one ``skipped <path> — no_index: <reason>`` line per restricted
    document so the exclusion is visible and auditable, and surface the total on the
    :class:`CatchupReport`.
    """
    count = 0
    for _sha, entry in manifest.items():
        if not entry_is_no_index(entry):
            continue
        count += 1
        log.info(
            "skipped %s — no_index: %s",
            entry.get("raw_path", "?"),
            entry.get("no_index_reason") or "(no reason given)",
        )
    return count


def run_catchup(
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
    run_id_prefix: str = "catchup",
    clock: Callable[[], str] = now_iso,
    registry: ExclusionRegistry | None = None,
    alert_log: Path | str | None = None,
) -> CatchupReport:
    """Run exactly one bounded catch-up pass and return its :class:`CatchupReport`.

    A single pass — **no loop** (NFR-002): the event-driven processor, not polling,
    keeps the index current after this returns. Never raises on a per-document error
    (bad docs dead-letter via the reingest path and are counted).

    ``registry`` (default: the tracked :func:`ExclusionRegistry.load_default`) is the
    class-fault guard: a registered exclusion is never selected, and encountering one
    that WOULD have been re-added is logged CRITICAL and recorded as a first-class
    alert (``alert_log`` → :func:`~goldberg_system.observability.restricted_alert.record_blocked_reingest`).
    """
    started = clock()
    t0 = time.monotonic()
    run_id = f"{run_id_prefix}-{started}"
    if registry is None:
        registry = ExclusionRegistry.load_default()

    # 1. provenance BEFORE indexing (persisted to the manifest)
    refresh = refresh_provenance(
        raw_root, allowlist, manifest_path, with_commit=with_commit
    )
    manifest = refresh.manifest

    # 1b. restricted subtrees (no_index) are excluded from ingestion — logged, counted,
    #     never silently dropped (still provenance-tracked in the manifest).
    no_index_skipped = log_no_index_skips(manifest)

    # 2. resume set — what is already indexed
    skip = set(already_indexed())

    # 2b. THE ALARM: registered exclusions this pass would otherwise have re-added must
    #     be impossible to miss — CRITICAL log + a durable, named status alert. The
    #     absence of an alert here was itself the fault.
    blocked = detect_blocked_reingests(manifest, skip, registry)
    for _sha, entry, match in blocked:
        record_blocked_reingest(
            entry.get("raw_path", "?"),
            match.reason,
            source="catchup",
            log_path=alert_log,
        )

    # 3. select the bounded difference — and count the TRUE (unbounded) pending total
    #    so any backlog beyond this batch is surfaced (remaining_pending), not silent.
    #    The registry is threaded through BOTH so a deliberate exclusion is never a gap.
    total_pending = count_pending(manifest, skip, registry=registry)
    pending = select_pending(manifest, skip, batch, registry=registry)
    only = {entry.get("raw_path", "") for _, entry in pending}

    # 4. process the difference via the reused reingest path
    indexed = 0
    dead = 0
    if only:
        report = reingest_from_raw(
            raw_root,
            manifest,
            docling,
            enricher,
            sinks,
            events=events,
            run_id=run_id,
            only=only,
            skip_shas=skip or None,
            workers=workers,
        )
        indexed = report.indexed
        dead = report.failures + report.missing_file

    skipped = sum(1 for sha, _ in manifest.items() if sha in skip)
    # Pending we did NOT reach this bounded pass — the backlog to surface. (Entries
    # attempted this pass that then dead-lettered have a DLQ record and are NOT counted
    # here; this is the *un-attempted* surplus with no index/DLQ trace.)
    remaining_pending = max(0, total_pending - len(pending))
    return CatchupReport(
        run_id=run_id,
        scanned=refresh.scanned,
        new=len(refresh.new_entries),
        pending=len(pending),
        indexed=indexed,
        skipped=skipped,
        dead_lettered=dead,
        elapsed=time.monotonic() - t0,
        remaining_pending=remaining_pending,
        no_index_skipped=no_index_skipped,
        deliberately_excluded=len(blocked),
    )


async def run_periodic_catchup(
    *,
    interval: float,
    run_pass: Callable[[], Awaitable[CatchupReport]],
    should_continue: Callable[[], bool],
    on_report: Callable[[CatchupReport], None] | None = None,
    wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Re-run the bounded catch-up every ``interval`` seconds until told to stop.

    Periodic **self-healing** (task #2): the one-shot startup catch-up only closes the
    gap that existed at boot. If the git publish-commit hook silently fails while the
    service is up (the exact ~2-day failure this work exists to prevent), the gap would
    otherwise grow unbounded until the next restart. A running consumer that re-runs the
    *same* bounded ``run_catchup`` on a timer caps that growth at one interval.

    This is deliberately **decoupled from wall-clock time and from ``run_catchup``'s
    internals** so it is unit-testable without sleeping: inject ``wait`` (the delay
    between passes) and ``run_pass`` (a zero-arg coroutine that performs one bounded
    pass — production wraps :func:`run_catchup` in :func:`asyncio.to_thread` so its
    blocking CPU/IO never stalls the event loop / the NATS consumer). ``should_continue``
    is checked both before waiting and after, so a stop requested mid-interval ends the
    loop promptly without an extra pass. Returns the number of passes actually run.
    """
    passes = 0
    while should_continue():
        await wait(interval)
        if not should_continue():  # stop requested during the interval → no extra pass
            break
        report = await run_pass()
        passes += 1
        if on_report is not None:
            on_report(report)
    return passes
