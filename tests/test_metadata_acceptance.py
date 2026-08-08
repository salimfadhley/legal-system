"""The five casework acceptance cases for per-file sidecars (doc/system/metadata.md).

Each drives the real ingest translation — build_manifest → write/load Manifest →
base_for_sha → build_enriched_from_raw → sinks — with a stubbed enricher (no network),
so it asserts the same paths casework will verify through the MCP tools:

  (a) author override + notes on an undateable doc
  (b) notes changing what the enricher extracts  (wiring here; extraction needs a live doc)
  (c) per-file no_index on ONE file while siblings stay indexed
  (d) method + claim_source correction
  (e) a caveat note
"""

from __future__ import annotations

from pathlib import Path

from goldberg_system.enrichment.adapter import EnrichmentRequest, EnrichmentResult
from goldberg_system.metadata.schema import Claim, DocumentMetadata
from goldberg_system.metadata.sidecar import ANNOTATION_OPEN
from goldberg_system.migrate.allowlist import Allowlist
from goldberg_system.migrate.manifest import Manifest, build_manifest, write_manifest
from goldberg_system.pipeline import build_enriched_from_raw
from goldberg_system.sinks.base import EnrichedDocument, SinkResult
from goldberg_system.sinks.elasticsearch_indexer import to_es_document


def _allowlist(tmp_path: Path) -> Allowlist:
    cfg = tmp_path / "allow.yaml"
    cfg.write_text("include:\n  evidence:\n    origin: received\nexclude_globs: []\n")
    return Allowlist.load(cfg)


class _StubEnricher:
    def __init__(self, result: EnrichmentResult) -> None:
        self.seen: EnrichmentRequest | None = None
        self._result = result

    def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        self.seen = request
        return self._result


class _CapturingIndexer:
    """A minimal sink mirroring ElasticsearchIndexer's no_index refusal, capturing writes."""

    def __init__(self) -> None:
        self.indexed: list[str] = []

    @property
    def name(self) -> str:
        return "capture"

    def write(self, document: EnrichedDocument) -> SinkResult:
        if document.metadata.no_index:
            return SinkResult(sink=self.name, ok=False, detail="refused no_index")
        self.indexed.append(document.raw_path)
        return SinkResult(sink=self.name, ok=True)


def _manifest_for(root: Path, tmp_path: Path) -> Manifest:
    entries = build_manifest(root, _allowlist(tmp_path), with_commit=False)
    write_manifest(entries, tmp_path / "manifest.json")
    return Manifest.load(tmp_path / "manifest.json")


def _base_for(manifest: Manifest, root: Path, raw_path: str) -> DocumentMetadata:
    sha = __import__("hashlib").sha256((root / raw_path).read_bytes()).hexdigest()
    base = manifest.base_for_sha(sha)
    assert base is not None
    return base


# --- (a) author override + notes on an undateable doc -----------------------------


def test_acceptance_a_author_override_and_notes_undateable(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "stmt.txt").write_text("A statement with no date on its face.")
    (root / "evidence" / "stmt.txt.metadata.yaml").write_text(
        "author: Paul Keitch\nnotes: Exhibit SM/01 — the disclosure officer's statement.\n"
    )
    manifest = _manifest_for(root, tmp_path)
    base = _base_for(manifest, root, "evidence/stmt.txt")

    # the enricher would infer a different author — the human override must win
    enricher = _StubEnricher(EnrichmentResult(summary="s", author="Someone Else"))
    doc = build_enriched_from_raw(
        "evidence/stmt.txt", (root / "evidence" / "stmt.txt").read_text(), enricher, base=base
    )
    es = to_es_document(doc)
    assert es["author"] == "Paul Keitch"  # search_evidence shows the corrected author
    assert ANNOTATION_OPEN in es["content"]  # note appears fenced
    assert es["notes"].startswith("Exhibit SM/01")
    assert doc.metadata.date is None  # genuinely undateable — nothing invented


# --- (b) notes changing what the enricher extracts (wiring; extraction needs live) --


def test_acceptance_b_notes_reach_enricher(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "fig.txt").write_text("Figure: 4,001 units")
    (root / "evidence" / "fig.txt.metadata.yaml").write_text(
        "notes: The 4,001 is OCR noise; the real figure is 4001 GBP.\n"
    )
    manifest = _manifest_for(root, tmp_path)
    base = _base_for(manifest, root, "evidence/fig.txt")

    enricher = _StubEnricher(EnrichmentResult(summary="s"))
    build_enriched_from_raw(
        "evidence/fig.txt", (root / "evidence" / "fig.txt").read_text(), enricher, base=base
    )
    # the note is delivered to the enricher as ground truth (the extraction change itself
    # is asserted through a live doc by casework)
    assert enricher.seen is not None
    assert enricher.seen.metadata.notes is not None
    assert "OCR noise" in enricher.seen.metadata.notes


# --- (c) per-file no_index on ONE file while siblings stay indexed ------------------


def test_acceptance_c_per_file_no_index_isolates_one_file(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    folder = root / "evidence" / "bundle"
    folder.mkdir(parents=True)
    (folder / "sealed.txt").write_text("sealed content")
    (folder / "sealed.txt.metadata.yaml").write_text(
        "no_index: true\nno_index_reason: CPR 32.12 — witness statement\n"
    )
    (folder / "open.txt").write_text("open content")  # sibling, no sidecar

    manifest = _manifest_for(root, tmp_path)
    sealed = _base_for(manifest, root, "evidence/bundle/sealed.txt")
    open_ = _base_for(manifest, root, "evidence/bundle/open.txt")
    assert sealed.no_index is True
    assert open_.no_index is False  # the sibling is NOT restricted

    indexer = _CapturingIndexer()
    enricher = _StubEnricher(EnrichmentResult(summary="s"))
    for rp, base in (("evidence/bundle/sealed.txt", sealed), ("evidence/bundle/open.txt", open_)):
        doc = build_enriched_from_raw(rp, (root / rp).read_text(), enricher, base=base)
        indexer.write(doc)
    # only the sibling was indexed; the sealed file was refused
    assert indexer.indexed == ["evidence/bundle/open.txt"]


# --- (d) method + claim_source correction -----------------------------------------


def test_acceptance_d_method_and_claim_source_correction(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "analysis.txt").write_text("Our analysis asserts the SI applies.")
    (root / "evidence" / "analysis.txt.metadata.yaml").write_text(
        "claim_source: Defence team\n"
        "method: opened legislation.gov.uk XML for SI 2020/759, diffed 2026-08-07\n"
    )
    manifest = _manifest_for(root, tmp_path)
    base = _base_for(manifest, root, "evidence/analysis.txt")
    assert base.method.startswith("opened legislation.gov.uk")

    # the enricher attributes the claim to the wrong speaker; claim_source must override
    enricher = _StubEnricher(
        EnrichmentResult(
            summary="s",
            claims=[Claim(subject="SI 2020/759", predicate="applies", object="here",
                          asserted_by="Some Witness")],
        )
    )
    doc = build_enriched_from_raw(
        "evidence/analysis.txt", (root / "evidence" / "analysis.txt").read_text(),
        enricher, base=base,
    )
    # find_claims would attribute every claim to the corrected claim_source
    assert doc.metadata.claims[0].asserted_by == "Defence team"
    es = to_es_document(doc)
    assert es["method"].startswith("opened legislation.gov.uk")


# --- (e) a caveat note -------------------------------------------------------------


def test_acceptance_e_caveat_note(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "scan.txt").write_text("A scanned document with garbled figures.")
    caveat = "Scanned: treat garbled figures as OCR noise, not the witness's numbers."
    (root / "evidence" / "scan.txt.metadata.yaml").write_text(
        __import__("yaml").safe_dump({"notes": caveat})
    )
    manifest = _manifest_for(root, tmp_path)
    base = _base_for(manifest, root, "evidence/scan.txt")

    enricher = _StubEnricher(EnrichmentResult(summary="s"))
    doc = build_enriched_from_raw(
        "evidence/scan.txt", (root / "evidence" / "scan.txt").read_text(), enricher, base=base
    )
    assert doc.metadata.notes == caveat
    # the caveat is visible on the document, fenced so it is never read as its own words
    assert f"{ANNOTATION_OPEN}\n{caveat}" in doc.markdown
