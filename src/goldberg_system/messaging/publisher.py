"""Publish the raw-commit trigger onto JetStream (WP02).

Part of the single messaging boundary — imports ``nats`` only for the standard
``Nats-Msg-Id`` header name. The async model matches :mod:`client`: the public
function is ``async`` and runs on the caller's event loop.
"""

from __future__ import annotations

import json
from typing import Any

from nats.js import JetStreamContext
from nats.js.api import Header

from goldberg_system.messaging.config import MessagingConfig

# The standard JetStream dedup header ("Nats-Msg-Id"). Setting it to the commit
# sha makes redundant publishes of the same commit idempotent within the stream's
# duplicate window.
_MSG_ID_HEADER = Header.MSG_ID.value


async def publish_commit(
    js: JetStreamContext,
    config: MessagingConfig,
    sha: str,
    ts: str,
    source: str,
) -> Any:
    """Publish a raw-commit trigger and return the JetStream ack.

    Publishes ``{"sha", "ts", "source"}`` as JSON to ``config.commit_subject`` with
    ``Nats-Msg-Id`` set to ``sha`` for content-addressed deduplication.

    Args:
        js: JetStream context (or an injected fake in tests).
        config: Resolved messaging config providing the commit subject.
        sha: Raw-commit sha — the dedup id and message body ``sha``.
        ts: ISO-8601 UTC timestamp of the commit.
        source: Producer identifier (e.g. ``"watcher"`` or ``"papra"``).

    Returns:
        The publish ack from JetStream.

    Raises:
        Exception: any error from the broker propagates unchanged; the caller
            decides whether a failed publish is fatal.
    """
    payload = json.dumps({"sha": sha, "ts": ts, "source": source}).encode("utf-8")
    return await js.publish(
        config.commit_subject,
        payload,
        headers={_MSG_ID_HEADER: sha},
    )
