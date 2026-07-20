"""Deterministic document identity and content-hash staleness."""

from goldberg_system.identity.docid import (
    compute_content_hash,
    compute_doc_id,
    is_stale,
)

__all__ = ["compute_doc_id", "compute_content_hash", "is_stale"]
