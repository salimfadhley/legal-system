"""The sink interface that M4 writers implement."""

from goldberg_system.sinks.base import EnrichedDocument, Sink, SinkResult

__all__ = ["Sink", "EnrichedDocument", "SinkResult"]
