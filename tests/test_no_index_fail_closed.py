"""FAIL-CLOSED ``no_index`` — the legal-safety latch (casework, CPR-32.12).

Ordinary metadata fields fail OPEN (a bad layer loses at worst a document). The
``no_index`` *exclusion* dimension is the opposite risk — exposing restricted material —
so it FAILS CLOSED: any APPARENT exclusion attempt resolves to do-not-index and is loud.
"An excluded document wrongly indexed cannot be un-seen; a wrongly excluded document is
found the moment someone looks."

Each test is a real scenario casework named:

* a typo'd key (``noindex: true``)            → NOT indexed + alarm
* an unparseable value (``no_index: maybe``)  → NOT indexed + alarm
* ``no_index: true`` with no reason           → NOT indexed + alarm
* broken-YAML folder metadata                 → whole subtree NOT indexed + alarm
* a child ``no_index: false``                 → does NOT unset a parent exclusion (latch)
* ``legally_obligatory`` vs ``housekeeping``  → different alarm severities
* regression: an ordinary typo (``mater:``)   → STILL fails open (indexed + metadata_error)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from goldberg_system.ingest.catchup import entry_is_ingestable
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree
from goldberg_system.migrate.manifest import build_entry
from goldberg_system.observability.restricted_alert import (
    RESTRICTED_REINGEST_CHECK,
    BlockedReingest,
    load_blocked_reingests,
    severity_for_category,
)
from goldberg_system.observability.state import aggregate
from goldberg_system.sinks.base import EnrichedDocument
from goldberg_system.sinks.elasticsearch_indexer import ElasticsearchIndexer


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )


def _raw(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    (root / "evidence" / "folder").mkdir(parents=True, exist_ok=True)
    (root / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    return root


def _entry_for(tmp_path: Path, sidecar_body: str) -> Any:
    """Resolve ``evidence/folder/doc.txt`` with the given per-file sidecar."""
    root = _raw(tmp_path)
    (root / "evidence" / "folder" / "doc.txt").write_text("body")
    (root / "evidence" / "folder" / "doc.txt.metadata.yaml").write_text(sidecar_body)
    entry = build_entry(root, Path("evidence/folder/doc.txt"), _allowlist())
    assert entry is not None
    return entry


class _FakeES:
    def __init__(self) -> None:
        self.indexed: list[str] = []

    def index(self, index: str, id: str, document: dict[str, Any]) -> None:
        self.indexed.append(id)


def _assert_refused_and_alarmed(
    tmp_path: Path, entry: Any, *, expected_category: str
) -> BlockedReingest:
    """Feed the resolved entry to the ES sink and assert it is refused AND alarms."""
    fake = _FakeES()
    alert_log = tmp_path / f"alerts-{expected_category}.jsonl"  # unique per call
    indexer = ElasticsearchIndexer(fake, "idx", alert_log=alert_log)
    doc = EnrichedDocument(
        doc_id="gb_doc",
        raw_path=entry.raw_path,
        raw_commit="",
        markdown="body",
        metadata=DocumentMetadata(
            raw_path=entry.raw_path,
            no_index=entry.no_index,
            no_index_reason=entry.no_index_reason,
            no_index_category=entry.no_index_category,
        ),
    )
    result = indexer.write(doc)
    assert result.ok is False  # refused, never indexed
    assert fake.indexed == []
    alerts = load_blocked_reingests(alert_log)
    assert len(alerts) == 1
    assert alerts[0].category == expected_category
    return alerts[0]


# --------------------------------------------------------------------------- #
# 1. a typo'd key → NOT indexed + alarm
# --------------------------------------------------------------------------- #
def test_typo_noindex_key_fails_closed(tmp_path: Path) -> None:
    entry = _entry_for(tmp_path, "noindex: true\n")
    assert entry.no_index is True  # a misspelled key ⇒ assume exclusion intended
    assert entry_is_ingestable(entry.__dict__) is False  # never selected for ingest
    assert entry.metadata_error is not None
    assert "typo" in entry.metadata_error.lower()
    # unclassified ⇒ safer, higher-severity category
    assert entry.no_index_category == "legally_obligatory"
    _assert_refused_and_alarmed(tmp_path, entry, expected_category="legally_obligatory")


def test_all_fuzzy_variants_of_the_key_fail_closed(tmp_path: Path) -> None:
    for variant in ("noindex", "no-index", "No_Index", "NO_INDEX", "no _index"):
        entry = _entry_for(tmp_path, f"{variant}: true\nno_index_reason: r\n")
        assert entry.no_index is True, f"{variant!r} was not treated as an exclusion"


# --------------------------------------------------------------------------- #
# 2. an unparseable value → NOT indexed + alarm
# --------------------------------------------------------------------------- #
def test_unparseable_no_index_value_fails_closed(tmp_path: Path) -> None:
    entry = _entry_for(tmp_path, "no_index: maybe\nno_index_reason: r\n")
    assert entry.no_index is True  # "maybe" is not a clean bool → fail closed
    assert entry_is_ingestable(entry.__dict__) is False
    assert entry.metadata_error is not None
    assert "not a clean boolean" in entry.metadata_error
    _assert_refused_and_alarmed(tmp_path, entry, expected_category="legally_obligatory")


def test_quoted_true_string_is_not_a_clean_bool(tmp_path: Path) -> None:
    # ``no_index: "true"`` parses to a string, not a bool — untrustworthy ⇒ fail closed.
    entry = _entry_for(tmp_path, 'no_index: "true"\nno_index_reason: r\n')
    assert entry.no_index is True
    assert "not a clean boolean" in (entry.metadata_error or "")


# --------------------------------------------------------------------------- #
# 3. no_index: true with no reason → NOT indexed + alarm
# --------------------------------------------------------------------------- #
def test_no_index_true_without_reason_fails_closed(tmp_path: Path) -> None:
    entry = _entry_for(tmp_path, "no_index: true\n")
    assert entry.no_index is True  # was "index anyway + flag"; now EXCLUDE + alarm
    assert entry_is_ingestable(entry.__dict__) is False
    assert "no_index_reason" in (entry.metadata_error or "")
    _assert_refused_and_alarmed(tmp_path, entry, expected_category="legally_obligatory")


# --------------------------------------------------------------------------- #
# 4. broken-YAML folder metadata → the WHOLE subtree fails closed + alarm
# --------------------------------------------------------------------------- #
def test_broken_yaml_folder_metadata_fails_whole_subtree_closed(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    deep = root / "evidence" / "restricted" / "sub" / "deep"
    deep.mkdir(parents=True)
    # A folder metadata.yaml that will NOT parse — we cannot rule out an exclusion.
    (root / "evidence" / "restricted" / "metadata.yaml").write_text(
        "no_index: [unterminated\n"
    )
    (root / "evidence" / "restricted" / "top.txt").write_text("a")
    (deep / "statement.txt").write_text("b")

    for rel in ("evidence/restricted/top.txt", "evidence/restricted/sub/deep/statement.txt"):
        entry = build_entry(root, Path(rel), _allowlist())
        assert entry is not None, rel
        assert entry.no_index is True, f"{rel} should fail closed"
        assert entry_is_ingestable(entry.__dict__) is False
        assert "malformed YAML" in (entry.metadata_error or "")
        assert entry.no_index_category == "legally_obligatory"

    # a SIBLING subtree with valid metadata is unaffected — only the broken subtree closes
    (root / "evidence" / "open").mkdir(parents=True)
    (root / "evidence" / "open" / "ok.txt").write_text("c")
    sibling = build_entry(root, Path("evidence/open/ok.txt"), _allowlist())
    assert sibling is not None and sibling.no_index is False


def test_non_mapping_metadata_layer_fails_closed(tmp_path: Path) -> None:
    # A metadata file that parses to a non-mapping (a bare list) is equally untrustworthy.
    entry = _entry_for(tmp_path, "- just\n- a\n- list\n")
    assert entry.no_index is True
    assert "not a mapping" in (entry.metadata_error or "")


# --------------------------------------------------------------------------- #
# 5. a child no_index: false does NOT unset a parent exclusion (the one-way latch)
# --------------------------------------------------------------------------- #
def test_child_no_index_false_cannot_unset_parent(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    child = root / "evidence" / "restricted" / "child"
    child.mkdir(parents=True)
    (root / "evidence" / "restricted" / "metadata.yaml").write_text(
        "no_index: true\nno_index_reason: CPR 32.12\n"
    )
    (child / "metadata.yaml").write_text("no_index: false\n")  # tries to re-include
    (child / "doc.txt").write_text("still restricted")

    entry = build_entry(root, Path("evidence/restricted/child/doc.txt"), _allowlist())
    assert entry is not None
    assert entry.no_index is True  # OR-latch: false never defeats a parent's true
    assert entry.no_index_reason == "CPR 32.12"  # the parent's reason survives


# --------------------------------------------------------------------------- #
# 6. category → different alarm severities (legally_obligatory vs housekeeping)
# --------------------------------------------------------------------------- #
def test_category_drives_alarm_severity(tmp_path: Path) -> None:
    # a well-formed exclusion whose category is honoured end-to-end…
    obligatory = _entry_for(
        tmp_path,
        "no_index: true\nno_index_reason: CPR 32.12\nno_index_category: legally_obligatory\n",
    )
    assert obligatory.no_index_category == "legally_obligatory"

    housekeeping = _entry_for(
        tmp_path,
        "no_index: true\nno_index_reason: build artefact\nno_index_category: housekeeping\n",
    )
    assert housekeeping.no_index_category == "housekeeping"

    # …produces a DIFFERENT alarm severity for each class.
    assert severity_for_category("legally_obligatory") == "incident"
    assert severity_for_category("housekeeping") == "noise"
    a_incident = _assert_refused_and_alarmed(
        tmp_path, obligatory, expected_category="legally_obligatory"
    )
    a_noise = _assert_refused_and_alarmed(
        tmp_path, housekeeping, expected_category="housekeeping"
    )
    assert a_incident.severity == "incident"
    assert a_noise.severity == "noise"
    assert a_incident.severity != a_noise.severity


def test_unknown_category_defaults_to_the_safer_reading(tmp_path: Path) -> None:
    entry = _entry_for(
        tmp_path,
        "no_index: true\nno_index_reason: r\nno_index_category: banana\n",
    )
    # missing/unknown category ⇒ legally_obligatory (alarm loudly, not quietly)
    assert entry.no_index_category == "legally_obligatory"
    assert "unknown no_index_category" in (entry.metadata_error or "")


class _MinimalStateES:
    """Just enough ES for ``aggregate`` to build a benign SystemState.

    ``restricted_hits`` are the ``_source`` dicts the LIVE restricted-reingest query
    (``bool.should``) returns — documents actually indexed right now.
    """

    def __init__(self, restricted_hits: list[dict[str, Any]] | None = None) -> None:
        self._restricted_hits = restricted_hits or []

    def count(self, index: str) -> dict[str, Any]:
        return {"count": 5}

    def search(self, **kw: Any) -> dict[str, Any]:
        aggs = kw.get("aggs") or {}
        if "stage" in aggs:
            return {"aggregations": {"stage": {"buckets": []}}}
        if "t" in aggs:
            return {"aggregations": {"t": {"buckets": []}}}
        q = kw.get("query", {})
        bool_q = q.get("bool", {})
        if "should" in bool_q:  # the live restricted-exposure query
            return {"hits": {"hits": [{"_source": s} for s in self._restricted_hits]}}
        if any("range" in c for c in bool_q.get("filter", []) + bool_q.get("must", [])):
            return {"hits": {"total": {"value": 0}}}
        return {"hits": {"hits": []}}


def _check(state: Any, name: str) -> dict[str, Any]:
    return next(c for c in state.health["checks"] if c["name"] == name)


def test_legally_obligatory_incident_degrades_but_housekeeping_noise_does_not() -> None:
    from goldberg_system.exclusion_registry import ExclusionEntry, ExclusionRegistry

    # A housekeeping no_index document IN THE INDEX is NOISE — the check stays ok.
    noise_es = _MinimalStateES(
        restricted_hits=[
            {
                "raw_path": "evidence/build/dup.txt",
                "no_index": True,
                "no_index_reason": "duplicate build artefact",
                "no_index_category": "housekeeping",
            }
        ]
    )
    state = aggregate(noise_es, registry=ExclusionRegistry())  # empty registry
    check = _check(state, RESTRICTED_REINGEST_CHECK)
    # The distinct signal: a housekeeping-only exposure does NOT fail the restricted check.
    assert check["ok"] is True
    assert "housekeeping" in check["detail"]

    # A legally_obligatory document IN THE INDEX is an INCIDENT — the check FAILS.
    incident_es = _MinimalStateES(
        restricted_hits=[{"raw_path": "evidence/sealed/secret.txt", "raw_sha256": None}]
    )
    registry = ExclusionRegistry(
        [
            ExclusionEntry(
                raw_path="evidence/sealed",
                reason="CPR 32.12 — restricted",
                timestamp="2026-08-08T00:00:00Z",
                source="deindex",
            )
        ]
    )
    state = aggregate(incident_es, registry=registry)
    check = _check(state, RESTRICTED_REINGEST_CHECK)
    assert check["ok"] is False
    assert "INCIDENT" in check["detail"]
    assert state.health["status"] == "degraded"


# --------------------------------------------------------------------------- #
# 7. REGRESSION — an ordinary typo STILL fails OPEN (we did not break the default)
# --------------------------------------------------------------------------- #
def test_ordinary_typo_still_fails_open(tmp_path: Path) -> None:
    # ``mater:`` for ``matters:`` is an ordinary-field typo — the layer is dropped but the
    # document STILL ingests with a visible metadata_error (losing a doc is the wrong
    # trade for a non-exclusion field). This proves the fail-closed change is scoped ONLY
    # to the exclusion dimension.
    entry = _entry_for(tmp_path, "mater: 9999\n")
    assert entry.no_index is False  # NOT excluded
    assert entry_is_ingestable(entry.__dict__) is True  # still selected for ingest
    assert entry.metadata_error is not None  # but loud
    assert "mater" in entry.metadata_error
    # inherited folder metadata still applies (the drop is scoped to the bad layer)
    assert entry.matters == ["M1"]
