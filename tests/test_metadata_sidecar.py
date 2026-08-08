"""Per-file sidecars: resolution chain, merge semantics, new fields, bad-key handling.

Covers doc/system/metadata.md deliverables 1, 2 and 4 (the sidecar chain, the new
schema fields flowing through the manifest translation, and the loud-but-present
bad-key handling). The enricher-notes wiring and the ``metadata lint`` self-test have
their own modules.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from goldberg_system.metadata import sidecar
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import (
    build_entry,
    folder_base_fields,
    resolve_chain_full,
)


def _allowlist(tmp_path: Path) -> Allowlist:
    cfg = tmp_path / "allow.yaml"
    cfg.write_text(
        "include:\n  evidence:\n    origin: received\nexclude_globs:\n  - '*.pyc'\n"
    )
    return Allowlist.load(cfg)


def _seed(root: Path) -> Path:
    ev = root / "evidence"
    (ev / "folder").mkdir(parents=True)
    (root / "evidence" / "metadata.yaml").write_text("case_number: '4225'\n")
    (ev / "folder" / "doc.pdf").write_bytes(b"%PDF body")
    return root


# --- deliverable 1: sidecar is the last, most-specific chain layer -----------------


def test_sidecar_overrides_folder_scalar(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "metadata.yaml").write_text("author: Folder Person\n")
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "author: Sidecar Person\n"
    )
    resolved = resolve_chain_full(Path("evidence/folder/doc.pdf"), root)
    assert resolved.errors == []
    # most-specific layer (the file's own sidecar) wins for a scalar
    assert resolved.fields["author"] == "Sidecar Person"


def test_sidecar_unions_list_field(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "metadata.yaml").write_text(
        "keywords: [alpha, beta]\n"
    )
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "keywords: [beta, gamma]\n"
    )
    fields = resolve_chain_full(Path("evidence/folder/doc.pdf"), root).fields
    # order-preserving union, no duplicates
    assert fields["keywords"] == ["alpha", "beta", "gamma"]


def test_matters_union_case_number_and_explicit(tmp_path: Path) -> None:
    root = _seed(tmp_path)  # folder sets case_number: '4225'
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "matters: ['422500059892']\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    assert entry.matters == ["4225", "422500059892"]


def test_sidecar_is_never_ingested(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text("author: X\n")
    al = _allowlist(tmp_path)
    assert build_entry(root, Path("evidence/folder/doc.pdf.metadata.yaml"), al) is None
    assert build_entry(root, Path("evidence/metadata.yaml"), al) is None
    assert sidecar.is_sidecar_name("doc.pdf.metadata.yaml")
    assert sidecar.is_sidecar_name("metadata.yaml")
    assert not sidecar.is_sidecar_name("doc.pdf")


# --- deliverable 2: new fields flow through the manifest translation --------------


def test_new_fields_flow_to_document_metadata(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "author": "Paul Keitch",
                "claim_source": "Paul Keitch",
                "notes": "Exhibit SM/01.",
                "method": "opened legislation.gov.uk XML for SI 2020/759, diffed",
                "source_channel": "served in the Oct 2025 bundle",
                "obtained_note": "disc 3 of 4",
                "superseded_by": "evidence/folder/doc-v2.pdf",
            }
        )
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    assert entry.author == "Paul Keitch"
    assert entry.method.startswith("opened legislation.gov.uk")
    assert entry.notes == "Exhibit SM/01."
    assert entry.source_channel == "served in the Oct 2025 bundle"
    assert entry.superseded_by == "evidence/folder/doc-v2.pdf"
    assert entry.metadata_error is None


def test_method_is_free_text_not_boolean() -> None:
    # method records what was opened; there is no verified boolean anywhere.
    md = DocumentMetadata(method="checked against legislation.gov.uk 2026-08-07")
    assert md.method == "checked against legislation.gov.uk 2026-08-07"
    assert not hasattr(md, "verified")


def test_date_uncertain_defaults_true_when_basis_not_on_its_face() -> None:
    md = DocumentMetadata(date="2020-06-01", date_basis="PDF CreationDate")
    assert md.date_uncertain is True


def test_date_uncertain_false_when_on_its_face() -> None:
    md = DocumentMetadata(date="2020-06-01", date_basis="on its face")
    assert md.date_uncertain is False


def test_date_uncertain_explicit_value_wins() -> None:
    md = DocumentMetadata(
        date="2020-06-01", date_basis="PDF CreationDate", date_uncertain=False
    )
    assert md.date_uncertain is False


def test_date_uncertain_left_default_when_no_date() -> None:
    md = DocumentMetadata(date_basis="whatever")
    assert md.date_uncertain is False


def test_date_uncertain_flows_from_sidecar(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "date: '2020-06-01'\ndate_basis: served in Oct 2025 bundle\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    assert entry.date == "2020-06-01"
    assert entry.date_uncertain is True


def test_folder_base_fields_carries_new_fields_and_date_uncertain() -> None:
    chain = {
        "author": "A",
        "notes": "n",
        "method": "m",
        "date": "2021-01-01",
        "date_basis": "PDF CreationDate",
        "matters": ["X"],
    }
    fields = folder_base_fields(chain)
    assert fields["author"] == "A"
    assert fields["notes"] == "n"
    assert fields["method"] == "m"
    assert fields["date_uncertain"] is True
    assert fields["matters"] == ["X"]


# --- deliverable 4: bad-key handling is loud-but-present --------------------------


def test_unknown_key_drops_layer_but_keeps_document(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    # typo: authour instead of author — the whole sidecar layer is rejected
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "authour: Paul Keitch\nnotes: should not be applied\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    # the document is STILL manifested (a typo must not make it disappear)
    assert entry is not None
    # none of the rejected layer's values are applied
    assert entry.author is None
    assert entry.notes is None
    # but the failure is loud and visible
    assert entry.metadata_error is not None
    assert "authour" in entry.metadata_error
    # inherited folder metadata still applies
    assert entry.matters == ["4225"]


def test_no_index_without_reason_fails_closed_and_alarms(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "no_index: true\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    # FAIL CLOSED (casework): no_index: true with a missing reason now EXCLUDES the doc
    # (do-not-index) and alarms that the reason is missing — it must never "index anyway".
    assert entry.no_index is True
    assert entry.metadata_error is not None
    assert "no_index_reason" in entry.metadata_error
    # an unclassified exclusion defaults to the safer, higher-severity category
    assert entry.no_index_category == "legally_obligatory"


def test_no_index_with_reason_is_honoured(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "no_index: true\nno_index_reason: CPR 32.12 witness statement\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    assert entry.no_index is True
    assert entry.metadata_error is None


def test_malformed_yaml_layer_rejected_not_fatal(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(
        "author: [unterminated\n"
    )
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None
    # ORDINARY fields still FAIL OPEN: the dropped layer's author is simply not applied.
    assert entry.author is None
    assert entry.metadata_error is not None
    assert "malformed YAML" in entry.metadata_error
    # …but the EXCLUSION dimension FAILS CLOSED: an unparseable layer could have been an
    # exclusion, so the document it governs is do-not-index (over-exclusion is cheap).
    assert entry.no_index is True
    assert entry.no_index_category == "legally_obligatory"


@pytest.mark.parametrize("bad_key", ["autor", "mater", "claimsource", "no_indexx"])
def test_various_typos_are_all_caught(tmp_path: Path, bad_key: str) -> None:
    root = _seed(tmp_path)
    (root / "evidence" / "folder" / "doc.pdf.metadata.yaml").write_text(f"{bad_key}: v\n")
    entry = build_entry(root, Path("evidence/folder/doc.pdf"), _allowlist(tmp_path))
    assert entry is not None and entry.metadata_error is not None
    assert bad_key in entry.metadata_error
