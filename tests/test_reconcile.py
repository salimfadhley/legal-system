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
