"""NATS event contracts for the pipeline."""

from goldberg_system.events.contracts import (
    EventSource,
    IndexedEvent,
    RawIngestedEvent,
)

__all__ = ["RawIngestedEvent", "IndexedEvent", "EventSource"]
