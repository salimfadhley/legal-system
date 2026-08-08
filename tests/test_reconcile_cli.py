"""CLI tests for ``legal_system reconcile`` (report mode + --ingest --limit reporting).

The command must, with no live services, (a) report the exists-in-raw-but-not-indexed
gap grouped by tree, (b) with ``--ingest`` dispatch the bounded catch-up and REPORT how
many were dispatched vs remain — never silently capping — and (c) emit ``--json``.
Elasticsearch and the catch-up pipeline are faked via monkeypatch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from goldberg_system import cli
from goldberg_system.ingest.catchup import CatchupReport
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=("**/*.mp4",),
    )


def _seed(tmp_path: Path, bodies: dict[str, str]) -> Path:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    for name, body in bodies.items():
        (raw / "evidence" / name).write_text(body)
    return raw


class _FakeIndexer:
    """Stands in for ElasticsearchIndexer; .client.search returns the indexed shas."""

    def __init__(self, indexed: set[str]) -> None:
        self.index = "goldberg_documents"
        shas = list(indexed)

        class _Client:
            def search(self, **kwargs):  # noqa: ANN003
                return {
                    "hits": {
                        "total": {"value": len(shas)},
                        "hits": [{"_source": {"raw_sha256": s}} for s in shas],
                    }
                }

        self.client = _Client()


def _patch_report_deps(monkeypatch, tmp_path: Path, raw: Path, indexed: set[str]) -> None:
    from goldberg_system import config
    from goldberg_system.sinks import elasticsearch_indexer

    monkeypatch.setattr(config, "project_path", lambda which: raw if which == "raw" else tmp_path)
    monkeypatch.setattr(Allowlist, "load", classmethod(lambda cls, *a, **k: _allowlist()))
    monkeypatch.setattr(
        elasticsearch_indexer.ElasticsearchIndexer,
        "from_env",
        classmethod(lambda cls: _FakeIndexer(indexed)),
    )


def test_reconcile_report_lists_gap_and_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"present.txt": "indexed body", "gap.txt": "missing body"})
    _patch_report_deps(monkeypatch, tmp_path, raw, {_sha("indexed body")})

    result = CliRunner().invoke(cli.main, ["reconcile"])

    assert result.exit_code == 1, result.output  # a gap must alert (non-zero)
    assert "GAP" in result.output
    assert "evidence/gap.txt" in result.output
    assert "evidence/present.txt" not in result.output
    assert "1  evidence" in result.output  # grouped by tree


def test_reconcile_complete_exits_zero(monkeypatch, tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha", "b.txt": "bravo"})
    _patch_report_deps(monkeypatch, tmp_path, raw, {_sha("alpha"), _sha("bravo")})

    result = CliRunner().invoke(cli.main, ["reconcile"])

    assert result.exit_code == 0, result.output
    assert "COMPLETE" in result.output


def test_reconcile_json_output(monkeypatch, tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"gap.txt": "missing"})
    _patch_report_deps(monkeypatch, tmp_path, raw, set())

    result = CliRunner().invoke(cli.main, ["reconcile", "--json"])

    payload = json.loads(result.output)
    assert payload["complete"] is False
    assert payload["gap_count"] == 1
    assert payload["gap"][0]["raw_path"] == "evidence/gap.txt"
    assert payload["by_tree"] == {"evidence": 1}


def test_reconcile_ingest_reports_dispatched_and_remaining(monkeypatch, tmp_path: Path) -> None:
    # 3 files missing, --limit 2 → catch-up dispatches 2, reports 1 remaining. The
    # command must SAY it capped, never silently.
    raw = _seed(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})
    _patch_report_deps(monkeypatch, tmp_path, raw, set())

    captured: dict = {}

    def fake_build_ingest_deps(index_override=None):
        return ("raw", "manifest", "allow", "docling", "enricher", "indexer", "events",
                lambda: set())

    def fake_run_catchup(**kwargs):
        captured.update(kwargs)
        return CatchupReport(
            run_id="catchup-t", scanned=3, new=3, pending=2, indexed=2, skipped=0,
            dead_lettered=0, elapsed=0.1, remaining_pending=1,
        )

    monkeypatch.setattr(cli, "_build_ingest_deps", fake_build_ingest_deps)
    import goldberg_system.ingest as ingest_pkg
    monkeypatch.setattr(ingest_pkg, "run_catchup", fake_run_catchup)

    result = CliRunner().invoke(cli.main, ["reconcile", "--ingest", "--limit", "2"])

    assert captured["batch"] == 2  # --limit threaded through as the catch-up bound
    assert "dispatched=2 remaining=1" in result.output
    assert "NOT dispatched this pass" in result.output  # the cap is announced
    assert result.exit_code == 1  # 1 still unresolved → non-zero


def test_reconcile_ingest_json_includes_ingest_block(monkeypatch, tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "a"})
    _patch_report_deps(monkeypatch, tmp_path, raw, set())

    def fake_build_ingest_deps(index_override=None):
        return ("raw", "manifest", "allow", "docling", "enricher", "indexer", "events",
                lambda: set())

    def fake_run_catchup(**kwargs):
        return CatchupReport(
            run_id="catchup-t", scanned=1, new=1, pending=1, indexed=1, skipped=0,
            dead_lettered=0, elapsed=0.1, remaining_pending=0,
        )

    monkeypatch.setattr(cli, "_build_ingest_deps", fake_build_ingest_deps)
    import goldberg_system.ingest as ingest_pkg
    monkeypatch.setattr(ingest_pkg, "run_catchup", fake_run_catchup)

    result = CliRunner().invoke(cli.main, ["reconcile", "--ingest", "--json"])

    payload = json.loads(result.output)
    assert payload["ingest"]["dispatched"] == 1
    assert payload["ingest"]["remaining"] == 0
    assert result.exit_code == 0
