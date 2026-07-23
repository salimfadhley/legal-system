"""Unit tests for ``ingest.commit_files.changed_files`` (WP03, FR-003/FR-004).

A temp git repo stands in for goldberg-raw: we commit files and assert that
``changed_files`` resolves the allowlisted, non-media Added/Modified paths of a
commit and drops everything else (deletions, excluded globs, media, non-allowlisted
trees, folder ``metadata.yaml``). No live NATS/ES — pure git + filesystem.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from goldberg_system.ingest.commit_files import GitResolutionError, changed_files
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": os.environ["PATH"],
}


def _allowlist() -> Allowlist:
    return Allowlist(
        included={
            "evidence": IncludedTree("evidence", "received"),
            "reports": IncludedTree("reports", "authored"),
        },
        excluded={},
        exclude_globs=("*.log", "evidence/secret/*"),
    )


def _git(raw: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(raw), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    return res.stdout.strip()


def _init(raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(raw), "init", "-q"], check=True, capture_output=True
    )


def _commit(raw: Path, message: str) -> str:
    _git(raw, "add", "-A")
    _git(raw, "commit", "-qm", message)
    return _git(raw, "rev-parse", "HEAD")


def test_root_commit_resolves_allowlisted_files(tmp_path: Path) -> None:
    # A root commit has no parent — exercises the ``git show`` fallback path.
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "letter.txt").write_text("a letter")
    sha = _commit(raw, "root")

    assert changed_files(raw, sha, _allowlist()) == ["evidence/letter.txt"]


def test_added_and_modified_kept_deletions_dropped(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "keep.txt").write_text("keep")
    (raw / "evidence" / "gone.txt").write_text("to be deleted")
    _commit(raw, "seed")

    # second commit: add one, modify one, delete one
    (raw / "evidence" / "new.txt").write_text("added")
    (raw / "evidence" / "keep.txt").write_text("modified")
    (raw / "evidence" / "gone.txt").unlink()
    sha = _commit(raw, "changes")

    resolved = changed_files(raw, sha, _allowlist())
    assert "evidence/new.txt" in resolved  # Added
    assert "evidence/keep.txt" in resolved  # Modified
    assert "evidence/gone.txt" not in resolved  # Deleted → dropped


def test_excluded_media_and_out_of_tree_are_filtered(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "secret").mkdir(parents=True)
    (raw / "junk").mkdir(parents=True)
    # allowlisted + wanted
    (raw / "evidence" / "doc.txt").write_text("wanted")
    # media — filtered by _SKIP_EXT
    (raw / "evidence" / "clip.mp4").write_bytes(b"video")
    # exclude_globs match
    (raw / "evidence" / "trace.log").write_text("log")
    (raw / "evidence" / "secret" / "x.txt").write_text("secret")
    # folder metadata.yaml — never an evidence file
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    # outside any allowlisted tree
    (raw / "junk" / "build.txt").write_text("junk")
    sha = _commit(raw, "mixed")

    resolved = changed_files(raw, sha, _allowlist())
    assert resolved == ["evidence/doc.txt"]


# --------------------------------------------------------------------------- #
# FIX 1 (cycle 2): could-not-resolve MUST raise (→ processor nak), never look
# like an empty (ackable) result. Distinguish "resolved, zero files" from
# "could not resolve"; resolve merge commits so their introduced files ingest.
# --------------------------------------------------------------------------- #
def test_unknown_sha_raises_git_resolution_error(tmp_path: Path) -> None:
    # An unknown/invalid sha must NOT resolve to [] (which would ack + drop) — it
    # must raise so the processor naks and the commit is retried.
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "seed.txt").write_text("seed")
    _commit(raw, "seed")

    with pytest.raises(GitResolutionError):
        changed_files(raw, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", _allowlist())


def test_git_failure_raises_git_resolution_error(tmp_path: Path) -> None:
    # A non-git directory makes git exit non-zero — a transient inability to
    # resolve, which must raise (nak), not silently return [].
    not_a_repo = tmp_path / "raw"
    not_a_repo.mkdir(parents=True)

    with pytest.raises(GitResolutionError):
        changed_files(not_a_repo, "HEAD", _allowlist())


def test_merge_commit_introduced_file_is_resolved(tmp_path: Path) -> None:
    # A NON-fast-forward merge that introduces an allowlisted file must resolve
    # that file. A clean merge emits NO rows under the default combined diff, so
    # without `-m` this looked identical to an empty commit — a silent drop.
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "base.txt").write_text("base")
    _commit(raw, "root")
    default_branch = _git(raw, "rev-parse", "--abbrev-ref", "HEAD")
    _git(raw, "checkout", "-q", "-b", "feature")
    (raw / "evidence" / "from_branch.txt").write_text("introduced on the branch")
    _commit(raw, "add on feature")
    # diverge the base branch so the merge cannot fast-forward
    _git(raw, "checkout", "-q", default_branch)
    (raw / "evidence" / "on_base.txt").write_text("diverge base")
    _commit(raw, "add on base")
    _git(raw, "merge", "--no-ff", "-q", "-m", "merge feature", "feature")
    merge_sha = _git(raw, "rev-parse", "HEAD")

    resolved = changed_files(raw, merge_sha, _allowlist())
    assert "evidence/from_branch.txt" in resolved  # introduced by the merged branch
    assert "evidence/on_base.txt" in resolved  # introduced on the base side


def test_genuinely_empty_allowlisted_commit_resolves_to_empty(tmp_path: Path) -> None:
    # A commit touching only non-allowlisted paths resolves (successfully) to zero
    # files — this IS ackable (nothing to ingest), distinct from a resolve failure.
    raw = tmp_path / "raw"
    _init(raw)
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "seed.txt").write_text("seed")
    _commit(raw, "seed")
    (raw / "junk").mkdir(parents=True)
    (raw / "junk" / "outside.txt").write_text("outside any allowlisted tree")
    sha = _commit(raw, "only non-allowlisted")

    assert changed_files(raw, sha, _allowlist()) == []
