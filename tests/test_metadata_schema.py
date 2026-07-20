"""Tests for the DocumentMetadata schema (FR-001, FR-003, FR-004, FR-009, NFR-001/002)."""

from __future__ import annotations

import pytest

from goldberg_system.metadata import (
    DisclosureStatus,
    DocumentMetadata,
    HandlingFlags,
    Origin,
    Role,
    Sensitivity,
    SourceChannel,
)


def test_defaults_are_empty_but_valid() -> None:
    md = DocumentMetadata()
    assert md.matters == []
    assert md.parties == []
    assert md.primary_matter is None
    assert md.raw_path is None
    assert md.papra_document_id is None


def test_matters_is_a_list_with_optional_primary() -> None:
    md = DocumentMetadata(
        matters=["422500059892", "648MC011"], primary_matter="648MC011"
    )
    assert md.matters == ["422500059892", "648MC011"]
    assert md.primary_matter == "648MC011"


def test_primary_matter_must_be_in_matters() -> None:
    with pytest.raises(ValueError):
        DocumentMetadata(matters=["422500059892"], primary_matter="not-a-matter")


def test_two_axis_taxonomy_fields() -> None:
    md = DocumentMetadata(origin=Origin.AUTHORED, role=Role.INPUT)
    assert md.origin is Origin.AUTHORED
    assert md.role is Role.INPUT


def test_papra_mapping_and_provenance() -> None:
    md = DocumentMetadata(
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="abc123",
        papra_document_id="doc_789",
    )
    assert md.raw_path == "evidence/cps/x/msg.eml"
    assert md.raw_commit == "abc123"
    assert md.papra_document_id == "doc_789"


@pytest.mark.parametrize(
    "field, expected",
    [
        ("cpia_s17", True),
        ("privileged", True),
        ("sensitivity", Sensitivity.SENSITIVE),
        ("disclosure_status", DisclosureStatus.UNKNOWN),
        ("source_channel", SourceChannel.UNKNOWN),
        ("reviewed", False),
    ],
)
def test_handling_flags_default_to_the_safe_value(field: str, expected: object) -> None:
    flags = HandlingFlags()
    assert getattr(flags, field) == expected


def test_unreviewed_handling_requires_caution() -> None:
    assert HandlingFlags().requires_caution is True
    cleared = HandlingFlags(
        cpia_s17=False,
        privileged=False,
        sensitivity=Sensitivity.NORMAL,
        reviewed=True,
    )
    assert cleared.requires_caution is False


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValueError):
        DocumentMetadata(not_a_real_field="x")


def test_round_trips_losslessly() -> None:
    md = DocumentMetadata(
        document_type="email",
        parties=["CPS", "Fadhley"],
        matters=["422500059892"],
        author="Asif Akram",
        origin=Origin.RECEIVED,
        role=Role.INPUT,
        raw_path="evidence/cps/x/msg.eml",
        raw_commit="abc123",
    )
    restored = DocumentMetadata.model_validate_json(md.model_dump_json())
    assert restored == md
