"""Assemble an enriched extracted document (ADR 0004).

Merge an :class:`EnrichmentResult` into the document's base metadata and render the
result as a markdown-with-frontmatter document (frontmatter = metadata, body =
extracted text). The base metadata supplies provenance / matters / handling flags
(from the raw side + folder defaults); enrichment supplies summary / long_summary /
keywords / entities / author / classification / attributed claims.
"""

from __future__ import annotations

from goldberg_system.enrichment.adapter import EnrichmentResult
from goldberg_system.metadata.frontmatter import to_frontmatter_document
from goldberg_system.metadata.schema import DocumentMetadata


def _union(a: list[str], b: list[str]) -> list[str]:
    out = list(a)
    for item in b:
        if item not in out:
            out.append(item)
    return out


def assemble_enriched_document(
    base: DocumentMetadata, result: EnrichmentResult, body: str
) -> str:
    """Return the enriched extracted document (frontmatter + body)."""
    updates: dict[str, object] = {
        "summary": result.summary,
        "keywords": _union(base.keywords, result.keywords),
        "entities": _union(base.entities, result.entities),
        "claims": result.claims,
    }
    if result.long_summary is not None:
        updates["long_summary"] = result.long_summary
    if result.author is not None and base.author is None:
        updates["author"] = result.author
    if result.document_type is not None and base.document_type is None:
        updates["document_type"] = result.document_type
    merged = base.model_copy(update=updates)
    return to_frontmatter_document(merged, body)
