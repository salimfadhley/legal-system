"""``no_index`` — permanently exclude a restricted corpus subtree from indexing.

A folder ``metadata.yaml`` may declare ``no_index: true`` (+ ``no_index_reason``) to
mark a legally/contractually restricted subtree (e.g. CPR 32.12 witness statements).
It flows through the same folder-metadata machinery as ``claim_source`` and is enforced
at both ends: ingest discovery skips it *quietly* (expected), and every sink refuses a
``no_index`` document *loudly* (defense in depth). These tests exercise resolution, the
recursive discovery skip + logging, the loud sink guards, and the ``deindex`` purge.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from goldberg_system.deindex import DeindexError, deindex
from goldberg_system.ingest.catchup import (
    count_pending,
    log_no_index_skips,
    select_pending,
)
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree
from goldberg_system.migrate.manifest import (
    Manifest,
    _resolve_chain,
    build_entry,
    entry_is_no_index,
    folder_base_fields,
)
from goldberg_system.sinks import (
    ElasticsearchIndexer,
    EnrichedDocument,
    ExtractedRepoWriter,
)


# --------------------------------------------------------------------------- #
# 1. no_index flows through resolution (build_entry, base_for_sha, folder_base) #
# --------------------------------------------------------------------------- #


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )


def _restricted_raw(tmp_path: Path) -> Path:
    """A raw tree with a restricted folder and a nested file two levels below it."""
    root = tmp_path / "raw"
    restricted = root / "evidence" / "restricted"
    (restricted / "sub" / "deep").mkdir(parents=True)
    (restricted / "metadata.yaml").write_text(
        "no_index: true\nno_index_reason: CPR 32.12 — restricted witness statements\n"
    )
    (restricted / "sub" / "deep" / "statement.txt").write_text("secret account")
    return root


def test_no_index_resolves_recursively_through_the_chain(tmp_path: Path) -> None:
    root = _restricted_raw(tmp_path)
    rel = Path("evidence/restricted/sub/deep/statement.txt")

    chain = _resolve_chain(rel, root)
    assert chain["no_index"] is True
    assert chain["no_index_reason"].startswith("CPR 32.12")

    # build_entry (manifest translation) carries the flag onto the entry
    entry = build_entry(root, rel, _allowlist())
    assert entry is not None
    assert entry.no_index is True
    assert entry.no_index_reason.startswith("CPR 32.12")

    # base_for_sha reconstructs DocumentMetadata carrying the flag
    manifest = Manifest({entry.sha256: entry.__dict__})
    base = manifest.base_for_sha(entry.sha256)
    assert base is not None
    assert base.no_index is True
    assert base.no_index_reason.startswith("CPR 32.12")

    # folder_base_fields (the re-enrich overlay path) also surfaces it
    fields = folder_base_fields(chain)
    assert fields["no_index"] is True
    assert fields["no_index_reason"].startswith("CPR 32.12")


def test_child_folder_may_reinclude_with_no_index_false(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    restricted = root / "evidence" / "restricted"
    (restricted / "public").mkdir(parents=True)
    (restricted / "metadata.yaml").write_text("no_index: true\nno_index_reason: r\n")
    # a child folder deliberately re-includes itself
    (restricted / "public" / "metadata.yaml").write_text("no_index: false\n")
    (restricted / "public" / "ok.txt").write_text("fine")
    (restricted / "hidden.txt").write_text("nope")

    hidden = build_entry(root, Path("evidence/restricted/hidden.txt"), _allowlist())
    public = build_entry(root, Path("evidence/restricted/public/ok.txt"), _allowlist())
    assert hidden is not None and hidden.no_index is True
    assert public is not None and public.no_index is False


# --------------------------------------------------------------------------- #
# 2. ingest discovery skips no_index recursively and logs (the quiet path)     #
# --------------------------------------------------------------------------- #


def _manifest_with_restricted() -> Manifest:
    return Manifest(
        {
            "aaa": {"raw_path": "evidence/normal/a.txt", "size": 1},
            "bbb": {
                "raw_path": "evidence/restricted/sub/deep/s.txt",
                "size": 1,
                "no_index": True,
                "no_index_reason": "CPR 32.12",
            },
            "ccc": {"raw_path": "evidence/normal/c.txt", "size": 1},
        }
    )


def test_selection_excludes_no_index_entries() -> None:
    manifest = _manifest_with_restricted()
    pending = select_pending(manifest, skip=set(), batch=50)
    paths = {e.get("raw_path") for _sha, e in pending}
    assert paths == {"evidence/normal/a.txt", "evidence/normal/c.txt"}
    # and the unbounded count matches (restricted entry not counted as pending)
    assert count_pending(manifest, skip=set()) == 2
    assert entry_is_no_index({"no_index": True}) is True
    assert entry_is_no_index({}) is False


def test_no_index_skip_is_logged_not_silent(caplog: Any) -> None:
    manifest = _manifest_with_restricted()
    with caplog.at_level(logging.INFO, logger="goldberg.ingest"):
        count = log_no_index_skips(manifest)
    assert count == 1
    assert any(
        "no_index" in r.message and "evidence/restricted/sub/deep/s.txt" in r.message
        for r in caplog.records
    )


# --------------------------------------------------------------------------- #
# 3. the sinks refuse a no_index doc loudly (defense in depth)                 #
# --------------------------------------------------------------------------- #


def _no_index_doc() -> EnrichedDocument:
    return EnrichedDocument(
        doc_id="gb_restricted",
        raw_path="evidence/restricted/s.txt",
        raw_commit="",
        markdown="restricted body",
        metadata=DocumentMetadata(
            raw_path="evidence/restricted/s.txt",
            no_index=True,
            no_index_reason="CPR 32.12",
        ),
    )


class _FakeES:
    def __init__(self) -> None:
        self.indexed: list[dict[str, Any]] = []

    def index(self, index: str, id: str, document: dict[str, Any]) -> None:
        self.indexed.append({"id": id})


def test_es_sink_refuses_no_index_document() -> None:
    fake = _FakeES()
    indexer = ElasticsearchIndexer(fake, "goldberg_documents_test")
    result = indexer.write(_no_index_doc())
    assert result.ok is False
    assert "refusing to index no_index document" in (result.detail or "")
    assert "CPR 32.12" in (result.detail or "")
    assert fake.indexed == []  # never written


def test_extracted_writer_refuses_no_index_document(tmp_path: Path) -> None:
    writer = ExtractedRepoWriter(tmp_path / "extracted")
    result = writer.write(_no_index_doc())
    assert result.ok is False
    assert "refusing to write no_index document" in (result.detail or "")
    # nothing written to the derived store
    assert list((tmp_path / "extracted").rglob("*.md")) == []


# --------------------------------------------------------------------------- #
# 5/6. deindex — dry-run counts, real run deletes by prefix, short-prefix guard #
# --------------------------------------------------------------------------- #


class _FakeDeindexES:
    """Counts/deletes by prefix over an in-memory raw_path list."""

    def __init__(self, raw_paths: list[str]) -> None:
        self.raw_paths = list(raw_paths)
        self.delete_calls: list[dict[str, Any]] = []

    def _match(self, query: dict[str, Any]) -> list[str]:
        clauses = query["bool"]["should"]
        prefixes = [c["prefix"]["raw_path"] for c in clauses]
        return [rp for rp in self.raw_paths if any(rp.startswith(p) for p in prefixes)]

    def count(self, index: str, query: dict[str, Any]) -> dict[str, Any]:
        return {"count": len(self._match(query))}

    def delete_by_query(
        self, index: str, query: dict[str, Any], refresh: bool = False
    ) -> dict[str, Any]:
        hits = self._match(query)
        self.delete_calls.append({"index": index, "n": len(hits)})
        self.raw_paths = [rp for rp in self.raw_paths if rp not in hits]
        return {"deleted": len(hits)}


def _extracted_tree(tmp_path: Path) -> Path:
    root = tmp_path / "extracted"
    (root / "evidence" / "restricted").mkdir(parents=True)
    (root / "evidence" / "normal").mkdir(parents=True)
    (root / "evidence" / "restricted" / "s.txt.md").write_text("restricted")
    (root / "evidence" / "normal" / "a.txt.md").write_text("normal")
    return root


def test_deindex_dry_run_counts_but_deletes_nothing(tmp_path: Path) -> None:
    es = _FakeDeindexES(["evidence/restricted/s.txt", "evidence/normal/a.txt"])
    extracted = _extracted_tree(tmp_path)

    result = deindex(
        es, "idx", ["evidence/restricted"],
        extracted_root=extracted, dry_run=True,
    )
    assert result.es_matched == 1
    assert result.es_deleted == 0
    assert es.delete_calls == []  # dry-run never calls delete_by_query
    assert result.files_matched == 1
    assert result.files_removed == 0
    # the extracted file is still on disk
    assert (extracted / "evidence" / "restricted" / "s.txt.md").exists()


def test_deindex_deletes_by_prefix(tmp_path: Path) -> None:
    es = _FakeDeindexES(["evidence/restricted/s.txt", "evidence/normal/a.txt"])
    extracted = _extracted_tree(tmp_path)

    result = deindex(
        es, "idx", ["evidence/restricted"], extracted_root=extracted,
    )
    assert result.es_deleted == 1
    assert es.raw_paths == ["evidence/normal/a.txt"]  # only the restricted doc purged
    assert result.files_removed == 1
    assert not (extracted / "evidence" / "restricted" / "s.txt.md").exists()
    assert (extracted / "evidence" / "normal" / "a.txt.md").exists()  # untouched


def test_deindex_refuses_short_prefix_unless_forced() -> None:
    es = _FakeDeindexES(["ab/secret.txt"])
    for bad in ("/", "", "ab"):
        try:
            deindex(es, "idx", [bad])
        except DeindexError:
            pass
        else:  # pragma: no cover - the guard must fire
            raise AssertionError(f"expected DeindexError for prefix {bad!r}")
    # --force lets a short (but non-root) prefix through
    result = deindex(es, "idx", ["ab"], force=True)
    assert result.es_deleted == 1
