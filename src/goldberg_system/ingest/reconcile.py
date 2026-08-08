"""Durable raw-vs-index RECONCILIATION — "what is committed to goldberg-raw but
still invisible to the index?".

The query side cannot tell a *never-ingested* document from a *non-existent* one: a
missing ``raw_sha256`` in Elasticsearch simply returns nothing, exactly as it would
for a path that was never committed. When the git publish-commit hook was found
unwired for ~2 days, 51 commits produced no pipeline events and were never ingested —
and nothing on the query side made those documents distinguishable from absence. This
module closes that detection gap.

:func:`reconcile_gap` walks goldberg-raw and lists every ingestable file whose CONTENT
(by ``raw_sha256``) is **not** represented in the index. It is a pure, side-effect-free
report (no manifest write, no git lookups) built on the *same* primitives the catch-up
uses — :func:`~goldberg_system.migrate.manifest.build_entry` for the walk-layer skip
and per-file sha, and :func:`~goldberg_system.ingest.catchup.entry_is_ingestable` for
the select-layer skip (restricted ``no_index`` subtrees + media). There is exactly ONE
skip list, so the reconciler can never disagree with catch-up about what "counts".

Both the ``legal_system reconcile`` CLI command and the MCP ``raw_index_gap`` tool call
into here, so there is a single implementation of "which raw files are not indexed".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from goldberg_system.exclusion_registry import ExclusionRegistry
from goldberg_system.ingest.catchup import entry_is_ingestable
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import _sha256, build_entry, entry_is_no_index


@dataclass(frozen=True)
class GapFile:
    """One file present in goldberg-raw whose content is absent from the index."""

    raw_path: str  # POSIX, relative to the goldberg-raw root
    raw_sha256: str  # content hash — the ADR 0006 join key
    tree: str  # top-level allowlisted tree (grouping key)


@dataclass
class GapReport:
    """The result of one raw-vs-index reconciliation pass.

    ``gap`` is the exists-in-raw-but-not-indexed set — the invisible documents. The
    scan is **never capped**: :func:`reconcile_gap` walks the whole raw tree, so a
    :attr:`complete` verdict means the corpus really is fully represented, not that the
    reconciler stopped looking (the false-comfort failure this exists to prevent).
    """

    scanned: int  # ingestable files walked (after BOTH skip layers)
    indexed_shas: int  # size of the already-indexed raw_sha256 set compared against
    gap: list[GapFile] = field(default_factory=list)
    skipped_no_index: int = 0  # restricted (no_index) files excluded from the scan
    skipped_media: int = 0  # media (_SKIP_EXT) files excluded from the scan
    skipped_excluded: int = 0  # deliberately-excluded (registry) files — never a gap
    indexed_truncated: bool = False  # the indexed-sha lookup itself hit its size cap

    @property
    def gap_count(self) -> int:
        return len(self.gap)

    @property
    def complete(self) -> bool:
        """True only when nothing is invisible — every scanned file is indexed."""
        return not self.gap

    @property
    def by_tree(self) -> dict[str, int]:
        """Gap counts grouped by top-level tree (sorted, for a stable report)."""
        counts: dict[str, int] = {}
        for gf in self.gap:
            counts[gf.tree] = counts.get(gf.tree, 0) + 1
        return dict(sorted(counts.items()))

    def summary_line(self) -> str:
        cap = " (indexed-set TRUNCATED)" if self.indexed_truncated else ""
        return (
            f"scanned={self.scanned} indexed={self.indexed_shas} "
            f"gap={self.gap_count} skipped_no_index={self.skipped_no_index} "
            f"skipped_media={self.skipped_media} "
            f"skipped_excluded={self.skipped_excluded} complete={self.complete}{cap}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "scanned": self.scanned,
            "indexed_shas": self.indexed_shas,
            "gap_count": self.gap_count,
            "skipped_no_index": self.skipped_no_index,
            "skipped_media": self.skipped_media,
            "skipped_excluded": self.skipped_excluded,
            "indexed_truncated": self.indexed_truncated,
            "by_tree": self.by_tree,
            "gap": [asdict(gf) for gf in self.gap],
        }


def reconcile_gap(
    *,
    raw_root: Path | str,
    allowlist: Allowlist,
    indexed_shas: Iterable[str],
    with_commit: bool = False,
    indexed_truncated: bool = False,
    registry: ExclusionRegistry | None = None,
) -> GapReport:
    """List every ingestable goldberg-raw file whose content is NOT in the index.

    Side-effect-free (report mode): it never writes the manifest and — with the default
    ``with_commit=False`` — runs no git subprocess, so it is safe to call read-only,
    repeatedly, from the CLI or the MCP server.

    The classification is the reason this module exists, so it must be exactly right in
    *both* degenerate directions — a bug that reports everything-missing or
    nothing-missing is the false comfort we are guarding against. It is pinned by a
    self-check unit test that seeds one known-indexed sha and one known-absent sha and
    asserts the gap contains exactly the absent one.

    ``indexed_shas`` is the set of ``raw_sha256`` values already present in the index
    (materialised by :func:`indexed_raw_shas`). Pass ``indexed_truncated=True`` if that
    lookup hit its own size cap, so the report can say so rather than quietly
    over-reporting the gap.
    """
    root = Path(raw_root)
    indexed = {s.lower() for s in indexed_shas}
    scanned = 0
    skipped_no_index = 0
    skipped_media = 0
    skipped_excluded = 0
    gap: list[GapFile] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # Walk-layer skip (.git / metadata.yaml / exclude_globs / outside any tree) is
        # applied by build_entry, which returns None for non-migratable files — the
        # SAME derivation refresh_provenance uses, so we never invent a second walk.
        entry = build_entry(root, rel, allowlist, with_commit=with_commit)
        if entry is None:
            continue
        record = asdict(entry)
        # Select-layer skip — the ONE shared predicate. Attribute the exclusion only
        # for the report; entry_is_ingestable remains the authoritative gate. A
        # deliberately-excluded (registry) file is NOT an invisible gap to heal —
        # attribute it first so a registered purge never shows up as "missing".
        if not entry_is_ingestable(record, registry):
            if registry is not None and registry.excludes_entry(record):
                skipped_excluded += 1
            elif entry_is_no_index(record):
                skipped_no_index += 1
            else:
                skipped_media += 1
            continue
        scanned += 1
        if entry.sha256.lower() not in indexed:
            gap.append(
                GapFile(
                    raw_path=entry.raw_path,
                    raw_sha256=entry.sha256,
                    tree=rel.parts[0] if rel.parts else "",
                )
            )

    return GapReport(
        scanned=scanned,
        indexed_shas=len(indexed),
        gap=gap,
        skipped_no_index=skipped_no_index,
        skipped_media=skipped_media,
        skipped_excluded=skipped_excluded,
        indexed_truncated=indexed_truncated,
    )


def indexed_raw_shas(
    client: Any, index: str, *, size: int = 2000
) -> tuple[set[str], bool]:
    """The set of ``raw_sha256`` values currently in ``index``, and a truncation flag.

    This is the "already indexed" resume set the catch-up computes, factored out so the
    reconcile CLI and MCP tool share one query rather than three copies. It paginates
    the whole index with the scan/scroll API (``size`` is the per-batch size), because a
    plain ``search`` is capped at ``index.max_result_window`` (default 10 000) and a
    corpus of any size would silently error or truncate — the exact false-``complete``
    this must never produce. ``truncated`` is therefore always False here (scan is
    exhaustive); the flag is retained for API compatibility and for a fake client used
    in tests that returns a single page.
    """
    try:
        from elasticsearch import helpers

        shas = {
            src["raw_sha256"].lower()
            for hit in helpers.scan(
                client,
                index=index,
                query={"query": {"exists": {"field": "raw_sha256"}}},
                _source=["raw_sha256"],
                preserve_order=False,
            )
            if (src := hit.get("_source", {})).get("raw_sha256")
        }
        return shas, False
    except (ImportError, AttributeError):
        # Fallback for a fake/minimal client (tests) that only implements .search():
        # a single bounded page, with the honest truncation flag preserved.
        resp = client.search(
            index=index,
            query={"exists": {"field": "raw_sha256"}},
            size=size,
            source_includes=["raw_sha256"],
        )
        hits = resp["hits"]["hits"]
        shas = {
            h["_source"]["raw_sha256"].lower()
            for h in hits
            if h["_source"].get("raw_sha256")
        }
        total = resp.get("hits", {}).get("total")
        total_val = total.get("value") if isinstance(total, dict) else total
        truncated = isinstance(total_val, int) and total_val > len(hits)
        return shas, truncated


def classify_path(
    *,
    raw_root: Path | str,
    raw_path: str,
    indexed_shas: Iterable[str],
    allowlist: Allowlist,
    with_commit: bool = False,
    registry: ExclusionRegistry | None = None,
) -> dict[str, Any]:
    """Classify a single ``raw_path``: is it in goldberg-raw, and is it in the index?

    Powers the MCP point query "does raw_path X exist in raw but not the index?".
    Returns citable structured data: the path, ``in_raw``/``in_index`` booleans, the
    content ``raw_sha256`` (when the file exists), whether it is ``ingestable`` (an
    excluded/media/restricted file legitimately never indexes), and the ``tree``.
    """
    root = Path(raw_root)
    rel = Path(raw_path)
    abs_path = root / rel
    indexed = {s.lower() for s in indexed_shas}

    in_raw = abs_path.is_file()
    raw_sha: str | None = None
    ingestable: bool | None = None
    note = ""

    if in_raw:
        raw_sha = _sha256(abs_path)  # the same content hash catch-up computes
        registry_hit = (
            registry.match(raw_path=rel.as_posix(), raw_sha256=raw_sha)
            if registry is not None
            else None
        )
        entry = build_entry(root, rel, allowlist, with_commit=with_commit)
        if registry_hit is not None:
            # Deliberately excluded (a purge under undertaking): expected to be absent,
            # and must NEVER be reported as an invisible gap to heal.
            ingestable = False
            note = f"deliberately excluded (registry): {registry_hit.reason}"
        elif entry is None:
            # Walk-layer excluded (folder metadata.yaml / exclude_globs / outside tree):
            # legitimately not evidence, so it is expected to be absent from the index.
            ingestable = False
            note = "excluded from ingestion (not evidence / not under an allowlisted tree)"
        elif not entry_is_ingestable(asdict(entry), registry):
            ingestable = False
            note = (
                "restricted (no_index)"
                if entry_is_no_index(asdict(entry))
                else "media (no OCR-able text)"
            )
        else:
            ingestable = True
    else:
        note = "not present in goldberg-raw"

    in_index = raw_sha is not None and raw_sha.lower() in indexed

    return {
        "raw_path": rel.as_posix(),
        "in_raw": in_raw,
        "in_index": in_index,
        "raw_sha256": raw_sha,
        "ingestable": ingestable,
        "tree": rel.parts[0] if rel.parts else "",
        "note": note,
        # The actionable case: committed to raw, ingestable, yet invisible.
        "invisible_gap": bool(in_raw and ingestable and not in_index),
    }
