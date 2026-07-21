"""The sink interface (M1) and the concrete sinks (M4)."""

from goldberg_system.sinks.base import EnrichedDocument, Sink, SinkResult
from goldberg_system.sinks.elasticsearch_indexer import (
    INDEX_MAPPING,
    ElasticsearchIndexer,
    ensure_index,
    to_es_document,
)
from goldberg_system.sinks.extracted_writer import ExtractedRepoWriter

__all__ = [
    "Sink",
    "EnrichedDocument",
    "SinkResult",
    "ElasticsearchIndexer",
    "ExtractedRepoWriter",
    "INDEX_MAPPING",
    "ensure_index",
    "to_es_document",
]
