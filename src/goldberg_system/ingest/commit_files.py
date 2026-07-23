"""Resolve the allowlisted, ingestable files changed by a single git commit (WP03).

A raw-commit event carries only the commit sha; the processor must turn that into
the concrete list of evidence files to (re)ingest. :func:`changed_files` asks git for
the commit's name-status, keeps Added/Modified paths (drops deletions), and filters
the result through the existing :class:`Allowlist` and the reingest ``_SKIP_EXT`` media
set — so the processor never wastes an extract/enrich on a deletion, a media file, a
folder ``metadata.yaml``, or a path outside an allowlisted tree.

git is the single source of truth here; this module is the only new place that shells
out to it (reusing ``migrate``'s primitives for everything else).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.reingest import _SKIP_EXT


class GitResolutionError(RuntimeError):
    """A git command needed to resolve a commit's changed files failed.

    Raised on a non-zero git exit or a subprocess/OS error. It signals a *transient*
    inability to resolve the commit — an unknown sha not yet fetched, a partial/locked
    repo, a ``.git`` read glitch. The processor maps it to a **NAK** (redeliver), so
    the commit is retried rather than acked on an empty result. This is what keeps a
    merge event or a ``.git`` hiccup from silently losing a legal document: "could not
    resolve" must never be indistinguishable from "resolved, zero files" (which acks).
    """


def _run_git(root: Path, *args: str) -> str:
    """Run git under ``root`` and return stdout; raise on failure.

    A non-zero exit (or a subprocess/OS error) is **not** swallowed — it raises
    :class:`GitResolutionError` so the caller can distinguish "resolved, zero files"
    (empty stdout, exit 0 → ackable) from "could not resolve" (→ nak). Swallowing the
    exit code (the previous behaviour) let a failed ``git`` masquerade as an empty
    commit and get acked, silently dropping the commit's documents.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise GitResolutionError(f"git {' '.join(args)}: {exc}") from exc
    if res.returncode != 0:
        raise GitResolutionError(
            f"git {' '.join(args)} exited {res.returncode}: {res.stderr.strip()}"
        )
    return res.stdout


def _name_status(root: Path, sha: str) -> list[tuple[str, str]]:
    """Return ``(status, path)`` pairs for the files a commit introduces.

    A single ``git diff-tree`` invocation handles every commit shape correctly:

    * ``-m`` expands a **merge** into a per-parent diff. A clean merge emits NO rows
      under the default combined diff (``diff-tree``/``git show`` without ``-m``),
      which previously made a merge that introduced ``evidence/new.pdf`` look exactly
      like an empty commit — and get acked → silently dropped. ``-m`` surfaces the
      files the merge brings in from either side (deduped in :func:`changed_files`).
      (This matters for the WP04 post-merge trigger, which fires on merge commits.)
    * ``--root`` diffs a **root** commit (no parent) against the empty tree, so the
      first commit's files resolve — replacing the old ``git show`` fallback, which is
      what forced the "empty stdout" branch that hid failures.

    An unknown/invalid sha, or any git error, raises :class:`GitResolutionError`
    (via :func:`_run_git`) rather than returning ``[]`` — so the processor NAKs.
    """
    out = _run_git(
        root,
        "diff-tree",
        "-m",
        "--root",
        "--no-commit-id",
        "--name-status",
        "-r",
        sha,
    )
    return _parse_name_status(out)


def _parse_name_status(text: str) -> list[tuple[str, str]]:
    """Parse tab-delimited ``git --name-status`` output into ``(status, path)``.

    Renames/copies render as ``R100<TAB>old<TAB>new`` — we take the *new* path (the
    last field). The status is normalised to its leading letter (``R100`` → ``R``).
    """
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip("\n")
        if not line or "\t" not in line:
            continue
        fields = line.split("\t")
        status = fields[0][:1].upper()
        if status not in {"A", "M", "D", "R", "C", "T"}:
            continue
        path = fields[-1]  # new path for renames/copies; the path otherwise
        pairs.append((status, path))
    return pairs


def _is_ingestable(rel: Path, allowlist: Allowlist) -> bool:
    """True when ``rel`` is an allowlisted, non-media evidence file (not metadata)."""
    if rel.name == "metadata.yaml":
        return False
    if allowlist.is_excluded_file(rel):
        return False
    if allowlist.tree_for(rel) is None:
        return False
    if rel.suffix.lower() in _SKIP_EXT:
        return False
    return True


def changed_files(
    raw_root: Path | str, sha: str, allowlist: Allowlist
) -> list[str]:
    """The allowlisted, ingestable POSIX paths Added/Modified by commit ``sha``.

    Deletions are dropped (nothing to ingest), as are media files (``_SKIP_EXT``),
    folder ``metadata.yaml`` files, ``exclude_globs`` matches, and paths outside any
    allowlisted tree. Returned paths are relative to ``raw_root`` (POSIX), de-duplicated
    while preserving first-seen order.

    An **empty list means "resolved, zero allowlisted files"** (ackable). If the
    commit cannot be resolved at all (unknown sha, git/repo error) this raises
    :class:`GitResolutionError` — the caller (the processor) must NAK, never ack, so
    no commit's documents are silently dropped.
    """
    root = Path(raw_root)
    seen: set[str] = set()
    out: list[str] = []
    for status, path in _name_status(root, sha):
        if status == "D":  # deletion — nothing to (re)ingest
            continue
        rel = Path(path)
        if not _is_ingestable(rel, allowlist):
            continue
        posix = rel.as_posix()
        if posix not in seen:
            seen.add(posix)
            out.append(posix)
    return out
