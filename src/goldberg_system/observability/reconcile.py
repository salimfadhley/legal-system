"""Reconciliation — the completeness check (ADR 0008 §4).

Answers *"is there something that did not ingest?"* by joining the **expected** set
(the goldberg-raw provenance manifest) against the **actual** set (the documents in
the ES ``goldberg_documents`` index). The join key is ``raw_path`` — the manifest's
authoritative path, which the manifest-aware pipeline stamps onto each indexed doc.

Reports:
- **missing**  — expected (in the manifest) but absent from the index → the drops.
- **extra**    — indexed under a ``raw_path`` the manifest doesn't know (e.g. a doc
                 indexed without provenance, so its ``raw_path`` is a Papra filename).
- **matched**  — present in both.

A missing document is an invisible hole in the corpus; this is how we find them.

**Orphans (the *third* axis — added after the deletion probe of 2026-07-25).** The
manifest-vs-index join above is blind to a document *removed from goldberg-raw*: a
deletion commit resolves to zero ingestable files and is acked as a no-op
(``commit_files.changed_files`` drops ``D`` status), and neither the manifest nor the
index is ever pruned. The stale manifest entry then *masks* the orphan — it still
"matches" the index, so ``reconcile`` reports the corpus COMPLETE while an ES document
survives whose source file no longer exists. :func:`find_orphans` closes that hole by
checking the *third* source of truth the join ignores — **the raw tree on disk** —
and reporting every manifest ``raw_path`` whose file is gone (and whether an ES
document still exists to be expunged).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReconciliationReport:
    expected_count: int
    actual_count: int
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # expected raw_paths not indexed
    extra: list[str] = field(default_factory=list)  # indexed raw_paths not expected
    missing_by_matter: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass
class OrphanEntry:
    """A manifest entry whose source file no longer exists in goldberg-raw."""

    raw_path: str
    sha256: str
    doc_id: str | None  # ES _id if a document still exists to expunge, else None

    @property
    def indexed(self) -> bool:
        """True when an ES document still survives for this deleted source file."""
        return self.doc_id is not None


@dataclass
class OrphanReport:
    """Manifest entries whose raw file is gone — the deletions the join can't see."""

    checked: int  # manifest entries examined
    orphans: list[OrphanEntry] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.orphans

    @property
    def indexed_orphans(self) -> list[OrphanEntry]:
        """Orphans that still have an ES document — the expungeable ones."""
        return [o for o in self.orphans if o.indexed]


def find_orphans(
    expected: dict[str, dict],
    raw_root: Path | str,
    indexed: dict[str, str],
) -> OrphanReport:
    """Manifest entries whose ``raw_path`` no longer exists on disk in goldberg-raw.

    The completeness join (:func:`reconcile`) compares the manifest against the index;
    a document deleted from goldberg-raw survives in *both*, so it stays invisible. This
    checks each manifest ``raw_path`` against the **actual raw tree** (a cheap ``stat``
    per entry) and reports every one whose file is gone — annotating each with the ES
    ``doc_id`` (from ``indexed``) when a document still exists to be expunged.
    """
    root = Path(raw_root)
    orphans: list[OrphanEntry] = []
    for rp, entry in sorted(expected.items()):
        if (root / rp).is_file():
            continue
        orphans.append(
            OrphanEntry(
                raw_path=rp,
                sha256=entry.get("sha256", ""),
                doc_id=indexed.get(rp),
            )
        )
    return OrphanReport(checked=len(expected), orphans=orphans)


def all_indexed_raw_paths(
    client: Any, index: str, *, page: int = 5000
) -> dict[str, str]:
    """Return ``{raw_path: doc_id}`` for every document in the index (scan)."""
    out: dict[str, str] = {}
    resp = client.search(
        index=index,
        query={"match_all": {}},
        size=page,
        source_includes=["raw_path", "doc_id"],
    )
    for hit in resp["hits"]["hits"]:
        src = hit.get("_source", {})
        rp = src.get("raw_path")
        if rp:
            out[rp] = src.get("doc_id", hit.get("_id", ""))
    return out


def reconcile(
    expected: dict[str, dict], indexed: dict[str, str]
) -> ReconciliationReport:
    """Join ``expected`` (raw_path → manifest entry) against ``indexed`` (raw_path → doc_id)."""
    expected_paths = set(expected)
    indexed_paths = set(indexed)
    missing = sorted(expected_paths - indexed_paths)
    extra = sorted(indexed_paths - expected_paths)
    matched = sorted(expected_paths & indexed_paths)

    by_matter: dict[str, int] = {}
    for rp in missing:
        for m in expected[rp].get("matters") or ["(none)"]:
            by_matter[m] = by_matter.get(m, 0) + 1

    return ReconciliationReport(
        expected_count=len(expected_paths),
        actual_count=len(indexed_paths),
        matched=matched,
        missing=missing,
        extra=extra,
        missing_by_matter=dict(sorted(by_matter.items(), key=lambda kv: -kv[1])),
    )


def expected_from_manifest(manifest_path: Path | str) -> dict[str, dict]:
    """Load ``{raw_path: entry}`` from a provenance manifest JSON (keyed by sha256)."""
    import json

    data = json.loads(Path(manifest_path).read_text())
    expected: dict[str, dict] = {}
    for entry in data.values():
        rp = entry.get("raw_path")
        if rp:
            expected[rp] = entry
    return expected


class Reconciler:
    """Reconcile the manifest (expected) against the ES index (actual)."""

    def __init__(self, client: Any, index: str) -> None:
        self.client = client
        self.index = index

    def run(self, manifest_path: Path | str) -> ReconciliationReport:
        expected = expected_from_manifest(manifest_path)
        indexed = all_indexed_raw_paths(self.client, self.index)
        return reconcile(expected, indexed)

    def orphans(
        self, manifest_path: Path | str, raw_root: Path | str
    ) -> OrphanReport:
        """Report manifest entries whose source file is gone from ``raw_root``.

        The completeness axis the manifest-vs-index join cannot see: a document deleted
        from goldberg-raw survives in both the manifest and the index. Cross-references
        the index so each orphan carries the ES ``doc_id`` that would need expunging.
        """
        expected = expected_from_manifest(manifest_path)
        indexed = all_indexed_raw_paths(self.client, self.index)
        return find_orphans(expected, raw_root, indexed)
