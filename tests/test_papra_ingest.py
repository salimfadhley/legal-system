"""Tests for the Papra ingest-folder writer."""

from __future__ import annotations

from pathlib import Path

from goldberg_system.papra import IngestFolder

ORG = "org_qcs9nhj2xbf88znpppy7lnba"


def test_org_dir_is_per_organisation(tmp_path: Path) -> None:
    folder = IngestFolder(tmp_path, ORG)
    assert folder.org_dir == tmp_path / ORG


def test_drop_bytes_writes_into_org_subfolder(tmp_path: Path) -> None:
    folder = IngestFolder(tmp_path, ORG)
    dest = folder.drop_bytes(b"hello", "a.txt")
    assert dest == tmp_path / ORG / "a.txt"
    assert dest.read_bytes() == b"hello"


def test_drop_file_copies_source(tmp_path: Path) -> None:
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF fake")
    folder = IngestFolder(tmp_path / "ingest", ORG)
    dest = folder.drop_file(src)
    assert dest == tmp_path / "ingest" / ORG / "src.pdf"
    assert dest.read_bytes() == b"%PDF fake"
    assert src.exists()  # original untouched


def test_drop_file_with_rename(tmp_path: Path) -> None:
    src = tmp_path / "orig.eml"
    src.write_bytes(b"From: x")
    folder = IngestFolder(tmp_path / "ingest", ORG)
    dest = folder.drop_file(src, name="renamed.eml")
    assert dest.name == "renamed.eml"
