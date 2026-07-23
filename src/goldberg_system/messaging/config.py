"""Messaging configuration for the NATS JetStream boundary (WP02).

A single frozen :class:`MessagingConfig` resolves the broker URL and the
JetStream naming from the environment. Naming is deliberately aligned with the
downstream ``nats-es-archive`` mission: stream ``GOLDBERG``, subject namespace
``goldberg.>``, commit trigger ``goldberg.raw.commit``, durable consumer
``ingest-processor``. Keep these defaults stable — other services bind to them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Environment variable names. ``NATS_URL`` follows the conventional NATS client
# variable; the rest use the project's ``GOLDBERG_*`` override pattern.
_ENV_NATS_URL = "NATS_URL"
_ENV_STREAM = "GOLDBERG_NATS_STREAM"
_ENV_SUBJECT_PREFIX = "GOLDBERG_NATS_SUBJECT_PREFIX"
_ENV_COMMIT_SUBJECT = "GOLDBERG_NATS_COMMIT_SUBJECT"
_ENV_DURABLE = "GOLDBERG_NATS_DURABLE"
_ENV_MAX_DELIVER = "GOLDBERG_NATS_MAX_DELIVER"
_ENV_ACK_WAIT = "GOLDBERG_NATS_ACK_WAIT"
_ENV_DEDUP_WINDOW = "GOLDBERG_NATS_DEDUP_WINDOW"

_DEFAULT_NATS_URL = "nats://192.168.86.31:4222"
_DEFAULT_STREAM = "GOLDBERG"
_DEFAULT_SUBJECT_PREFIX = "goldberg"
_DEFAULT_COMMIT_SUBJECT = "goldberg.raw.commit"
_DEFAULT_DURABLE = "ingest-processor"
_DEFAULT_MAX_DELIVER = 5
# ~5 minutes (research.md R3): must comfortably exceed a slow Docling OCR + enrich so
# the server does not redeliver a message that is still being processed, and so a
# short dependency outage does not burn all deliveries before recovery. The previous
# 30s default triggered premature duplicate redelivery / DLQ under normal OCR latency.
# Env-overridable via GOLDBERG_NATS_ACK_WAIT.
_DEFAULT_ACK_WAIT_SECONDS = 300.0
_DEFAULT_DEDUP_WINDOW_SECONDS = 120.0


@dataclass(frozen=True)
class MessagingConfig:
    """Resolved NATS JetStream settings for the pipeline messaging boundary.

    Attributes:
        nats_url: NATS server URL (``NATS_URL``).
        stream: JetStream stream name that owns ``<subject_prefix>.>``.
        subject_prefix: Root of the pipeline subject namespace (``goldberg``).
        commit_subject: Subject the raw-commit trigger is published to.
        durable: Durable name of the ingest pull consumer.
        max_deliver: Max redelivery attempts before a message is dead-lettered.
        ack_wait_seconds: How long the server waits for an ack before redelivery.
        dedup_window_seconds: Stream duplicate window; dedup is keyed on the
            ``Nats-Msg-Id`` header (set to the commit sha by the publisher).
    """

    nats_url: str = _DEFAULT_NATS_URL
    stream: str = _DEFAULT_STREAM
    subject_prefix: str = _DEFAULT_SUBJECT_PREFIX
    commit_subject: str = _DEFAULT_COMMIT_SUBJECT
    durable: str = _DEFAULT_DURABLE
    max_deliver: int = _DEFAULT_MAX_DELIVER
    ack_wait_seconds: float = _DEFAULT_ACK_WAIT_SECONDS
    dedup_window_seconds: float = _DEFAULT_DEDUP_WINDOW_SECONDS

    @property
    def stream_subjects(self) -> tuple[str, ...]:
        """The subject(s) the stream captures: the whole ``<prefix>.>`` namespace."""
        return (f"{self.subject_prefix}.>",)

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "MessagingConfig":
        """Resolve a config from ``env`` (defaults to ``os.environ``).

        Every field falls back to its downstream-aligned default when the
        corresponding variable is unset.
        """
        src: Mapping[str, str] = os.environ if env is None else env
        return cls(
            nats_url=src.get(_ENV_NATS_URL, _DEFAULT_NATS_URL),
            stream=src.get(_ENV_STREAM, _DEFAULT_STREAM),
            subject_prefix=src.get(_ENV_SUBJECT_PREFIX, _DEFAULT_SUBJECT_PREFIX),
            commit_subject=src.get(_ENV_COMMIT_SUBJECT, _DEFAULT_COMMIT_SUBJECT),
            durable=src.get(_ENV_DURABLE, _DEFAULT_DURABLE),
            max_deliver=int(src.get(_ENV_MAX_DELIVER, _DEFAULT_MAX_DELIVER)),
            ack_wait_seconds=float(
                src.get(_ENV_ACK_WAIT, _DEFAULT_ACK_WAIT_SECONDS)
            ),
            dedup_window_seconds=float(
                src.get(_ENV_DEDUP_WINDOW, _DEFAULT_DEDUP_WINDOW_SECONDS)
            ),
        )
