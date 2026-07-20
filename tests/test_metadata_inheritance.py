"""Tests for directory-inheritance resolution (FR-002, C-004)."""

from __future__ import annotations

import pytest

from goldberg_system.metadata import InheritanceConflict, resolve_metadata


def test_empty_chain_yields_defaults() -> None:
    md = resolve_metadata([])
    assert md.matters == []


def test_locked_scalar_inherits_from_parent() -> None:
    md = resolve_metadata([{"document_type": "evidence"}, {"topic": "cps"}])
    assert md.document_type == "evidence"
    assert md.topic == "cps"


def test_locked_scalar_conflict_raises() -> None:
    with pytest.raises(InheritanceConflict):
        resolve_metadata([{"document_type": "evidence"}, {"document_type": "exhibit"}])


def test_locked_scalar_same_value_is_ok() -> None:
    md = resolve_metadata(
        [{"party_role": "prosecution"}, {"party_role": "prosecution"}]
    )
    assert md.party_role == "prosecution"


def test_overridable_child_replaces_parent() -> None:
    md = resolve_metadata([{"date": "2025-01-01"}, {"date": "2025-06-30"}])
    assert md.date == "2025-06-30"


def test_non_inherited_takes_leaf_only() -> None:
    md = resolve_metadata([{"summary": "parent summary"}, {"topic": "x"}])
    # parent's summary does not propagate to the leaf level
    assert md.summary is None


def test_non_inherited_leaf_value_used() -> None:
    md = resolve_metadata([{"summary": "parent"}, {"summary": "leaf"}])
    assert md.summary == "leaf"


def test_irreversible_skip_stays_true() -> None:
    md = resolve_metadata([{"skip": True}, {"skip": False}])
    assert md.skip is True


def test_lists_union_merge_in_order() -> None:
    md = resolve_metadata(
        [
            {"parties": ["CPS", "Goldberg"], "matters": ["422500059892"]},
            {"parties": ["Goldberg", "Fadhley"], "matters": ["648MC011"]},
        ]
    )
    assert md.parties == ["CPS", "Goldberg", "Fadhley"]
    assert md.matters == ["422500059892", "648MC011"]


def test_unknown_keys_are_ignored_by_resolver() -> None:
    # Keys not on the schema are dropped before validation (they never reach it).
    md = resolve_metadata([{"document_type": "evidence", "legacy_key": "x"}])
    assert md.document_type == "evidence"
