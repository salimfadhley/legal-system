"""The CPR-32.12 class-fault guard: a deliberately-excluded document can NEVER be
re-added by an automated process without something loudly saying so.

These tests exercise the three enforcement points and the alarm together:

1. the healer (catch-up selection) never SELECTS a registered exclusion, classifies it
   ``deliberately_excluded``, and surviving a full catch-up pass leaves it un-ingested —
   the exact scenario that silently re-ingested four restricted documents;
2. the sink refuses a registered document even WITHOUT a ``no_index`` sidecar;
3. the raw-vs-index reconciler classifies a registered path as ``deliberately_excluded``
   rather than an invisible gap to heal; and
4. the alarm surfaces as a NAMED failing status check (``restricted_reingest_blocked``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from goldberg_system.exclusion_registry import ExclusionEntry, ExclusionRegistry
from goldberg_system.ingest.catchup import (
    count_pending,
    detect_blocked_reingests,
    entry_is_ingestable,
    run_catchup,
    select_pending,
)
from goldberg_system.ingest.reconcile import classify_path, reconcile_gap
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree
from goldberg_system.migrate.manifest import Manifest
from goldberg_system.observability.restricted_alert import (
    RESTRICTED_REINGEST_CHECK,
    load_blocked_reingests,
)
from goldberg_system.observability.state import aggregate
from goldberg_system.sinks.base import EnrichedDocument
from goldberg_system.sinks.elasticsearch_indexer import ElasticsearchIndexer


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _registry(raw_path: str = "evidence/sealed", *, sha: str | None = None) -> ExclusionRegistry:
    return ExclusionRegistry(
        [
            ExclusionEntry(
                raw_path=raw_path,
                reason="CPR 32.12 — restricted witness statements",
                timestamp="2026-08-08T00:00:00Z",
                source="deindex",
                raw_sha256=sha,
            )
        ]
    )


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )


# --------------------------------------------------------------------------- #
# 1. the healer never selects a registered exclusion (no no_index sidecar needed)
# --------------------------------------------------------------------------- #
def test_select_pending_never_selects_registered_exclusion() -> None:
    # NB: the restricted entry has NO no_index flag — the registry alone must exclude it.
    manifest = Manifest(
        {
            "s1": {"raw_path": "evidence/normal/a.txt"},
            "s2": {"raw_path": "evidence/sealed/secret.txt"},  # registered, not no_index
        }
    )
    reg = _registry("evidence/sealed")

    pending = select_pending(manifest, skip=set(), batch=50, registry=reg)
    paths = {e["raw_path"] for _s, e in pending}

    assert paths == {"evidence/normal/a.txt"}  # the sealed one is NOT selected
    assert count_pending(manifest, skip=set(), registry=reg) == 1
    # the shared predicate agrees
    assert entry_is_ingestable({"raw_path": "evidence/sealed/secret.txt"}, reg) is False
    assert entry_is_ingestable({"raw_path": "evidence/normal/a.txt"}, reg) is True


def test_detect_blocked_reingests_flags_registered_not_indexed() -> None:
    manifest = Manifest(
        {
            "s1": {"raw_path": "evidence/normal/a.txt"},
            "s2": {"raw_path": "evidence/sealed/secret.txt"},
        }
    )
    reg = _registry("evidence/sealed")

    blocked = detect_blocked_reingests(manifest, skip=set(), registry=reg)

    assert [e.get("raw_path") for _s, e, _m in blocked] == ["evidence/sealed/secret.txt"]
    assert blocked[0][2].reason.startswith("CPR 32.12")


# --------------------------------------------------------------------------- #
# THE exact failing scenario: a registered exclusion survives a full catch-up
# pass WITHOUT re-ingestion, and alarms.
# --------------------------------------------------------------------------- #
_PASSTHROUGH_TEXT = {".md", ".txt"}


class _FakeDocling:
    def convert_file(self, path: Path | str) -> str:
        p = Path(path)
        return p.read_text() if p.suffix.lower() in _PASSTHROUGH_TEXT else "text"


class _FakeEnricher:
    def enrich(self, request: Any) -> Any:
        from goldberg_system.enrichment.adapter import EnrichmentResult

        return EnrichmentResult(summary="s", keywords=[], entities=[], claims=[])


class _FakeSink:
    def __init__(self) -> None:
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def write(self, document: Any) -> Any:
        from goldberg_system.sinks.base import SinkResult

        self.written.append(document.raw_path)
        return SinkResult(sink="fake", ok=True)


def test_registered_exclusion_survives_full_catchup_without_reingest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence" / "normal").mkdir(parents=True)
    (raw / "evidence" / "sealed").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    (raw / "evidence" / "normal" / "a.txt").write_text("public evidence")
    # A restricted file that was PURGED (no no_index sidecar — sidecars patched the
    # known four, but the RULE must still hold). It is present in goldberg-raw, so the
    # old healer would treat it as a gap and re-ingest it.
    (raw / "evidence" / "sealed" / "secret.txt").write_text("restricted material")
    manifest_path = tmp_path / "provenance-manifest.json"
    alert_log = tmp_path / "alerts.jsonl"
    sink = _FakeSink()

    report = run_catchup(
        raw_root=raw,
        manifest_path=manifest_path,
        allowlist=_allowlist(),
        docling=_FakeDocling(),
        enricher=_FakeEnricher(),
        sinks=[sink],
        already_indexed=lambda: set(),
        batch=50,
        workers=1,
        with_commit=False,
        clock=lambda: "20260808T000000Z",
        registry=_registry("evidence/sealed"),
        alert_log=alert_log,
    )

    # the restricted doc was NOT re-ingested; only the public one was
    assert sink.written == ["evidence/normal/a.txt"]
    assert report.indexed == 1
    assert report.deliberately_excluded == 1
    assert "deliberately_excluded=1" in report.summary_line()
    # …and the alarm fired durably, naming what was blocked and why
    alerts = load_blocked_reingests(alert_log)
    assert [a.raw_path for a in alerts] == ["evidence/sealed/secret.txt"]
    assert alerts[0].reason.startswith("CPR 32.12")
    assert alerts[0].source == "catchup"


# --------------------------------------------------------------------------- #
# 2. the sink refuses a registered document even WITHOUT a no_index sidecar
# --------------------------------------------------------------------------- #
class _FakeES:
    def __init__(self) -> None:
        self.indexed: list[str] = []

    def index(self, index: str, id: str, document: dict) -> None:
        self.indexed.append(id)


def _doc(raw_path: str, *, sha: str | None = None) -> EnrichedDocument:
    from goldberg_system.metadata.schema import DocumentMetadata

    return EnrichedDocument(
        doc_id="gb_doc",
        raw_path=raw_path,
        raw_commit="",
        markdown="body",
        metadata=DocumentMetadata(raw_path=raw_path, raw_sha256=sha),  # NO no_index flag
    )


def test_sink_refuses_registered_document_without_sidecar(tmp_path: Path) -> None:
    fake = _FakeES()
    alert_log = tmp_path / "alerts.jsonl"
    indexer = ElasticsearchIndexer(
        fake, "idx", registry=_registry("evidence/sealed"), alert_log=alert_log
    )

    result = indexer.write(_doc("evidence/sealed/secret.txt"))

    assert result.ok is False
    assert "deliberately-excluded" in (result.detail or "")
    assert "CPR 32.12" in (result.detail or "")
    assert fake.indexed == []  # never written
    # alarmed durably
    assert [a.raw_path for a in load_blocked_reingests(alert_log)] == [
        "evidence/sealed/secret.txt"
    ]


def test_sink_refuses_registered_document_by_sha(tmp_path: Path) -> None:
    fake = _FakeES()
    reg = _registry("some/other/path", sha="deadbeef")
    indexer = ElasticsearchIndexer(fake, "idx", registry=reg)

    # path does not match, but the content sha does → still refused
    result = indexer.write(_doc("evidence/moved/renamed.txt", sha="DEADBEEF"))

    assert result.ok is False
    assert fake.indexed == []


def test_sink_still_indexes_a_normal_document() -> None:
    fake = _FakeES()
    indexer = ElasticsearchIndexer(fake, "idx", registry=_registry("evidence/sealed"))

    result = indexer.write(_doc("evidence/normal/a.txt"))

    assert result.ok is True
    assert fake.indexed == ["gb_doc"]


# --------------------------------------------------------------------------- #
# 3. the reconciler classifies a registered path as deliberately_excluded, not a gap
# --------------------------------------------------------------------------- #
def test_reconcile_gap_classifies_registered_as_excluded_not_gap(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence" / "sealed").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    (raw / "evidence" / "doc.txt").write_text("real evidence")
    (raw / "evidence" / "sealed" / "secret.txt").write_text("restricted")

    report = reconcile_gap(
        raw_root=raw,
        allowlist=_allowlist(),
        indexed_shas=set(),  # nothing indexed → naively everything looks like a gap
        registry=_registry("evidence/sealed"),
    )

    gap_paths = {gf.raw_path for gf in report.gap}
    assert gap_paths == {"evidence/doc.txt"}  # the sealed file is NOT a gap
    assert report.skipped_excluded == 1
    assert "skipped_excluded=1" in report.summary_line()


def test_classify_path_registered_is_not_invisible_gap(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "evidence" / "sealed").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: M1\n")
    (raw / "evidence" / "sealed" / "secret.txt").write_text("restricted")

    out = classify_path(
        raw_root=raw,
        raw_path="evidence/sealed/secret.txt",
        indexed_shas=set(),
        allowlist=_allowlist(),
        registry=_registry("evidence/sealed"),
    )

    assert out["in_raw"] is True
    assert out["ingestable"] is False
    assert out["invisible_gap"] is False
    assert "deliberately excluded" in out["note"]


# --------------------------------------------------------------------------- #
# 4. the alarm surfaces as a NAMED failing status check
# --------------------------------------------------------------------------- #
class _MinimalStateES:
    """Enough of an ES to let ``aggregate`` build a SystemState (all empty/benign)."""

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
        if any("range" in c for c in bool_q.get("filter", []) + bool_q.get("must", [])):
            return {"hits": {"total": {"value": 0}}}
        return {"hits": {"hits": []}}


def _check(state: Any, name: str) -> dict[str, Any]:
    return next(c for c in state.health["checks"] if c["name"] == name)


def test_status_named_check_ok_when_no_alerts(tmp_path: Path) -> None:
    state = aggregate(_MinimalStateES(), restricted_alert_log=tmp_path / "none.jsonl")
    check = _check(state, RESTRICTED_REINGEST_CHECK)
    assert check["ok"] is True
    assert check["detail"] == "none"


def test_status_named_check_fails_and_names_what_was_blocked(tmp_path: Path) -> None:
    from goldberg_system.observability.restricted_alert import record_blocked_reingest

    alert_log = tmp_path / "alerts.jsonl"
    record_blocked_reingest(
        "evidence/sealed/secret.txt",
        "CPR 32.12 — restricted",
        source="catchup",
        log_path=alert_log,
    )

    state = aggregate(_MinimalStateES(), restricted_alert_log=alert_log)

    check = _check(state, RESTRICTED_REINGEST_CHECK)
    assert check["ok"] is False  # a NAMED failing check, not a buried DLQ line
    assert "evidence/sealed/secret.txt" in check["detail"]
    assert "CPR 32.12" in check["detail"]
    assert state.health["status"] == "degraded"
