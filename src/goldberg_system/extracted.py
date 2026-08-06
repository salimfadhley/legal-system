"""Reconstruct an :class:`EnrichedDocument` from a persisted representation.

The pipeline writes enriched documents *out* to two places — Elasticsearch
(:func:`sinks.elasticsearch_indexer.to_es_document`) and the goldberg-extracted git
store (:func:`metadata.frontmatter.to_frontmatter_document`). This module holds the
**inverses**: rebuild the in-memory :class:`EnrichedDocument` from either, so we can

- populate goldberg-extracted from the ES index (``goldberg backfill-extracted``), and
- rebuild the ES index from goldberg-extracted (``goldberg reindex-from-extracted``),

both **without** re-extracting (Docling) or re-enriching (LLM). See ADR 0015.

``doc_id`` is not stored in the frontmatter (it is not a metadata field), so when
rebuilding from a file it is recomputed deterministically from ``raw_path`` + body —
exactly as the pipeline computed it originally (:func:`identity.compute_doc_id`).
"""

from __future__ import annotations

from goldberg_system.identity import compute_doc_id
from goldberg_system.metadata.frontmatter import parse_frontmatter_document
from goldberg_system.metadata.schema import DocumentMetadata
from goldberg_system.sinks.base import EnrichedDocument

# Keys written by ``to_es_document`` that are not ``DocumentMetadata`` fields.
_ES_ONLY = frozenset({"doc_id", "content", "content_hash"})


def enriched_from_es_source(
    source: dict, *, doc_id_fallback: str = ""
) -> EnrichedDocument:
    """Rebuild an :class:`EnrichedDocument` from an ES ``_source`` document.

    Inverse of ``to_es_document``: the body is ``content``, the metadata is every
    other field that belongs to :class:`DocumentMetadata` (unknown keys are ignored,
    so a future ES-only field cannot break the rebuild). ``doc_id`` comes from the
    source, falling back to ``doc_id_fallback`` (typically the ES ``_id``).
    """
    meta = DocumentMetadata.model_validate(
        {k: v for k, v in source.items() if k in DocumentMetadata.model_fields}
    )
    raw_path = meta.raw_path or source.get("raw_path") or doc_id_fallback
    return EnrichedDocument(
        doc_id=source.get("doc_id") or doc_id_fallback,
        raw_path=raw_path,
        raw_commit=source.get("raw_commit") or meta.raw_commit or "",
        markdown=source.get("content") or "",
        metadata=meta,
    )


def enriched_from_frontmatter(text: str) -> EnrichedDocument:
    """Rebuild an :class:`EnrichedDocument` from a goldberg-extracted ``.md`` file.

    Inverse of ``to_frontmatter_document``. ``raw_path`` must be present in the
    frontmatter (it is the document's identity anchor); ``doc_id`` is recomputed from
    ``raw_path`` + body so it matches what the pipeline originally assigned.
    """
    metadata, body = parse_frontmatter_document(text)
    if not metadata.raw_path:
        raise ValueError("cannot rebuild: frontmatter has no raw_path")
    return EnrichedDocument(
        doc_id=compute_doc_id(metadata.raw_path, body.encode("utf-8")),
        raw_path=metadata.raw_path,
        raw_commit=metadata.raw_commit or "",
        markdown=body,
        metadata=metadata,
    )
