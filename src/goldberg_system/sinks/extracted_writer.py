"""The goldberg-extracted repository writer sink (M4).

Persists each enriched document as a markdown-with-frontmatter file (ADR 0004) at
a path mirroring the raw file's path, under the goldberg-extracted root. Writing is
idempotent (same path → overwrite).
"""

from __future__ import annotations

from pathlib import Path

from goldberg_system.metadata.frontmatter import to_frontmatter_document
from goldberg_system.sinks.base import EnrichedDocument, SinkResult


class ExtractedRepoWriter:
    """A :class:`~goldberg_system.sinks.base.Sink` that writes extracted `.md` files."""

    def __init__(self, extracted_root: Path | str) -> None:
        self.extracted_root = Path(extracted_root)

    @property
    def name(self) -> str:
        return "goldberg-extracted"

    def target_path(self, document: EnrichedDocument) -> Path:
        """The extracted `.md` path, mirroring the raw file path.

        The ``raw_path`` is Elasticsearch-controlled data, so a hostile/corrupt value
        like ``../../escape`` must never let a write land OUTSIDE ``extracted_root``.
        We resolve the candidate and assert containment (``relative_to``); a path that
        escapes the root raises :class:`ValueError`, which :meth:`write` turns into a
        failed :class:`SinkResult` rather than writing outside the repo.
        """
        rel = document.metadata.raw_path or document.raw_path or document.doc_id
        root = self.extracted_root.resolve()
        candidate = (root / f"{rel}.md").resolve()
        # Raises ValueError if the resolved candidate is not contained within root
        # (i.e. the raw_path traversed out of the extracted repository).
        candidate.relative_to(root)
        return candidate

    def write(self, document: EnrichedDocument) -> SinkResult:
        try:
            dest = self.target_path(document)
        except ValueError:
            rel = document.metadata.raw_path or document.raw_path or document.doc_id
            return SinkResult(
                sink=self.name,
                ok=False,
                detail=f"refusing path-traversal write outside extracted_root: {rel!r}",
            )
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                to_frontmatter_document(document.metadata, document.markdown),
                encoding="utf-8",
            )
            return SinkResult(sink=self.name, ok=True, detail=str(dest))
        except OSError as exc:
            return SinkResult(sink=self.name, ok=False, detail=str(exc))
