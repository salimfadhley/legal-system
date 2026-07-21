"""Tests for the corpus migration tooling (M8, ADR 0006)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import yaml

from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import build_manifest, write_manifest
from goldberg_system.migrate.populate_raw import gitattributes_content, populate_raw

_ALLOWLIST_YAML = """
include:
  evidence: { origin: received }
  reports:  { origin: authored }
exclude:
  tmp: scratch
exclude_globs:
  - "**/.DS_Store"
  - "**/*.pyc"
"""


def _allowlist(tmp_path: Path) -> Allowlist:
    cfg = tmp_path / "allowlist.yaml"
    cfg.write_text(_ALLOWLIST_YAML)
    return Allowlist.load(cfg)


def _make_archive(root: Path) -> None:
    # evidence tree: carries a case_number in metadata.yaml, plus a nested matter
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "metadata.yaml").write_text("case_number: '422500059892'\ndocument_type: evidence\n")
    (root / "evidence" / "letter.md").write_text("a received letter")
    (root / "evidence" / "deacon").mkdir()
    (root / "evidence" / "deacon" / "metadata.yaml").write_text("case_number: L00SS179\n")
    (root / "evidence" / "deacon" / "claim.pdf").write_bytes(b"%PDF-1.4 fake")
    # reports tree: no case_number, authored origin
    (root / "reports").mkdir()
    (root / "reports" / "r1.md").write_text("a report")
    # excluded tree + excluded file inside an included tree
    (root / "tmp").mkdir()
    (root / "tmp" / "junk.png").write_bytes(b"junk")
    (root / "evidence" / ".DS_Store").write_bytes(b"x")
    (root / "evidence" / "stale.pyc").write_bytes(b"x")


def test_allowlist_classification(tmp_path: Path) -> None:
    al = _allowlist(tmp_path)
    assert al.tree_for("evidence/letter.md").origin == "received"
    assert al.tree_for("reports/r1.md").origin == "authored"
    assert al.tree_for("tmp/junk.png") is None
    assert al.is_excluded_file("evidence/.DS_Store")
    assert al.is_excluded_file("evidence/x.pyc")
    assert not al.is_excluded_file("evidence/letter.md")


def test_populate_copies_only_allowlisted(tmp_path: Path) -> None:
    arch, raw = tmp_path / "arch", tmp_path / "raw"
    _make_archive(arch)
    al = _allowlist(tmp_path)
    report = populate_raw(arch, raw, al)

    assert (raw / "evidence" / "letter.md").is_file()
    assert (raw / "evidence" / "deacon" / "claim.pdf").is_file()
    assert (raw / "reports" / "r1.md").is_file()
    # excluded tree + excluded files never copied
    assert not (raw / "tmp").exists()
    assert not (raw / "evidence" / ".DS_Store").exists()
    assert not (raw / "evidence" / "stale.pyc").exists()
    # .gitattributes configures LFS
    assert "*.pdf filter=lfs" in (raw / ".gitattributes").read_text()
    # tree-root origin stamped without clobbering existing case_number
    ev_meta = yaml.safe_load((raw / "evidence" / "metadata.yaml").read_text())
    assert ev_meta["origin"] == "received" and ev_meta["role"] == "input"
    assert ev_meta["case_number"] == "422500059892"
    assert yaml.safe_load((raw / "reports" / "metadata.yaml").read_text())["origin"] == "authored"
    assert report.files_copied == 5  # 3 content + 2 metadata.yaml
    assert report.skipped_excluded == 2


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    arch, raw = tmp_path / "arch", tmp_path / "raw"
    _make_archive(arch)
    report = populate_raw(arch, raw, _allowlist(tmp_path), dry_run=True)
    assert report.files_copied == 5
    assert not raw.exists()


def test_manifest_sha256_and_matters(tmp_path: Path) -> None:
    arch, raw = tmp_path / "arch", tmp_path / "raw"
    _make_archive(arch)
    al = _allowlist(tmp_path)
    populate_raw(arch, raw, al)
    entries = build_manifest(raw, al, with_commit=False)
    by_path = {e.raw_path: e for e in entries}

    # SHA-256 is the real content hash (the Papra join key)
    letter = by_path["evidence/letter.md"]
    assert letter.sha256 == hashlib.sha256(b"a received letter").hexdigest()
    # matters resolved from the folder metadata.yaml chain
    assert letter.matters == ["422500059892"]
    assert letter.origin == "received"
    # nested folder overrides the matter
    assert by_path["evidence/deacon/claim.pdf"].matters == ["L00SS179"]
    # reports: authored origin, no matter
    assert by_path["reports/r1.md"].origin == "authored"
    assert by_path["reports/r1.md"].matters == []
    # metadata.yaml files themselves are not manifest entries
    assert not any(e.raw_path.endswith("metadata.yaml") for e in entries)


def test_manifest_raw_commit_populated_after_commit(tmp_path: Path) -> None:
    arch, raw = tmp_path / "arch", tmp_path / "raw"
    _make_archive(arch)
    al = _allowlist(tmp_path)
    populate_raw(arch, raw, al)
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(raw), *cmd], check=True, capture_output=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]}
    subprocess.run(["git", "-C", str(raw), "commit", "-qm", "seed"], check=True, capture_output=True, env=env)
    entries = build_manifest(raw, al, with_commit=True)
    assert all(len(e.raw_commit) == 40 for e in entries)


def test_write_manifest_keyed_by_sha(tmp_path: Path) -> None:
    arch, raw = tmp_path / "arch", tmp_path / "raw"
    _make_archive(arch)
    al = _allowlist(tmp_path)
    populate_raw(arch, raw, al)
    entries = build_manifest(raw, al, with_commit=False)
    dest = write_manifest(entries, tmp_path / "m.json")
    import json

    data = json.loads(dest.read_text())
    assert set(data) == {e.sha256 for e in entries}
    assert all("raw_path" in v and "matters" in v for v in data.values())


def test_gitattributes_covers_common_binaries() -> None:
    content = gitattributes_content()
    for ext in ("pdf", "png", "jpg", "mp4", "zip"):
        assert f"*.{ext} filter=lfs" in content
