"""Tests for the light folder-defaults merge (ADR 0004)."""

from __future__ import annotations

from goldberg_system.metadata import DisclosureStatus, merge_folder_defaults


def test_scalar_child_overrides_without_conflict_error() -> None:
    # Unlike the old inheritance engine, conflicting scalars do NOT raise.
    md = merge_folder_defaults(
        {"document_type": "evidence"}, {"document_type": "exhibit"}
    )
    assert md.document_type == "exhibit"


def test_scalar_inherits_when_child_absent() -> None:
    md = merge_folder_defaults({"party_role": "prosecution"}, {"topic": "cps"})
    assert md.party_role == "prosecution"
    assert md.topic == "cps"


def test_lists_union_across_layers() -> None:
    md = merge_folder_defaults(
        {"matters": ["422500059892"], "parties": ["CPS"]},
        {"matters": ["648MC011"], "parties": ["CPS", "Fadhley"]},
    )
    assert md.matters == ["422500059892", "648MC011"]
    assert md.parties == ["CPS", "Fadhley"]


def test_folder_handling_defaults_apply() -> None:
    md = merge_folder_defaults(
        {"handling": {"disclosure_status": "unused", "reviewed": True}}, {}
    )
    assert md.handling.disclosure_status is DisclosureStatus.UNUSED
    assert md.handling.reviewed is True


def test_none_values_are_ignored() -> None:
    md = merge_folder_defaults({"topic": "x"}, {"topic": None})
    assert md.topic == "x"
