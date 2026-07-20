"""Deterministic doc-ids and content-hash staleness.

The doc-id is keyed on the raw file (its repo-relative path *and* its bytes) so
that re-ingesting the same file yields the **same** id (updates, never
duplicates), while the same content at a different path is a distinct document.
The content hash alone drives the staleness check: if the raw bytes are unchanged
the pipeline can skip re-extraction.
"""

from __future__ import annotations

import hashlib

_DOC_ID_PREFIX = "gb"


def compute_content_hash(content: bytes) -> str:
    """Return the SHA-256 hex digest of ``content``."""
    return hashlib.sha256(content).hexdigest()


def compute_doc_id(raw_path: str, content: bytes) -> str:
    """Return a deterministic doc-id for a raw file.

    Keyed on ``raw_path`` (repo-relative, POSIX) and the file bytes. Stable and
    reproducible across runs and machines.
    """
    digest = hashlib.sha256()
    digest.update(raw_path.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content)
    return f"{_DOC_ID_PREFIX}_{digest.hexdigest()}"


def is_stale(previous_content_hash: str | None, content: bytes) -> bool:
    """Return True if ``content`` differs from the previously recorded hash.

    A ``None`` previous hash (never processed) is always stale.
    """
    if previous_content_hash is None:
        return True
    return previous_content_hash != compute_content_hash(content)
