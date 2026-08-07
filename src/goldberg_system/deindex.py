"""Purge already-indexed content by ``raw_path`` prefix (the ``goldberg deindex`` tool).

Marking a folder ``no_index`` in its ``metadata.yaml`` stops *future* ingestion, but
material that was indexed *before* the restriction was applied is still searchable.
:func:`deindex` is the retroactive purge casework needs: it deletes every Elasticsearch
document whose ``raw_path`` starts with one of the given prefixes and, optionally,
removes the mirrored files from the goldberg-extracted derived store.

It is deliberately kept as a small, side-effecting core (a fake ES client drives the
tests). ``git rm`` is out of scope — files are removed from the working tree and the
operator commits the deletion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A prefix this short would purge (almost) the whole corpus by accident — refuse it
# unless the operator passes ``force``. "/" or empty is always refused.
MIN_PREFIX_LEN = 3


class DeindexError(ValueError):
    """A deindex request is unsafe or malformed (e.g. a corpus-wide prefix)."""


@dataclass
class DeindexResult:
    """What a :func:`deindex` run matched and (unless dry-run) removed."""

    prefixes: list[str]
    dry_run: bool
    es_matched: int = 0  # ES docs whose raw_path matched a prefix
    es_deleted: int = 0  # ES docs actually deleted (0 on dry-run)
    files_matched: int = 0  # extracted files whose path matched a prefix
    files_removed: int = 0  # extracted files actually unlinked (0 on dry-run)
    removed_paths: list[str] = field(default_factory=list)  # matched extracted raw_paths


def _validate_prefixes(prefixes: list[str], *, force: bool) -> list[str]:
    """Reject empty / root / dangerously-short prefixes; return the cleaned list."""
    if not prefixes:
        raise DeindexError("no --path prefix given; refusing to purge nothing")
    cleaned: list[str] = []
    for raw in prefixes:
        p = raw.strip()
        if not p or p == "/":
            raise DeindexError(
                f"refusing empty/root prefix {raw!r} — it would match the whole corpus"
            )
        if len(p) < MIN_PREFIX_LEN and not force:
            raise DeindexError(
                f"refusing short prefix {p!r} (< {MIN_PREFIX_LEN} chars); "
                f"pass force=True/--force to override"
            )
        cleaned.append(p)
    return cleaned


def _prefix_query(prefixes: list[str]) -> dict[str, Any]:
    """A bool-should of ``prefix`` clauses over the ``raw_path`` keyword field."""
    return {
        "bool": {
            "should": [{"prefix": {"raw_path": p}} for p in prefixes],
            "minimum_should_match": 1,
        }
    }


def _matching_extracted_files(
    extracted_root: Path, prefixes: list[str]
) -> list[tuple[Path, str]]:
    """`(file, raw_path)` pairs under ``extracted_root`` whose raw_path hits a prefix.

    The derived store mirrors each raw file at ``<raw_path>.md``, so we strip the
    trailing ``.md`` before comparing against the raw_path prefixes.
    """
    matches: list[tuple[Path, str]] = []
    for md in sorted(extracted_root.rglob("*.md")):
        rel = md.relative_to(extracted_root).as_posix()
        raw_rel = rel[:-3] if rel.endswith(".md") else rel
        if any(raw_rel.startswith(p) for p in prefixes):
            matches.append((md, raw_rel))
    return matches


def deindex(
    client: Any,
    index: str,
    prefixes: list[str],
    *,
    extracted_root: Path | str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> DeindexResult:
    """Purge indexed content whose ``raw_path`` matches any of ``prefixes``.

    (a) counts (dry-run) or ``delete_by_query``-deletes matching Elasticsearch docs;
    (b) if ``extracted_root`` is given, removes the mirrored derived-store files.

    Guards against an empty/root/too-short prefix (:class:`DeindexError`) so a stray
    ``deindex --path /`` cannot wipe the corpus.
    """
    cleaned = _validate_prefixes(prefixes, force=force)
    query = _prefix_query(cleaned)

    matched = int(client.count(index=index, query=query).get("count", 0))
    deleted = 0
    if not dry_run:
        resp = client.delete_by_query(index=index, query=query, refresh=True)
        deleted = int((resp or {}).get("deleted", 0))

    files_matched = 0
    files_removed = 0
    removed_paths: list[str] = []
    if extracted_root is not None:
        root = Path(extracted_root)
        for md, raw_rel in _matching_extracted_files(root, cleaned):
            files_matched += 1
            removed_paths.append(raw_rel)
            if not dry_run:
                md.unlink()
                files_removed += 1

    return DeindexResult(
        prefixes=cleaned,
        dry_run=dry_run,
        es_matched=matched,
        es_deleted=deleted,
        files_matched=files_matched,
        files_removed=files_removed,
        removed_paths=removed_paths,
    )
