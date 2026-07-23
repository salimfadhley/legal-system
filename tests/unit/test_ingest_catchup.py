"""Unit tests for ``ingest.catchup.run_catchup`` (WP03, FR-007, NFR-002).

The catch-up is a single bounded pass: refresh provenance, compute already-indexed,
select the bounded pending difference, and ``process_one`` each via the reused
``reingest_from_raw`` path. Fakes stand in for Docling/enricher/sinks (no live
services); a temp raw dir provides the manifest source.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from goldberg_system.ingest.catchup import (
    CatchupReport,
    count_pending,
    run_catchup,
    select_pending,
)
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree
from goldberg_system.migrate.manifest import Manifest

_PASSTHROUGH_TEXT = {".md", ".txt"}


class _FakeDocling:
    def convert_file(self, path: Path | str) -> str:
        p = Path(path)
        if p.suffix.lower() in _PASSTHROUGH_TEXT:
            return p.read_text()
        return "extracted text"


class _FakeEnricher:
    def enrich(self, request: Any) -> Any:
        from goldberg_system.enrichment.adapter import EnrichmentResult

        return EnrichmentResult(summary="s", keywords=[], entities=[], claims=[])


class _FakeSink:
    def __init__(self) -> None:
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document: Any) -> Any:
        from goldberg_system.sinks.base import SinkResult

        self.written.append(document.raw_path)
        return SinkResult(sink="fake", ok=True)


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _seed_raw(tmp_path: Path, bodies: dict[str, str]) -> tuple[Path, Path]:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    for name, body in bodies.items():
        (raw / "evidence" / name).write_text(body)
    return raw, tmp_path / "provenance-manifest.json"


def _catchup(
    raw: Path,
    manifest_path: Path,
    *,
    already_indexed: set[str],
    batch: int,
    sink: _FakeSink,
) -> CatchupReport:
    return run_catchup(
        raw_root=raw,
        manifest_path=manifest_path,
        allowlist=_allowlist(),
        docling=_FakeDocling(),
        enricher=_FakeEnricher(),
        sinks=[sink],
        already_indexed=lambda: set(already_indexed),
        batch=batch,
        workers=1,
        with_commit=False,
        clock=lambda: "20260723T000000Z",
    )


# --------------------------------------------------------------------------- #
# select_pending (extracted diff selection)
# --------------------------------------------------------------------------- #
def test_select_pending_excludes_indexed_and_media_and_caps_at_batch() -> None:
    manifest = Manifest(
        {
            "s1": {"raw_path": "evidence/a.txt"},
            "s2": {"raw_path": "evidence/b.txt"},
            "s3": {"raw_path": "evidence/clip.mp4"},  # media → excluded
            "s4": {"raw_path": "evidence/c.txt"},
            "s5": {"raw_path": "evidence/d.txt"},
        }
    )
    pending = select_pending(manifest, skip={"s2"}, batch=2)

    paths = [e["raw_path"] for _, e in pending]
    assert "evidence/b.txt" not in paths  # already indexed
    assert "evidence/clip.mp4" not in paths  # media
    assert len(pending) == 2  # capped at batch


def test_count_pending_is_unbounded_true_total() -> None:
    # FIX 3 (cycle 2): count_pending returns the TRUE total pending (no batch cap),
    # so a backlog larger than one batch can be surfaced as remaining_pending.
    manifest = Manifest(
        {
            "s1": {"raw_path": "evidence/a.txt"},
            "s2": {"raw_path": "evidence/b.txt"},
            "s3": {"raw_path": "evidence/clip.mp4"},  # media → excluded
            "s4": {"raw_path": "evidence/c.txt"},
            "s5": {"raw_path": "evidence/d.txt"},
        }
    )
    # 5 entries, minus 1 media, minus 1 already-indexed = 3 pending (uncapped)
    assert count_pending(manifest, skip={"s2"}) == 3


# --------------------------------------------------------------------------- #
# run_catchup (single bounded pass)
# --------------------------------------------------------------------------- #
def test_processes_n_minus_k_capped_at_batch(tmp_path: Path) -> None:
    bodies = {f"d{i}.txt": f"doc {i}" for i in range(5)}  # N = 5
    raw, manifest_path = _seed_raw(tmp_path, bodies)
    already = {_sha("doc 0")}  # K = 1 already indexed
    sink = _FakeSink()

    report = _catchup(raw, manifest_path, already_indexed=already, batch=3, sink=sink)

    # N - K = 4, capped at batch=3
    assert report.indexed == 3
    assert len(sink.written) == 3
    assert report.skipped == 1  # the one already-indexed sha


def test_second_pass_after_indexing_is_noop(tmp_path: Path) -> None:
    bodies = {"a.txt": "alpha", "b.txt": "bravo"}  # N = 2
    raw, manifest_path = _seed_raw(tmp_path, bodies)
    sink1 = _FakeSink()

    first = _catchup(raw, manifest_path, already_indexed=set(), batch=50, sink=sink1)
    assert first.indexed == 2
    assert first.new == 2  # provenance registered this pass

    indexed = {_sha("alpha"), _sha("bravo")}
    sink2 = _FakeSink()
    second = _catchup(raw, manifest_path, already_indexed=indexed, batch=50, sink=sink2)

    assert second.indexed == 0  # nothing new → no duplicates (idempotent)
    assert second.new == 0  # manifest already has both entries
    assert sink2.written == []


# --------------------------------------------------------------------------- #
# FIX 3 (cycle 2): a backlog larger than one bounded pass must be SURFACED
# (remaining_pending + degraded), never left silent.
# --------------------------------------------------------------------------- #
def test_backlog_beyond_batch_is_surfaced_and_degraded(tmp_path: Path) -> None:
    bodies = {f"d{i}.txt": f"doc {i}" for i in range(5)}  # N = 5 pending
    raw, manifest_path = _seed_raw(tmp_path, bodies)
    sink = _FakeSink()

    report = _catchup(raw, manifest_path, already_indexed=set(), batch=2, sink=sink)

    assert report.indexed == 2  # only the bounded batch this pass
    assert report.pending == 2
    # The remaining 3 are NOT silently deferred — they are reported…
    assert report.remaining_pending == 3
    # …and health is marked degraded so ops run another catch-up (FR-007).
    assert report.degraded is True
    assert "remaining_pending=3" in report.summary_line()


def test_no_backlog_is_not_degraded(tmp_path: Path) -> None:
    bodies = {"a.txt": "alpha", "b.txt": "bravo"}  # N = 2, all fit in the batch
    raw, manifest_path = _seed_raw(tmp_path, bodies)
    sink = _FakeSink()

    report = _catchup(raw, manifest_path, already_indexed=set(), batch=50, sink=sink)

    assert report.indexed == 2
    assert report.remaining_pending == 0
    assert report.degraded is False
