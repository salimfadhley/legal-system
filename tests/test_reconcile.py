"""Tests for reconciliation — the completeness check (M12, ADR 0008)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goldberg_system.observability.reconcile import (
    Reconciler,
    all_indexed_raw_paths,
    expected_from_manifest,
    find_orphans,
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


# --- orphan detection: the deletion axis the join can't see -------------------


def test_find_orphans_flags_deleted_source_still_indexed(tmp_path: Path) -> None:
    """A manifest entry whose raw file is gone but whose ES doc survives → orphan."""
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.pdf").write_text("present")  # a exists; b was deleted
    expected = {
        "evidence/a.pdf": {"sha256": "sha_a"},
        "evidence/b.pdf": {"sha256": "sha_b"},
    }
    indexed = {"evidence/a.pdf": "gb_a", "evidence/b.pdf": "gb_b"}
    report = find_orphans(expected, raw, indexed)
    assert report.checked == 2
    assert not report.clean
    assert [o.raw_path for o in report.orphans] == ["evidence/b.pdf"]
    orphan = report.orphans[0]
    assert orphan.indexed and orphan.doc_id == "gb_b"  # expungeable from ES
    assert orphan.sha256 == "sha_b"
    assert report.indexed_orphans == report.orphans


def test_find_orphans_clean_when_all_present(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "a.pdf").write_text("present")
    report = find_orphans({"evidence/a.pdf": {"sha256": "sha_a"}}, raw, {})
    assert report.clean and report.orphans == []


def test_find_orphans_manifest_only_when_no_es_doc(tmp_path: Path) -> None:
    """Source deleted AND no ES doc → still an orphan, but not expungeable."""
    raw = tmp_path / "raw"
    raw.mkdir()
    report = find_orphans({"evidence/gone.pdf": {"sha256": "s"}}, raw, indexed={})
    assert not report.clean
    assert report.orphans[0].indexed is False
    assert report.orphans[0].doc_id is None
    assert report.indexed_orphans == []


def test_reconcile_reports_complete_while_orphan_hides(tmp_path: Path) -> None:
    """The whole point: the join says COMPLETE, --orphans catches the deletion."""
    raw = tmp_path / "raw"
    raw.mkdir()  # the source file was deleted → not on disk
    expected = {"evidence/deleted.pdf": {"sha256": "s", "matters": ["M1"]}}
    indexed = {"evidence/deleted.pdf": "gb_x"}  # manifest + index still agree
    assert reconcile(expected, indexed).complete  # join is blind to the deletion
    orphans = find_orphans(expected, raw, indexed)  # the axis that isn't
    assert not orphans.clean and orphans.orphans[0].doc_id == "gb_x"
