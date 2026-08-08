"""Unit tests for the raw-vs-index gap classifier (``ingest.reconcile``).

The reconciler exists to make a committed-but-never-indexed document visible — so its
classification must be exactly right in BOTH degenerate directions. A bug that reports
everything-missing or nothing-missing is the precise false-comfort failure this guards
against, so the central test is a self-check that seeds one known-indexed sha and one
known-absent sha and asserts the gap is exactly the absent one.

It also proves the reconciler honours the SAME skip rules as catch-up (media, no_index,
metadata.yaml, exclude_globs, outside-tree) by sharing ``entry_is_ingestable`` and
``build_entry`` — never a second skip list.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from goldberg_system.ingest.reconcile import (
    GapReport,
    classify_path,
    indexed_raw_shas,
    reconcile_gap,
)
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=("**/*.mp4",),  # media excluded at the walk layer, like prod
    )


def _seed(tmp_path: Path, bodies: dict[str, str], *, meta: str = "case_number: M1\n") -> Path:
    raw = tmp_path / "raw"
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text(meta)
    for name, body in bodies.items():
        p = raw / "evidence" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return raw


# --------------------------------------------------------------------------- #
# THE SELF-CHECK: one known-indexed sha, one known-absent sha.
# --------------------------------------------------------------------------- #
def test_self_check_classifies_indexed_and_absent_exactly(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"present.txt": "already indexed", "gap.txt": "never ingested"})
    indexed = {_sha("already indexed")}  # present.txt IS in the index; gap.txt is NOT

    report = reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=indexed)

    gap_paths = {gf.raw_path for gf in report.gap}
    # Exactly the absent one — not empty (would hide the real hole) and not both
    # (would raise a false alarm on an indexed doc).
    assert gap_paths == {"evidence/gap.txt"}
    assert report.gap_count == 1
    assert report.scanned == 2
    assert report.complete is False
    assert report.by_tree == {"evidence": 1}
    assert report.gap[0].raw_sha256 == _sha("never ingested")


def test_complete_when_every_file_is_indexed(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha", "b.txt": "bravo"})
    indexed = {_sha("alpha"), _sha("bravo")}

    report = reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=indexed)

    assert report.complete is True
    assert report.gap == []
    assert report.scanned == 2


def test_all_missing_when_index_is_empty(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha", "b.txt": "bravo"})

    report = reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=set())

    assert report.gap_count == 2
    assert report.complete is False


# --------------------------------------------------------------------------- #
# Skip rules are shared with catch-up (no second list).
# --------------------------------------------------------------------------- #
def test_media_and_metadata_and_no_index_are_excluded_from_the_gap(tmp_path: Path) -> None:
    # A restricted (no_index) subtree, a media file, and folder metadata.yaml must NOT
    # count as gap even with an empty index — they are legitimately never indexed.
    # Use an allowlist WITHOUT the media glob so clip.mp4 passes the walk layer and is
    # caught by the SELECT-layer _SKIP_EXT rule (exercising the shared skip predicate).
    allow = Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )
    raw = _seed(tmp_path, {"doc.txt": "real evidence", "clip.mp4": "binary-ish"})
    # restricted subtree with its own metadata.yaml turning off indexing
    restricted = raw / "evidence" / "sealed"
    restricted.mkdir()
    (restricted / "metadata.yaml").write_text("no_index: true\nno_index_reason: sealed\n")
    (restricted / "secret.txt").write_text("restricted material")

    report = reconcile_gap(raw_root=raw, allowlist=allow, indexed_shas=set())

    gap_paths = {gf.raw_path for gf in report.gap}
    assert gap_paths == {"evidence/doc.txt"}  # only the real, ingestable evidence
    assert report.skipped_media == 1  # clip.mp4 — select-layer _SKIP_EXT
    assert report.skipped_no_index == 1  # evidence/sealed/secret.txt
    assert report.scanned == 1


def test_files_outside_allowlisted_trees_are_ignored(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"doc.txt": "evidence"})
    junk = raw / "not_allowlisted"
    junk.mkdir()
    (junk / "x.txt").write_text("outside any tree")

    report = reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=set())

    assert {gf.raw_path for gf in report.gap} == {"evidence/doc.txt"}


def test_report_is_side_effect_free_no_manifest_written(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha"})
    before = {p.name for p in raw.rglob("*")}

    reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=set())

    after = {p.name for p in raw.rglob("*")}
    assert after == before  # no manifest / temp files created
    assert not (tmp_path / "provenance-manifest.json").exists()


def test_sha_comparison_is_case_insensitive(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha"})
    indexed = {_sha("alpha").upper()}  # index stored an upper-cased hash

    report = reconcile_gap(raw_root=raw, allowlist=_allowlist(), indexed_shas=indexed)

    assert report.complete is True


# --------------------------------------------------------------------------- #
# classify_path — the MCP point query.
# --------------------------------------------------------------------------- #
def test_classify_path_invisible_gap(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"gap.txt": "never ingested"})

    out = classify_path(
        raw_root=raw,
        raw_path="evidence/gap.txt",
        indexed_shas=set(),
        allowlist=_allowlist(),
    )

    assert out["in_raw"] is True
    assert out["in_index"] is False
    assert out["ingestable"] is True
    assert out["invisible_gap"] is True
    assert out["raw_sha256"] == _sha("never ingested")


def test_classify_path_indexed_is_not_a_gap(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha"})

    out = classify_path(
        raw_root=raw,
        raw_path="evidence/a.txt",
        indexed_shas={_sha("alpha")},
        allowlist=_allowlist(),
    )

    assert out["in_index"] is True
    assert out["invisible_gap"] is False


def test_classify_path_absent_from_raw(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha"})

    out = classify_path(
        raw_root=raw,
        raw_path="evidence/nope.txt",
        indexed_shas=set(),
        allowlist=_allowlist(),
    )

    assert out["in_raw"] is False
    assert out["in_index"] is False
    assert out["raw_sha256"] is None
    assert out["invisible_gap"] is False


def test_classify_path_restricted_is_not_invisible_gap(tmp_path: Path) -> None:
    raw = _seed(tmp_path, {"a.txt": "alpha"})
    sealed = raw / "evidence" / "sealed"
    sealed.mkdir()
    (sealed / "metadata.yaml").write_text("no_index: true\n")
    (sealed / "s.txt").write_text("secret")

    out = classify_path(
        raw_root=raw,
        raw_path="evidence/sealed/s.txt",
        indexed_shas=set(),
        allowlist=_allowlist(),
    )

    assert out["in_raw"] is True
    assert out["ingestable"] is False  # restricted → expected to be absent
    assert out["invisible_gap"] is False


# --------------------------------------------------------------------------- #
# indexed_raw_shas — the shared ES lookup + truncation flag.
# --------------------------------------------------------------------------- #
class _FakeES:
    def __init__(self, shas: list[str], total: int | None = None) -> None:
        self._shas = shas
        self._total = total if total is not None else len(shas)

    def search(self, **kwargs):  # noqa: ANN003
        return {
            "hits": {
                "total": {"value": self._total},
                "hits": [{"_source": {"raw_sha256": s}} for s in self._shas],
            }
        }


def test_indexed_raw_shas_lowercases_and_flags_truncation() -> None:
    shas, truncated = indexed_raw_shas(_FakeES(["ABC", "Def"]), "idx")
    assert shas == {"abc", "def"}
    assert truncated is False

    shas2, truncated2 = indexed_raw_shas(_FakeES(["a", "b"], total=100), "idx")
    assert truncated2 is True  # total > returned → surfaced, not swallowed


def test_gapreport_to_dict_shape() -> None:
    report = GapReport(scanned=3, indexed_shas=2, gap=[], skipped_no_index=1)
    d = report.to_dict()
    assert d["complete"] is True
    assert d["scanned"] == 3
    assert d["gap"] == []
    assert d["by_tree"] == {}
