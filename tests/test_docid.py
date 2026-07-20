"""Tests for deterministic doc-id + staleness (FR-007, FR-008, NFR-003)."""

from __future__ import annotations

from goldberg_system.identity import (
    compute_content_hash,
    compute_doc_id,
    is_stale,
)


def test_doc_id_is_deterministic() -> None:
    a = compute_doc_id("evidence/x/msg.eml", b"hello")
    b = compute_doc_id("evidence/x/msg.eml", b"hello")
    assert a == b


def test_doc_id_depends_on_path() -> None:
    assert compute_doc_id("a/msg.eml", b"hello") != compute_doc_id(
        "b/msg.eml", b"hello"
    )


def test_doc_id_depends_on_content() -> None:
    assert compute_doc_id("a/msg.eml", b"hello") != compute_doc_id(
        "a/msg.eml", b"world"
    )


def test_doc_id_has_prefix() -> None:
    assert compute_doc_id("a", b"x").startswith("gb_")


def test_content_hash_stable() -> None:
    assert compute_content_hash(b"abc") == compute_content_hash(b"abc")
    assert compute_content_hash(b"abc") != compute_content_hash(b"abd")


def test_staleness() -> None:
    h = compute_content_hash(b"v1")
    assert is_stale(h, b"v1") is False
    assert is_stale(h, b"v2") is True
    assert is_stale(None, b"v1") is True
