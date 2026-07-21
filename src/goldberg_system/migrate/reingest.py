"""Direct-Docling bulk re-ingest (M8 fix).

Walks the provenance manifest, reads each file from goldberg-raw, extracts it via
docling-serve directly (bypassing Papra's broken extraction), enriches it with real
manifest provenance (raw_path/raw_commit/matters/raw_sha256, joined by SHA-256), and
writes it to the sinks — emitting a pipeline audit event at each step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from goldberg_system.enrichment.adapter import EnrichmentAdapter
from goldberg_system.extract.docling_client import DoclingClient, DoclingError
from goldberg_system.migrate.manifest import Manifest
from goldberg_system.observability.events import PipelineEvent, safe_emit
from goldberg_system.pipeline import build_enriched_from_raw, write_to_sinks
from goldberg_system.sinks.base import Sink

# Docling cannot extract text from audio/video — skip (they carry no OCR-able text).
_SKIP_EXT = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4a",
    ".mp3",
    ".wav",
    ".ogg",
}


@dataclass
class ReingestReport:
    processed: int = 0
    indexed: int = 0
    skipped_empty: int = 0
    skipped_media: int = 0
    missing_file: int = 0
    failures: int = 0


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
    on_doc: Callable[[str, str], None] | None = None,
) -> ReingestReport:
    """Extract (Docling) → enrich → index every manifested file from ``raw_root``.

    ``only`` restricts to specific raw_paths (for test injections); ``max_docs`` caps
    the count.
    """
    root = Path(raw_root)
    report = ReingestReport()

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

    for sha, entry in manifest.items():
        raw_path = entry.get("raw_path", "")
        if only is not None and raw_path not in only:
            continue
        if max_docs is not None and report.processed >= max_docs:
            break
        report.processed += 1

        if Path(raw_path).suffix.lower() in _SKIP_EXT:
            report.skipped_media += 1
            emit(
                "extracted", "skipped", sha, raw_path, reason="media (no OCR-able text)"
            )
            if on_doc:
                on_doc(raw_path, "skipped-media")
            continue

        path = root / raw_path
        if not path.is_file():
            report.missing_file += 1
            emit(
                "received",
                "failed",
                sha,
                raw_path,
                reason="file missing in goldberg-raw",
            )
            if on_doc:
                on_doc(raw_path, "missing")
            continue
        emit("received", "ok", sha, raw_path)

        try:
            content = docling.convert_file(path)
        except DoclingError as exc:
            report.failures += 1
            emit(
                "extracted",
                "failed",
                sha,
                raw_path,
                reason="docling extraction",
                error=str(exc),
            )
            if on_doc:
                on_doc(raw_path, f"extract-failed: {exc}")
            continue

        if not content.strip():
            report.skipped_empty += 1
            emit("extracted", "skipped", sha, raw_path, reason="empty extraction")
            if on_doc:
                on_doc(raw_path, "empty")
            continue

        try:
            base = manifest.base_for_sha(sha)
            assert base is not None  # sha came from the manifest
            document = build_enriched_from_raw(raw_path, content, enricher, base=base)
            results = write_to_sinks(document, sinks)
            if all(r.ok for r in results):
                report.indexed += 1
                emit("indexed", "ok", sha, raw_path, doc_id=document.doc_id)
                if on_doc:
                    on_doc(raw_path, "indexed")
            else:
                report.failures += 1
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
                if on_doc:
                    on_doc(raw_path, "sink-failed")
        except Exception as exc:  # noqa: BLE001 - one bad doc must not stop the run
            report.failures += 1
            emit(
                "enriched",
                "failed",
                sha,
                raw_path,
                reason="enrich/index error",
                error=str(exc),
            )
            if on_doc:
                on_doc(raw_path, f"error: {exc}")

    return report
