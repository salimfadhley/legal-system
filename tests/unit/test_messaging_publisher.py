"""Unit tests for the commit publisher (WP02, FR-001/FR-003).

Uses an **injected fake** JetStream that records what ``publish`` was called with —
no live broker. Async is driven with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from goldberg_system.messaging import MessagingConfig, publish_commit


class RecordingJetStream:
    """Records the single ``publish`` call and returns a canned ack."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.ack = SimpleNamespace(stream="GOLDBERG", seq=42, duplicate=False)

    async def publish(
        self,
        subject: str,
        payload: bytes = b"",
        timeout: float | None = None,
        stream: str | None = None,
        headers: dict[str, Any] | None = None,
        msg_ttl: float | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "subject": subject,
                "payload": payload,
                "headers": headers,
                "stream": stream,
            }
        )
        return self.ack


class FailingJetStream:
    """A JetStream whose ``publish`` always raises (broker rejected the message)."""

    async def publish(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no stream response")


def test_publish_commit_sets_msg_id_header_to_sha() -> None:
    js = RecordingJetStream()
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(
        publish_commit(js, cfg, sha="deadbeef", ts="2026-07-23T12:00:00Z", source="watcher")
    )

    headers = js.calls[0]["headers"]
    assert headers is not None
    # Dedup id is the raw commit sha, keyed on the standard JetStream header.
    assert headers["Nats-Msg-Id"] == "deadbeef"


def test_publish_commit_subject_and_json_body() -> None:
    js = RecordingJetStream()
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(
        publish_commit(
            js, cfg, sha="abc123", ts="2026-07-23T12:34:56Z", source="watcher"
        )
    )

    call = js.calls[0]
    assert call["subject"] == "goldberg.raw.commit"
    body = json.loads(call["payload"].decode("utf-8"))
    assert body == {"sha": "abc123", "ts": "2026-07-23T12:34:56Z", "source": "watcher"}


def test_publish_commit_returns_ack() -> None:
    js = RecordingJetStream()
    cfg = MessagingConfig.from_env(env={})

    ack = asyncio.run(
        publish_commit(js, cfg, sha="abc123", ts="2026-07-23T12:00:00Z", source="watcher")
    )

    assert ack is js.ack
    assert ack.seq == 42


def test_publish_commit_uses_configured_subject_override() -> None:
    js = RecordingJetStream()
    cfg = MessagingConfig.from_env(env={"GOLDBERG_NATS_COMMIT_SUBJECT": "gbtest.raw.commit"})

    asyncio.run(
        publish_commit(js, cfg, sha="abc123", ts="2026-07-23T12:00:00Z", source="papra")
    )

    assert js.calls[0]["subject"] == "gbtest.raw.commit"


def test_publish_commit_raises_on_failure() -> None:
    js = FailingJetStream()
    cfg = MessagingConfig.from_env(env={})

    with pytest.raises(RuntimeError):
        asyncio.run(
            publish_commit(js, cfg, sha="abc123", ts="2026-07-23T12:00:00Z", source="watcher")
        )
