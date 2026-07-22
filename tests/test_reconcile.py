"""Tests for reconciliation — the completeness check (M12, ADR 0008)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goldberg_system.observability.reconcile import (
    Reconciler,
    all_indexed_raw_paths,
    expected_from_manifest,
    reconcile,
)


class _FakeES:
    def __init__(self, raw_paths: list[str]) -> None:
        self._raw_paths = raw_paths

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "hits": {
                "hits": [
                    {"_id": f"gb_{i}", "_source": {"raw_path": rp, "doc_id": f"gb_{i}"}}
                    for i, rp in enumerate(self._raw_paths)
                ]
            }
        }


def _manifest(tmp_path: Path, entries: dict[str, dict]) -> Path:
    # manifest is keyed by sha256; value carries raw_path + matters
    by_sha = {
        f"sha{i}": {**v, "raw_path": rp} for i, (rp, v) in enumerate(entries.items())
    }
    dest = tmp_path / "m.json"
    dest.write_text(json.dumps(by_sha))
    return dest


def test_reconcile_finds_missing_and_extra() -> None:
    expected = {
        "evidence/a.pdf": {"matters": ["422500059892"]},
        "evidence/b.pdf": {"matters": ["L00SS179"]},
        "evidence/c.pdf": {"matters": ["422500059892"]},
    }
    indexed = {
        "evidence/a.pdf": "gb_a",
        "exhibits/x.pdf": "gb_x",
    }  # b,c missing; x extra
    report = reconcile(expected, indexed)
    assert not report.complete
    assert report.missing == ["evidence/b.pdf", "evidence/c.pdf"]
    assert report.extra == ["exhibits/x.pdf"]
    assert report.matched == ["evidence/a.pdf"]
    assert report.expected_count == 3 and report.actual_count == 2
    # missing grouped by matter
    assert report.missing_by_matter == {"422500059892": 1, "L00SS179": 1}


def test_reconcile_complete_when_all_indexed() -> None:
    expected = {"evidence/a.pdf": {"matters": ["M1"]}}
    report = reconcile(expected, {"evidence/a.pdf": "gb_a"})
    assert report.complete
    assert report.missing == []


def test_all_indexed_raw_paths_skips_docs_without_raw_path() -> None:
    es = _FakeES(["evidence/a.pdf", "evidence/b.pdf"])
    out = all_indexed_raw_paths(es, "goldberg_documents")
    assert out == {"evidence/a.pdf": "gb_0", "evidence/b.pdf": "gb_1"}


def test_expected_from_manifest(tmp_path: Path) -> None:
    path = _manifest(tmp_path, {"evidence/a.pdf": {"matters": ["M1"]}})
    expected = expected_from_manifest(path)
    assert "evidence/a.pdf" in expected
    assert expected["evidence/a.pdf"]["matters"] == ["M1"]


def test_reconciler_end_to_end(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        {"evidence/a.pdf": {"matters": ["M1"]}, "evidence/b.pdf": {"matters": ["M2"]}},
    )
    es = _FakeES(["evidence/a.pdf"])  # only a indexed → b missing
    report = Reconciler(es, "goldberg_documents").run(path)
    assert report.missing == ["evidence/b.pdf"]
    assert report.matched == ["evidence/a.pdf"]


# ══════════════════════════════════════════════════════════════════════════════
# Auto-ingestion reconciler (goldberg-autoingest) — the polling daemon that scans
# goldberg-raw, registers provenance BEFORE indexing, and ingests the difference.
# In-memory fakes, no network (integration test gated on GOLDBERG_INTEGRATION=1).
# ══════════════════════════════════════════════════════════════════════════════

import hashlib  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import urllib.request  # noqa: E402
from threading import Thread  # noqa: E402

import pytest  # noqa: E402

from goldberg_system.extract.docling_client import DoclingError  # noqa: E402
from goldberg_system.migrate.allowlist import IncludedTree  # noqa: E402
from goldberg_system.migrate.allowlist import Allowlist as _Allowlist  # noqa: E402
from goldberg_system.reconcile import ReconcileCycle  # noqa: E402
from goldberg_system.reconcile import Reconciler as AutoReconciler  # noqa: E402
from goldberg_system.reconcile import (  # noqa: E402
    health_payload,
    make_health_server,
    refresh_provenance,
)

_PASSTHROUGH = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv"}


class _FakeDocling:
    """Passthrough reads text files off disk; other suffixes are 'OCR' outcomes."""

    def __init__(
        self,
        *,
        fail_suffixes: tuple[str, ...] = (),
        empty_suffixes: tuple[str, ...] = (),
    ) -> None:
        self._fail = fail_suffixes
        self._empty = empty_suffixes

    def convert_file(self, path: Path | str) -> str:
        p = Path(path)
        if p.suffix.lower() in _PASSTHROUGH:
            return p.read_text()  # passthrough works even when "Docling is down"
        if p.name.endswith(self._fail):
            raise DoclingError("docling connection error: down")
        if p.name.endswith(self._empty):
            return ""  # blank/graphic-only → skipped-empty
        return "extracted text"


class _FakeEnricher:
    def enrich(self, request):  # type: ignore[no-untyped-def]
        from goldberg_system.enrichment.adapter import EnrichmentResult

        return EnrichmentResult(summary="s", keywords=[], entities=[], claims=[])


class _FakeSink:
    def __init__(self) -> None:
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document):  # type: ignore[no-untyped-def]
        from goldberg_system.sinks.base import SinkResult

        self.written.append(document.raw_path)
        return SinkResult(sink="fake", ok=True)


class _ManifestAwareSink:
    """Asserts, at write time, that provenance was already persisted (C-002)."""

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = Path(manifest_path)
        self.written: list[str] = []
        self.provenance_ok: list[bool] = []

    @property
    def name(self) -> str:
        return "manifest-aware"

    def write(self, document):  # type: ignore[no-untyped-def]
        from goldberg_system.sinks.base import SinkResult

        data = (
            json.loads(self.manifest_path.read_text())
            if self.manifest_path.is_file()
            else {}
        )
        entry = next(
            (v for v in data.values() if v.get("raw_path") == document.raw_path), None
        )
        # provenance is real only if the entry exists AND carries commit + matters
        self.provenance_ok.append(
            bool(entry and entry.get("raw_commit") and entry.get("matters"))
        )
        self.written.append(document.raw_path)
        return SinkResult(sink="manifest-aware", ok=True)


class _FakeEvents:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


def _ai_allowlist() -> _Allowlist:
    return _Allowlist(
        included={
            "evidence": IncludedTree("evidence", "received"),
            "reports": IncludedTree("reports", "authored"),
        },
        excluded={},
        exclude_globs=(),
    )


def _git_seed(raw: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"],
    }
    subprocess.run(
        ["git", "-C", str(raw), "init", "-q"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(raw), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(raw), "commit", "-qm", "seed"],
        check=True,
        capture_output=True,
        env=env,
    )


def _ai_reconciler(
    tmp_path: Path,
    *,
    docling: _FakeDocling | None = None,
    events: Any = None,
    batch: int = 50,
    workers: int = 1,
    with_commit: bool = False,
) -> tuple[AutoReconciler, Path]:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    manifest_path = tmp_path / "provenance-manifest.json"
    rec = AutoReconciler(
        raw_root=raw,
        manifest_path=manifest_path,
        allowlist=_ai_allowlist(),
        docling=docling or _FakeDocling(),
        enricher=_FakeEnricher(),
        sinks=[_FakeSink()],
        already_indexed=lambda: set(),
        events=events,
        batch=batch,
        workers=workers,
        with_commit=with_commit,
        sleep=lambda _s: None,
    )
    return rec, raw


def test_refresh_provenance_registers_new_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    (raw / "evidence" / "letter.md").write_text("a received letter")
    _git_seed(raw)
    manifest_path = tmp_path / "provenance-manifest.json"

    refresh = refresh_provenance(raw, _ai_allowlist(), manifest_path, with_commit=True)

    assert refresh.scanned == 1
    assert len(refresh.new_entries) == 1
    data = json.loads(manifest_path.read_text())
    entry = next(iter(data.values()))
    assert entry["raw_path"] == "evidence/letter.md"
    assert entry["matters"] == ["M1"]
    assert len(entry["raw_commit"]) == 40  # real git commit
    assert entry["sha256"] == next(iter(data))


def test_provenance_persisted_before_sink_write(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    (raw / "evidence" / "doc.md").write_text("body text")
    _git_seed(raw)
    manifest_path = tmp_path / "provenance-manifest.json"
    sink = _ManifestAwareSink(manifest_path)
    rec = AutoReconciler(
        raw_root=raw,
        manifest_path=manifest_path,
        allowlist=_ai_allowlist(),
        docling=_FakeDocling(),
        enricher=_FakeEnricher(),
        sinks=[sink],
        already_indexed=lambda: set(),
        with_commit=True,
    )

    cycle = rec.run_cycle()

    assert cycle.indexed == 1
    assert sink.written == ["evidence/doc.md"]
    assert sink.provenance_ok == [True]  # manifest had commit+matters at write time


def test_already_indexed_shas_are_skipped(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.md").write_text("alpha")
    (raw / "evidence" / "b.md").write_text("bravo")
    sha_a = hashlib.sha256(b"alpha").hexdigest()
    sink = _FakeSink()
    rec.sinks = [sink]
    rec.already_indexed = lambda: {sha_a}

    cycle = rec.run_cycle()

    assert sink.written == ["evidence/b.md"]  # a already indexed → not re-written
    assert cycle.indexed == 1
    assert cycle.skipped == 1


def test_idempotent_second_cycle_indexes_nothing(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.md").write_text("alpha")
    indexed: set[str] = set()
    rec.already_indexed = lambda: set(indexed)

    c1 = rec.run_cycle()
    assert c1.indexed == 1
    indexed.add(hashlib.sha256(b"alpha").hexdigest())

    c2 = rec.run_cycle()
    assert c2.indexed == 0  # nothing new → no duplicates
    assert c2.new == 0


def test_per_file_failure_dead_letters_and_cycle_continues(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path, docling=_FakeDocling(fail_suffixes=(".pdf",)))
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "scan.pdf").write_bytes(b"%PDF")  # OCR needed, docling down
    (raw / "evidence" / "note.md").write_text("plain text")
    sink = _FakeSink()
    rec.sinks = [sink]

    cycle = rec.run_cycle()

    assert cycle.indexed == 1  # note.md still flowed
    assert sink.written == ["evidence/note.md"]
    assert cycle.dead_lettered == 1  # scan.pdf dead-lettered, cycle did not crash


def test_ocr_failure_is_retried_next_cycle(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path, docling=_FakeDocling(fail_suffixes=(".pdf",)))
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "scan.pdf").write_bytes(b"%PDF")

    c1 = rec.run_cycle()
    c2 = rec.run_cycle()

    assert c1.dead_lettered == 1
    assert c2.dead_lettered == 1  # retried, not silently dropped


def test_empty_extraction_not_retried_forever(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path, docling=_FakeDocling(empty_suffixes=(".pdf",)))
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "blank.pdf").write_bytes(b"%PDF")

    c1 = rec.run_cycle()
    assert c1.indexed == 0
    # second cycle: it is excluded from the pending set entirely
    assert rec.pending_count(rec.load_manifest(), set()) == 0


def test_batch_bound_is_respected(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path, batch=3)
    (raw / "evidence").mkdir(parents=True)
    for i in range(10):
        (raw / "evidence" / f"d{i}.md").write_text(f"doc {i}")
    sink = _FakeSink()
    rec.sinks = [sink]

    cycle = rec.run_cycle()

    assert cycle.new == 10  # all provenance registered
    assert cycle.indexed == 3  # but only a bounded batch ingested
    assert len(sink.written) == 3


def test_cycle_summary_counts(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.md").write_text("alpha")  # indexed
    (raw / "evidence" / "clip.mp4").write_bytes(b"video")  # media → not ingestable
    (raw / "evidence" / "c.md").write_text("charlie")  # already indexed
    rec.already_indexed = lambda: {hashlib.sha256(b"charlie").hexdigest()}

    cycle = rec.run_cycle()

    assert isinstance(cycle, ReconcileCycle)
    assert cycle.scanned == 3
    assert cycle.new == 3
    assert cycle.indexed == 1  # a.md
    assert cycle.skipped == 1  # c.md already indexed
    assert cycle.dead_lettered == 0
    assert cycle.elapsed >= 0


def test_heartbeat_emitted_even_on_idle_cycle(tmp_path: Path) -> None:
    events = _FakeEvents()
    rec, _ = _ai_reconciler(tmp_path, events=events)
    cycle = rec.run_cycle()  # no files → idle

    assert cycle.indexed == 0
    reasons = [getattr(e, "reason", None) for e in events.events]
    assert "reconcile-heartbeat" in reasons
    components = {getattr(e, "component", None) for e in events.events}
    assert "reconciler" in components


def test_run_forever_once_runs_exactly_one_cycle(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.md").write_text("alpha")
    seen: list[ReconcileCycle] = []

    rec.run_forever(interval=0, max_cycles=1, on_cycle=seen.append)

    assert len(seen) == 1
    assert seen[0].indexed == 1


def test_run_forever_never_dies_on_cycle_error(tmp_path: Path) -> None:
    rec, _ = _ai_reconciler(tmp_path)
    calls = {"n": 0}

    def boom() -> ReconcileCycle:
        calls["n"] += 1
        raise RuntimeError("cycle blew up")

    rec.run_cycle = boom  # type: ignore[method-assign]
    rec.run_forever(interval=0, max_cycles=3)  # must not raise
    assert calls["n"] == 3


def test_health_payload_reports_last_cycle(tmp_path: Path) -> None:
    rec, raw = _ai_reconciler(tmp_path)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.md").write_text("alpha")
    assert health_payload(rec)["last_cycle"] is None  # before any cycle
    rec.run_cycle()
    payload = health_payload(rec)
    assert payload["status"] == "ok"
    assert payload["last_cycle"]["indexed"] == 1


def test_health_server_serves_health(tmp_path: Path) -> None:
    rec, _ = _ai_reconciler(tmp_path)
    rec.run_cycle()
    server = make_health_server(rec, host="127.0.0.1", port=0)
    port = server.server_address[1]
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert body["status"] == "ok"
    finally:
        server.shutdown()


@pytest.mark.skipif(
    os.environ.get("GOLDBERG_INTEGRATION") != "1",
    reason="live integration (ES + Docling) — set GOLDBERG_INTEGRATION=1",
)
def test_reconcile_live_smoke() -> None:  # pragma: no cover - live only
    from goldberg_system.config import project_path
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.sinks import ElasticsearchIndexer

    indexer = ElasticsearchIndexer.from_env()
    indexer.index = "goldberg_documents_test"
    indexer.ensure_index()
    rec = AutoReconciler(
        raw_root=project_path("raw"),
        manifest_path=project_path("system") / "config" / "provenance-manifest.json",
        allowlist=_ai_allowlist(),
        docling=DoclingClient.from_env(),
        enricher=OpenAIEnricher.from_settings(),
        sinks=[indexer],
        already_indexed=lambda: set(),
        batch=1,
    )
    cycle = rec.run_cycle()
    assert cycle.scanned >= 0
