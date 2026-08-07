"""Direct-Docling bulk re-ingest (M8 fix).

Walks the provenance manifest, reads each file from goldberg-raw, extracts it via
docling-serve directly (bypassing Papra's broken extraction), enriches it with real
manifest provenance (raw_path/raw_commit/matters/raw_sha256, joined by SHA-256), and
writes it to the sinks — emitting a pipeline audit event at each step.

I/O-bound (Docling wait, OpenAI, ES), so it runs ``workers`` documents concurrently
via threads; Docling processes async convert jobs in parallel server-side.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.extract.docling_client import DoclingClient, DoclingError
from goldberg_system.migrate.manifest import Manifest, entry_is_no_index
from goldberg_system.observability.events import PipelineEvent, safe_emit
from goldberg_system.pipeline import build_enriched_from_raw, write_to_sinks
from goldberg_system.sinks.base import Sink

# Docling cannot extract text from audio/video — skip (they carry no OCR-able text).
_SKIP_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".ogg"}


@dataclass
class ReingestReport:
    processed: int = 0
    indexed: int = 0
    skipped_empty: int = 0
    skipped_media: int = 0
    skipped_indexed: int = 0  # already in the target index (resume)
    skipped_no_index: int = 0  # restricted subtree (no_index) — never index
    missing_file: int = 0
    failures: int = 0

    def tally(self, status: str) -> None:
        self.processed += 1
        if status == "indexed":
            self.indexed += 1
        elif status == "skipped-empty":
            self.skipped_empty += 1
        elif status == "skipped-media":
            self.skipped_media += 1
        elif status == "skipped-indexed":
            self.skipped_indexed += 1
        elif status == "skipped-no-index":
            self.skipped_no_index += 1
        elif status == "missing":
            self.missing_file += 1
        else:  # failed / sink-failed / error
            self.failures += 1


def reingest_from_raw(
    raw_root: Path | str,
    manifest: Manifest,
    docling: DoclingClient,
    enricher: EnrichmentAdapter,
    sinks: list[Sink],
    *,
    events: Any = None,
    run_id: str | None = None,
    max_docs: int | None = None,
    only: set[str] | None = None,
    skip_shas: set[str] | None = None,
    workers: int = 1,
    on_doc: Callable[[str, str], None] | None = None,
) -> ReingestReport:
    """Extract (Docling) → enrich → index every manifested file from ``raw_root``.

    ``only`` restricts to specific raw_paths (test injections); ``max_docs`` caps the
    count; ``workers`` processes that many documents concurrently. ``skip_shas`` are
    SHA-256s already in the target index — skipped without re-extracting (resume).
    """
    root = Path(raw_root)
    skip_shas = skip_shas or set()

    def emit(stage: str, status: str, sha: str, raw_path: str, **kw: Any) -> None:
        if events is None:
            return
        safe_emit(
            events,
            PipelineEvent.make(
                "reingest",
                stage,
                status,
                run_id=run_id,
                sha256=sha,
                raw_path=raw_path,
                **kw,
            ),
        )

    def process_one(sha: str, entry: dict) -> tuple[str, str]:
        """Process one document; return (raw_path, status)."""
        raw_path = entry.get("raw_path", "")
        if sha in skip_shas:  # resume: already indexed, don't re-extract
            return raw_path, "skipped-indexed"
        if entry_is_no_index(entry):  # restricted subtree — never extract/enrich/index
            emit(
                "received",
                "skipped",
                sha,
                raw_path,
                reason=f"no_index: {entry.get('no_index_reason') or '(no reason given)'}",
            )
            return raw_path, "skipped-no-index"
        if Path(raw_path).suffix.lower() in _SKIP_EXT:
            emit(
                "extracted", "skipped", sha, raw_path, reason="media (no OCR-able text)"
            )
            return raw_path, "skipped-media"

        path = root / raw_path
        if not path.is_file():
            emit(
                "received",
                "failed",
                sha,
                raw_path,
                reason="file missing in goldberg-raw",
            )
            return raw_path, "missing"
        emit("received", "ok", sha, raw_path)

        try:
            content = docling.convert_file(path)
        except DoclingError as exc:
            emit(
                "extracted",
                "failed",
                sha,
                raw_path,
                reason="docling extraction",
                error=str(exc),
            )
            return raw_path, f"extract-failed: {exc}"

        if not content.strip():
            emit("extracted", "skipped", sha, raw_path, reason="empty extraction")
            return raw_path, "skipped-empty"

        try:
            base = manifest.base_for_sha(sha)
            assert base is not None  # sha came from the manifest
            document = build_enriched_from_raw(raw_path, content, enricher, base=base)
            results = write_to_sinks(document, sinks)
            if all(r.ok for r in results):
                emit("indexed", "ok", sha, raw_path, doc_id=document.doc_id)
                return raw_path, "indexed"
            detail = "; ".join(r.detail for r in results if not r.ok and r.detail)
            emit(
                "indexed",
                "failed",
                sha,
                raw_path,
                doc_id=document.doc_id,
                reason="sink write failed",
                error=detail,
            )
            return raw_path, "sink-failed"
        except Exception as exc:  # noqa: BLE001 - one bad doc must not stop the run
            emit(
                "enriched",
                "failed",
                sha,
                raw_path,
                reason="enrich/index error",
                error=str(exc),
            )
            return raw_path, f"error: {exc}"

    # select the work list (respect only/max) before dispatching
    items: list[tuple[str, dict]] = []
    for sha, entry in manifest.items():
        if only is not None and entry.get("raw_path", "") not in only:
            continue
        items.append((sha, entry))
        if max_docs is not None and len(items) >= max_docs:
            break

    report = ReingestReport()

    def record(raw_path: str, status: str) -> None:
        report.tally(status)
        if on_doc:
            on_doc(raw_path, status)

    if workers <= 1:
        for sha, entry in items:
            rp, status = process_one(sha, entry)
            record(rp, status)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_one, sha, entry): entry for sha, entry in items
            }
            for fut in as_completed(futures):
                rp, status = fut.result()
                record(rp, status)

    return report
