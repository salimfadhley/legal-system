"""NATS JetStream connection, stream provisioning, and a durable pull consumer.

**Async model (chosen and consistent across this package):** the public API is
``async``. ``nats-py`` is natively asyncio and a durable JetStream connection plus
its pull subscription must live on one long-running event loop — a persistent
service (WP03) opens the connection once and drives ``fetch``/``ack`` in a loop.
Wrapping each call in ``asyncio.run`` would tear the connection down between
operations, so we deliberately do **not** offer a synchronous facade. Callers run
inside their own event loop (a service ``asyncio.run(main())`` at the top, or
``asyncio.run`` per-call in tests).

This module is the *only* place in the codebase that imports ``nats``; everything
else depends on these helpers so the broker stays swappable and unit-testable via
injected fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import NotFoundError

from goldberg_system.messaging.config import MessagingConfig

# Redelivery backoff schedule (seconds), indexed by delivery attempt (research.md R3).
# A transient dependency outage (Docling down, slow OCR) must NOT redeliver
# immediately and burn all deliveries in seconds — we back off increasingly, capped.
# It is applied in two complementary ways so redelivery timing is uniform whatever the
# failure mode:
#   * as an explicit ``msg.nak(delay=...)`` — the deliberate-NAK path (the processor
#     decided the commit failed transiently); and
#   * as the consumer ``backoff`` schedule — the server-side path that governs
#     ack_wait *timeout* redeliveries (e.g. a crash where we never got to NAK).
# Kept shorter than ``max_deliver`` (nats-server requires len(backoff) < max_deliver).
_REDELIVERY_BACKOFF_SECONDS: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)


def _backoff_delay(msg: Any) -> float:
    """Backoff seconds for this message's next redelivery, by delivery count.

    Uses ``msg.metadata.num_delivered`` (1-based) to index the schedule, clamped to
    its last (capped) step. Falls back to the first step when the delivery count is
    unavailable — a NAK must always carry a non-zero delay.
    """
    try:
        delivered = int(msg.metadata.num_delivered)
    except Exception:  # noqa: BLE001 - unknown/absent delivery count → first step
        return _REDELIVERY_BACKOFF_SECONDS[0]
    idx = max(0, min(delivered - 1, len(_REDELIVERY_BACKOFF_SECONDS) - 1))
    return _REDELIVERY_BACKOFF_SECONDS[idx]


def _consumer_backoff(config: MessagingConfig) -> list[float]:
    """The server-side backoff schedule, truncated so ``len < max_deliver``.

    nats-server rejects a consumer whose backoff list is not shorter than
    ``max_deliver``; truncating keeps a small ``--max-deliver`` override valid.
    """
    limit = max(1, config.max_deliver - 1)
    return list(_REDELIVERY_BACKOFF_SECONDS[:limit])


@dataclass
class MessagingConnection:
    """A thin owning wrapper over the NATS client and its JetStream context.

    Hold this for the lifetime of the service; call :meth:`close` on shutdown.
    ``ensure_stream`` / ``pull_consumer`` / ``publish_commit`` all take the bare
    ``js`` so they remain trivially fakeable in unit tests.
    """

    nc: NATSClient
    js: JetStreamContext

    async def close(self) -> None:
        """Drain and close the underlying connection."""
        await self.nc.drain()


async def connect(config: MessagingConfig) -> MessagingConnection:
    """Open a NATS connection and return it wrapped with its JetStream context."""
    nc = await nats.connect(config.nats_url)
    js = nc.jetstream()
    return MessagingConnection(nc=nc, js=js)


async def ensure_stream(
    js: JetStreamContext, config: MessagingConfig
) -> Any:
    """Idempotently ensure the ``GOLDBERG`` stream exists over ``goldberg.>``.

    Safe to call on every startup: if the stream is already present it is left
    untouched (no reconfigure), otherwise it is created with ``limits`` retention
    and a duplicate window so publishes carrying ``Nats-Msg-Id`` are deduplicated.
    Returns the stream info (existing or freshly created).
    """
    try:
        return await js.stream_info(config.stream)
    except NotFoundError:
        stream_config = StreamConfig(
            name=config.stream,
            subjects=list(config.stream_subjects),
            retention=RetentionPolicy.LIMITS,
            duplicate_window=config.dedup_window_seconds,
        )
        return await js.add_stream(stream_config)


@dataclass
class PullConsumer:
    """A durable pull consumer plus broker-agnostic ack/nak/term helpers.

    The processor (WP03) uses :meth:`fetch` to pull a batch and then dispatches
    each message to :meth:`ack` (done), :meth:`nak` (retry later), or
    :meth:`term` (give up — do not redeliver), never touching ``nats`` directly.
    """

    subscription: JetStreamContext.PullSubscription

    async def fetch(
        self, batch: int = 1, timeout: float | None = 5
    ) -> list[Any]:
        """Pull up to ``batch`` messages, waiting at most ``timeout`` seconds."""
        return await self.subscription.fetch(batch, timeout)

    async def ack(self, msg: Any) -> None:
        """Acknowledge successful processing (removes the message)."""
        await msg.ack()

    async def nak(self, msg: Any) -> None:
        """Negatively acknowledge — request redelivery after a backoff delay.

        The delay grows with the delivery count (``_REDELIVERY_BACKOFF_SECONDS``,
        capped) so a transient outage backs off instead of exhausting ``max_deliver``
        in seconds (research.md R3), rather than an immediate redelivery.
        """
        await msg.nak(delay=_backoff_delay(msg))

    async def term(self, msg: Any) -> None:
        """Terminate — stop redelivering this message (poison / unprocessable)."""
        await msg.term()


async def pull_consumer(
    js: JetStreamContext, config: MessagingConfig
) -> PullConsumer:
    """Create (or bind) the durable pull consumer for the commit subject.

    The consumer uses explicit ack, bounded redelivery (``max_deliver``), an
    ``ack_wait`` timeout, a redelivery ``backoff`` schedule (research.md R3), and
    filters to ``commit_subject`` so only raw-commit triggers are delivered.
    """
    consumer_config = ConsumerConfig(
        durable_name=config.durable,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=config.ack_wait_seconds,
        max_deliver=config.max_deliver,
        backoff=_consumer_backoff(config),
        filter_subject=config.commit_subject,
    )
    subscription = await js.pull_subscribe(
        subject=config.commit_subject,
        durable=config.durable,
        stream=config.stream,
        config=consumer_config,
    )
    return PullConsumer(subscription=subscription)
