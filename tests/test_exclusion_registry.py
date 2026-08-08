"""Tests for the durable exclusion registry (the CPR-32.12 class-fault memory).

The registry is the record that a purge was DELIBERATE, so the catch-up/reconcile
healer can never silently re-add it. These tests pin: the append-only JSONL format,
O(1) lookup by BOTH raw_path and raw_sha256, the fail-safe literal-prefix match that
mirrors ``deindex``, and that ``deindex`` records every purged prefix.
"""

from __future__ import annotations

import json
from pathlib import Path

from goldberg_system.deindex import deindex
from goldberg_system.exclusion_registry import (
    ExclusionEntry,
    ExclusionRegistry,
    append_exclusion,
    record_exclusion,
)


def _entry(raw_path: str, *, sha: str | None = None, reason: str = "CPR 32.12") -> ExclusionEntry:
    return ExclusionEntry(
        raw_path=raw_path,
        reason=reason,
        timestamp="2026-08-08T00:00:00Z",
        source="deindex",
        raw_sha256=sha,
    )


# --------------------------------------------------------------------------- #
# format: append-only, human-readable JSONL
# --------------------------------------------------------------------------- #
def test_append_writes_one_jsonl_line_per_entry(tmp_path: Path) -> None:
    reg = tmp_path / "exclusion-registry.jsonl"
    append_exclusion(reg, _entry("evidence/sealed/a.pdf", sha="ABC123"))
    append_exclusion(reg, _entry("evidence/sealed/b.pdf"))

    lines = [ln for ln in reg.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["raw_path"] == "evidence/sealed/a.pdf"
    assert first["raw_sha256"] == "abc123"  # normalised to lowercase
    assert first["reason"] == "CPR 32.12"
    assert first["source"] == "deindex"


def test_load_roundtrips_and_ignores_comments_and_blanks(tmp_path: Path) -> None:
    reg = tmp_path / "r.jsonl"
    reg.write_text(
        "# a human-readable header comment\n"
        "\n"
        + _entry("evidence/x.pdf").to_json()
        + "\n"
    )
    loaded = ExclusionRegistry.load(reg)
    assert len(loaded) == 1
    assert loaded.entries[0].raw_path == "evidence/x.pdf"


def test_load_skips_malformed_line_without_crashing(tmp_path: Path) -> None:
    reg = tmp_path / "r.jsonl"
    reg.write_text("{not json\n" + _entry("evidence/ok.pdf").to_json() + "\n")
    loaded = ExclusionRegistry.load(reg)
    # the corrupt line is skipped; the good one survives — a bad registry never
    # takes the pipeline down.
    assert {e.raw_path for e in loaded.entries} == {"evidence/ok.pdf"}


def test_missing_file_is_empty_registry(tmp_path: Path) -> None:
    loaded = ExclusionRegistry.load(tmp_path / "does-not-exist.jsonl")
    assert len(loaded) == 0
    assert loaded.excludes(raw_path="evidence/anything.pdf") is False


# --------------------------------------------------------------------------- #
# lookup by raw_path AND sha256
# --------------------------------------------------------------------------- #
def test_lookup_by_exact_path_and_by_sha() -> None:
    reg = ExclusionRegistry([_entry("evidence/sealed/a.pdf", sha="DEADBEEF")])
    assert reg.excludes(raw_path="evidence/sealed/a.pdf") is True
    assert reg.excludes(raw_sha256="deadbeef") is True
    assert reg.excludes(raw_sha256="DEADBEEF") is True  # case-insensitive
    assert reg.excludes(raw_path="evidence/other.pdf") is False


def test_prefix_match_mirrors_deindex_and_is_fail_safe() -> None:
    # A purged folder prefix excludes everything committed beneath it (like deindex's
    # literal raw_path prefix). Over-matching only ever EXCLUDES more — never re-adds.
    reg = ExclusionRegistry([_entry("evidence/sealed")])
    assert reg.excludes(raw_path="evidence/sealed/witness1.pdf") is True
    assert reg.excludes(raw_path="evidence/sealed/deep/sub/x.pdf") is True
    assert reg.excludes(raw_path="evidence/public/ok.pdf") is False


def test_match_entry_reads_manifest_and_document_shapes() -> None:
    reg = ExclusionRegistry([_entry("evidence/sealed/a.pdf", sha="abc")])
    # manifest entry uses "sha256"; an ES/document dict uses "raw_sha256"
    assert reg.match_entry({"raw_path": "evidence/sealed/a.pdf"}) is not None
    assert reg.match_entry({"raw_path": "x", "sha256": "abc"}) is not None
    assert reg.match_entry({"raw_path": "x", "raw_sha256": "abc"}) is not None
    assert reg.match_entry({"raw_path": "x", "sha256": "zzz"}) is None


def test_record_exclusion_requires_a_reason(tmp_path: Path) -> None:
    try:
        record_exclusion("evidence/x", reason="  ", source="manual", path=tmp_path / "r.jsonl")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for an empty reason")


# --------------------------------------------------------------------------- #
# deindex records every purged prefix in the registry
# --------------------------------------------------------------------------- #
class _FakeDeindexES:
    def __init__(self, raw_paths: list[str]) -> None:
        self.raw_paths = list(raw_paths)

    def _match(self, query: dict) -> list[str]:
        prefixes = [c["prefix"]["raw_path"] for c in query["bool"]["should"]]
        return [rp for rp in self.raw_paths if any(rp.startswith(p) for p in prefixes)]

    def count(self, index: str, query: dict) -> dict:
        return {"count": len(self._match(query))}

    def delete_by_query(self, index: str, query: dict, refresh: bool = False) -> dict:
        hits = self._match(query)
        self.raw_paths = [rp for rp in self.raw_paths if rp not in hits]
        return {"deleted": len(hits)}


def test_deindex_appends_to_registry_with_reason(tmp_path: Path) -> None:
    reg = tmp_path / "exclusion-registry.jsonl"
    es = _FakeDeindexES(["evidence/sealed/s.pdf", "evidence/normal/a.pdf"])

    result = deindex(
        es, "idx", ["evidence/sealed"],
        reason="CPR 32.12 — restricted witness statements",
        registry_path=reg,
    )

    assert result.registered == ["evidence/sealed"]
    loaded = ExclusionRegistry.load(reg)
    assert loaded.excludes(raw_path="evidence/sealed/s.pdf") is True
    entry = loaded.entries[0]
    assert entry.source == "deindex"
    assert entry.reason.startswith("CPR 32.12")


def test_deindex_dry_run_records_nothing(tmp_path: Path) -> None:
    reg = tmp_path / "exclusion-registry.jsonl"
    es = _FakeDeindexES(["evidence/sealed/s.pdf"])

    result = deindex(
        es, "idx", ["evidence/sealed"], reason="r", registry_path=reg, dry_run=True
    )

    assert result.registered == []
    assert not reg.exists()  # a preview must not mutate the durable record
