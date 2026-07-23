"""Unit tests for the messaging config + client boundary (WP02, FR-001/FR-003).

Everything here runs against **injected fakes** — no live NATS broker. Async public
functions are driven with ``asyncio.run`` so the suite needs no pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from nats.js.api import AckPolicy, RetentionPolicy
from nats.js.errors import NotFoundError

from goldberg_system.messaging import (
    MessagingConfig,
    ensure_stream,
    pull_consumer,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeJetStream:
    """A minimal stand-in for ``nats.js.JetStreamContext``.

    Records the JetStream management calls the client makes and lets a test seed
    which streams already exist (so ``stream_info`` raises ``NotFoundError`` until
    a stream has been created).
    """

    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self._streams: set[str] = set(existing)
        self.add_stream_calls: list[Any] = []
        self.stream_info_calls: list[str] = []
        self.pull_subscribe_calls: list[dict[str, Any]] = []

    async def stream_info(
        self, name: str, subjects_filter: str | None = None
    ) -> SimpleNamespace:
        self.stream_info_calls.append(name)
        if name not in self._streams:
            raise NotFoundError()
        return SimpleNamespace(config=SimpleNamespace(name=name))

    async def add_stream(
        self, config: Any = None, **params: Any
    ) -> SimpleNamespace:
        self.add_stream_calls.append(config)
        self._streams.add(config.name)
        return SimpleNamespace(config=config)

    async def pull_subscribe(
        self,
        subject: str,
        durable: str | None = None,
        stream: str | None = None,
        config: Any = None,
        **kwargs: Any,
    ) -> "FakePullSubscription":
        self.pull_subscribe_calls.append(
            {
                "subject": subject,
                "durable": durable,
                "stream": stream,
                "config": config,
            }
        )
        return FakePullSubscription()


class FakePullSubscription:
    """Stand-in for ``JetStreamContext.PullSubscription``."""

    def __init__(self) -> None:
        self.fetch_calls: list[tuple[int, float | None]] = []
        self._messages = [FakeMsg(b"one"), FakeMsg(b"two")]

    async def fetch(
        self, batch: int = 1, timeout: float | None = 5
    ) -> list["FakeMsg"]:
        self.fetch_calls.append((batch, timeout))
        return self._messages[:batch]


class FakeMsg:
    """Stand-in for a JetStream ``Msg`` — records ack/nak/term dispatch."""

    def __init__(self, data: bytes = b"", num_delivered: int | None = None) -> None:
        self.data = data
        self.acked = False
        self.naked = False
        self.termed = False
        self.nak_delay: float | None = None
        if num_delivered is not None:
            self.metadata = SimpleNamespace(num_delivered=num_delivered)

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.naked = True
        self.nak_delay = delay

    async def term(self) -> None:
        self.termed = True


# --------------------------------------------------------------------------- #
# MessagingConfig
# --------------------------------------------------------------------------- #
def test_config_defaults_match_downstream_naming() -> None:
    cfg = MessagingConfig.from_env(env={})
    assert cfg.nats_url == "nats://192.168.86.31:4222"
    assert cfg.stream == "GOLDBERG"
    assert cfg.subject_prefix == "goldberg"
    assert cfg.commit_subject == "goldberg.raw.commit"
    assert cfg.durable == "ingest-processor"
    # The stream captures the whole prefix wildcard, aligning with nats-es-archive.
    assert cfg.stream_subjects == ("goldberg.>",)


def test_config_default_ack_wait_is_production_safe() -> None:
    # FIX 2 (cycle 2): research.md R3 requires ~5 min ack_wait so a slow Docling
    # OCR (or a ~1-minute dependency outage) does not trigger premature redelivery
    # / burn all deliveries. The shipped 30s default was too aggressive.
    cfg = MessagingConfig.from_env(env={})
    assert cfg.ack_wait_seconds == 300.0


def test_config_ack_wait_still_env_overridable() -> None:
    cfg = MessagingConfig.from_env(env={"GOLDBERG_NATS_ACK_WAIT": "90"})
    assert cfg.ack_wait_seconds == 90.0


def test_config_env_overrides() -> None:
    env = {
        "NATS_URL": "nats://10.0.0.9:4222",
        "GOLDBERG_NATS_STREAM": "GOLDBERG_TEST",
        "GOLDBERG_NATS_SUBJECT_PREFIX": "gbtest",
        "GOLDBERG_NATS_COMMIT_SUBJECT": "gbtest.raw.commit",
        "GOLDBERG_NATS_DURABLE": "ingest-processor-test",
        "GOLDBERG_NATS_MAX_DELIVER": "9",
        "GOLDBERG_NATS_ACK_WAIT": "45",
        "GOLDBERG_NATS_DEDUP_WINDOW": "300",
    }
    cfg = MessagingConfig.from_env(env=env)
    assert cfg.nats_url == "nats://10.0.0.9:4222"
    assert cfg.stream == "GOLDBERG_TEST"
    assert cfg.subject_prefix == "gbtest"
    assert cfg.commit_subject == "gbtest.raw.commit"
    assert cfg.durable == "ingest-processor-test"
    assert cfg.max_deliver == 9
    assert cfg.ack_wait_seconds == 45.0
    assert cfg.dedup_window_seconds == 300.0
    assert cfg.stream_subjects == ("gbtest.>",)


def test_config_is_frozen() -> None:
    cfg = MessagingConfig.from_env(env={})
    try:
        cfg.stream = "OTHER"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001 - assert immutability, any raise is fine
        assert isinstance(exc, (AttributeError, TypeError))
    else:  # pragma: no cover - would mean the dataclass is mutable
        raise AssertionError("MessagingConfig should be frozen/immutable")


# --------------------------------------------------------------------------- #
# ensure_stream
# --------------------------------------------------------------------------- #
def test_ensure_stream_creates_when_absent() -> None:
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(ensure_stream(js, cfg))

    assert len(js.add_stream_calls) == 1
    created = js.add_stream_calls[0]
    assert created.name == "GOLDBERG"
    assert created.subjects == ["goldberg.>"]
    assert created.retention == RetentionPolicy.LIMITS
    # Dedup window keyed on Nats-Msg-Id is expressed as the stream duplicate window.
    assert created.duplicate_window == cfg.dedup_window_seconds


def test_ensure_stream_is_idempotent_called_twice_one_create() -> None:
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(ensure_stream(js, cfg))
    asyncio.run(ensure_stream(js, cfg))

    # Second call sees the stream already present -> no second add.
    assert len(js.add_stream_calls) == 1


def test_ensure_stream_noop_when_present() -> None:
    js = FakeJetStream(existing=("GOLDBERG",))
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(ensure_stream(js, cfg))

    assert js.add_stream_calls == []


# --------------------------------------------------------------------------- #
# pull_consumer + ack/nak/term
# --------------------------------------------------------------------------- #
def test_pull_consumer_configures_durable_explicit_ack() -> None:
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})

    asyncio.run(pull_consumer(js, cfg))

    assert len(js.pull_subscribe_calls) == 1
    call = js.pull_subscribe_calls[0]
    assert call["subject"] == "goldberg.raw.commit"
    assert call["durable"] == "ingest-processor"
    assert call["stream"] == "GOLDBERG"
    consumer_cfg = call["config"]
    assert consumer_cfg.durable_name == "ingest-processor"
    assert consumer_cfg.ack_policy == AckPolicy.EXPLICIT
    assert consumer_cfg.max_deliver == cfg.max_deliver
    assert consumer_cfg.ack_wait == cfg.ack_wait_seconds
    assert consumer_cfg.filter_subject == "goldberg.raw.commit"
    # FIX 2 (cycle 2): a server-side backoff schedule covers ack_wait-timeout
    # redeliveries (e.g. a crash where we never NAK). Increasing, bounded, and
    # shorter than max_deliver so the server accepts it.
    assert consumer_cfg.backoff  # non-empty schedule configured
    assert all(d > 0 for d in consumer_cfg.backoff)
    assert consumer_cfg.backoff == sorted(consumer_cfg.backoff)  # non-decreasing
    assert len(consumer_cfg.backoff) < cfg.max_deliver


def test_nak_issues_delayed_redelivery() -> None:
    # FIX 2 (cycle 2): a deliberate NAK backs off (non-zero delay) instead of an
    # immediate redelivery, so a transient outage does not exhaust max_deliver.
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})
    consumer = asyncio.run(pull_consumer(js, cfg))

    first = FakeMsg(num_delivered=1)
    asyncio.run(consumer.nak(first))
    assert first.naked
    assert first.nak_delay is not None and first.nak_delay > 0

    # Later deliveries back off further (or hold at the cap) — never less.
    later = FakeMsg(num_delivered=3)
    asyncio.run(consumer.nak(later))
    assert later.nak_delay is not None and later.nak_delay >= first.nak_delay


def test_nak_without_delivery_metadata_still_backs_off() -> None:
    # A message lacking delivery metadata must still nak with a non-zero delay.
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})
    consumer = asyncio.run(pull_consumer(js, cfg))

    msg = FakeMsg()  # no metadata
    asyncio.run(consumer.nak(msg))
    assert msg.naked and msg.nak_delay is not None and msg.nak_delay > 0


def test_pull_consumer_fetch_delegates_to_subscription() -> None:
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})

    consumer = asyncio.run(pull_consumer(js, cfg))
    msgs = asyncio.run(consumer.fetch(batch=2, timeout=1.0))

    assert len(msgs) == 2
    assert consumer.subscription.fetch_calls == [(2, 1.0)]


def test_consumer_ack_nak_term_dispatch_to_message() -> None:
    js = FakeJetStream()
    cfg = MessagingConfig.from_env(env={})
    consumer = asyncio.run(pull_consumer(js, cfg))

    ack_msg = FakeMsg()
    nak_msg = FakeMsg()
    term_msg = FakeMsg()

    asyncio.run(consumer.ack(ack_msg))
    asyncio.run(consumer.nak(nak_msg))
    asyncio.run(consumer.term(term_msg))

    assert ack_msg.acked and not ack_msg.naked and not ack_msg.termed
    assert nak_msg.naked and not nak_msg.acked and not nak_msg.termed
    assert term_msg.termed and not term_msg.acked and not term_msg.naked
