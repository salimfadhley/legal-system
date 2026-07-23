"""The NATS JetStream messaging boundary for the ingestion pipeline (WP02).

This package is the **single seam** through which the codebase talks to NATS
JetStream — it is the only place that imports ``nats-py``. The rest of the system
depends on these helpers so the broker stays injectable and unit-testable.

Async model: the public API is ``async`` (see :mod:`goldberg_system.messaging.client`
for the rationale — a durable JetStream connection lives on one long-running event
loop; there is deliberately no synchronous facade).

Public surface:
    * :class:`MessagingConfig` — env-resolved, frozen JetStream settings.
    * :func:`connect` / :class:`MessagingConnection` — open + own a connection.
    * :func:`ensure_stream` — idempotently provision the ``GOLDBERG`` stream.
    * :func:`pull_consumer` / :class:`PullConsumer` — durable pull consumer with
      ``fetch`` and ``ack``/``nak``/``term`` helpers.
    * :func:`publish_commit` — publish the raw-commit trigger with a dedup id.
"""

from goldberg_system.messaging.client import (
    MessagingConnection,
    PullConsumer,
    connect,
    ensure_stream,
    pull_consumer,
)
from goldberg_system.messaging.config import MessagingConfig
from goldberg_system.messaging.publisher import publish_commit

__all__ = [
    "MessagingConfig",
    "MessagingConnection",
    "PullConsumer",
    "connect",
    "ensure_stream",
    "pull_consumer",
    "publish_commit",
]
