"""Unit tests for ``ingest.processor.IngestProcessor`` (WP03, FR-005/FR-006/FR-009).

Everything runs against injected fakes — a fake pull consumer (records ack/nak/term),
a stub ``process_commit`` returning a canned :class:`CommitResult`, and a recording
event sink. Async is driven with ``asyncio.run`` so no pytest-asyncio is needed. No
live NATS/ES/Docling.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from goldberg_system.ingest.processor import (
    CommitResult,
    FileResult,
    IngestProcessor,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeConsumer:
    """Records ack/nak/term dispatch; ``fetch`` drains preloaded batches."""

    def __init__(self, batches: list[list[Any]] | None = None) -> None:
        self._batches = list(batches or [])
        self.acked: list[Any] = []
        self.naked: list[Any] = []
        self.termed: list[Any] = []

    async def fetch(self, batch: int, timeout: float | None) -> list[Any]:
        if not self._batches:
            raise TimeoutError("no messages")
        return self._batches.pop(0)

    async def ack(self, msg: Any) -> None:
        self.acked.append(msg)

    async def nak(self, msg: Any) -> None:
        self.naked.append(msg)

    async def term(self, msg: Any) -> None:
        self.termed.append(msg)


class FakeMsg:
    def __init__(
        self, sha: str = "c0ffee", num_delivered: int = 1, data: bytes | None = None
    ) -> None:
        self.data = (
            data
            if data is not None
            else json.dumps({"sha": sha, "ts": "2026", "source": "test"}).encode()
        )
        self.metadata = SimpleNamespace(num_delivered=num_delivered)


class FakeEvents:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


def _stub(result: CommitResult) -> Any:
    def process_commit(commit_sha: str, run_id: str) -> CommitResult:
        return result

    return process_commit


def _make(
    result: CommitResult, *, events: FakeEvents | None = None, max_deliver: int = 5
) -> tuple[IngestProcessor, FakeConsumer]:
    consumer = FakeConsumer()
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=_stub(result),
        max_deliver=max_deliver,
        events=events,
        clock=lambda: "20260723T000000Z",
    )
    return proc, consumer


# --------------------------------------------------------------------------- #
# ack / nak / term
# --------------------------------------------------------------------------- #
def test_ack_when_all_files_terminal_ok() -> None:
    result = CommitResult(
        commit_sha="c0ffee",
        results=[FileResult("evidence/a.txt", "indexed", "sha_a")],
    )
    proc, consumer = _make(result)
    msg = FakeMsg()

    asyncio.run(proc._handle(msg))

    assert consumer.acked == [msg]
    assert consumer.naked == [] and consumer.termed == []


def test_skipped_indexed_is_terminal_and_acks() -> None:
    # Idempotency (FR-006): a redelivery whose files are already indexed just acks.
    result = CommitResult(
        commit_sha="c0ffee",
        results=[FileResult("evidence/a.txt", "skipped-indexed", "sha_a")],
    )
    proc, consumer = _make(result)
    msg = FakeMsg()

    asyncio.run(proc._handle(msg))

    assert consumer.acked == [msg]
    assert consumer.termed == []


def test_empty_commit_acks() -> None:
    proc, consumer = _make(CommitResult(commit_sha="c0ffee", results=[]))
    msg = FakeMsg()

    asyncio.run(proc._handle(msg))

    assert consumer.acked == [msg]


def test_transient_failure_naks_before_max_deliver() -> None:
    result = CommitResult(
        commit_sha="c0ffee",
        results=[FileResult("evidence/scan.pdf", "extract-failed: docling down", None)],
    )
    events = FakeEvents()
    proc, consumer = _make(result, events=events, max_deliver=5)
    msg = FakeMsg(num_delivered=1)

    asyncio.run(proc._handle(msg))

    assert consumer.naked == [msg]
    assert consumer.acked == [] and consumer.termed == []
    # No terminal DLQ event yet — the message will be retried.
    assert events.events == []


def test_terms_and_emits_dlq_after_max_deliver() -> None:
    result = CommitResult(
        commit_sha="c0ffee",
        results=[FileResult("evidence/scan.pdf", "extract-failed: docling down", "sha")],
    )
    events = FakeEvents()
    proc, consumer = _make(result, events=events, max_deliver=5)
    msg = FakeMsg(num_delivered=5)  # final delivery

    asyncio.run(proc._handle(msg))

    assert consumer.termed == [msg]
    assert consumer.naked == [] and consumer.acked == []
    # A terminal failed (DLQ) pipeline event is emitted (FR-009).
    dlq = [e for e in events.events if e.status == "failed"]
    assert dlq and dlq[0].raw_path == "evidence/scan.pdf"
    assert dlq[0].component == "ingest"


def test_process_commit_exception_naks_then_terms() -> None:
    def boom(commit_sha: str, run_id: str) -> CommitResult:
        raise RuntimeError("unexpected")

    consumer = FakeConsumer()
    events = FakeEvents()
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=boom,
        max_deliver=3,
        events=events,
        clock=lambda: "t",
    )
    # early delivery → nak
    asyncio.run(proc._handle(FakeMsg(num_delivered=1)))
    assert len(consumer.naked) == 1 and not consumer.termed
    # final delivery → term + DLQ event
    asyncio.run(proc._handle(FakeMsg(num_delivered=3)))
    assert len(consumer.termed) == 1
    assert any(e.status == "failed" for e in events.events)


def test_git_resolution_failure_naks_not_acks() -> None:
    # FIX 1 (cycle 2): if the commit's files cannot be resolved (unknown sha, a
    # merge that emitted no rows, a `.git` read error) `changed_files` raises
    # GitResolutionError. The processor must treat this as transient → nak (retry),
    # never ack an empty result and silently drop the commit's documents.
    from goldberg_system.ingest.commit_files import GitResolutionError

    def boom(commit_sha: str, run_id: str) -> CommitResult:
        raise GitResolutionError("git diff-tree exited 128: unknown revision")

    consumer = FakeConsumer()
    events = FakeEvents()
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=boom,
        max_deliver=5,
        events=events,
        clock=lambda: "t",
    )

    asyncio.run(proc._handle(FakeMsg(num_delivered=1)))

    assert consumer.naked and not consumer.acked and not consumer.termed
    # Not dead-lettered yet — it will be retried on redelivery.
    assert not any(e.status == "failed" for e in events.events)


def test_git_resolution_failure_terms_and_dlqs_at_max_deliver() -> None:
    # A persistent resolution failure can't loop forever: at max_deliver it terms
    # and emits a DLQ event, so it is surfaced (never silently dropped).
    from goldberg_system.ingest.commit_files import GitResolutionError

    def boom(commit_sha: str, run_id: str) -> CommitResult:
        raise GitResolutionError("git diff-tree exited 128: unknown revision")

    consumer = FakeConsumer()
    events = FakeEvents()
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=boom,
        max_deliver=3,
        events=events,
        clock=lambda: "t",
    )

    asyncio.run(proc._handle(FakeMsg(num_delivered=3)))

    assert consumer.termed and not consumer.acked
    assert any(e.status == "failed" for e in events.events)


def test_unparseable_message_is_poison_terminated() -> None:
    events = FakeEvents()
    proc, consumer = _make(
        CommitResult(commit_sha="x", results=[]), events=events
    )
    bad = FakeMsg(data=b"not json")

    asyncio.run(proc._handle(bad))

    assert consumer.termed == [bad]
    assert any(e.status == "failed" for e in events.events)


# --------------------------------------------------------------------------- #
# loop
# --------------------------------------------------------------------------- #
def test_process_batch_handles_every_message() -> None:
    result = CommitResult(
        commit_sha="c0ffee", results=[FileResult("a.txt", "indexed", "s")]
    )
    consumer = FakeConsumer(batches=[[FakeMsg(), FakeMsg()]])
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=_stub(result),
        max_deliver=5,
        clock=lambda: "t",
    )

    handled = asyncio.run(proc.process_batch())

    assert handled == 2
    assert len(consumer.acked) == 2


def test_process_batch_swallows_fetch_timeout() -> None:
    consumer = FakeConsumer(batches=[])  # fetch raises TimeoutError
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=_stub(CommitResult(commit_sha="x", results=[])),
        max_deliver=5,
        clock=lambda: "t",
    )

    assert asyncio.run(proc.process_batch()) == 0


def test_run_forever_stops_on_should_stop() -> None:
    result = CommitResult(
        commit_sha="c0ffee", results=[FileResult("a.txt", "indexed", "s")]
    )
    consumer = FakeConsumer(batches=[[FakeMsg()]])
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=_stub(result),
        max_deliver=5,
        clock=lambda: "t",
    )
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # allow two loop iterations

    asyncio.run(proc.run_forever(should_stop=should_stop, idle_sleep=0))

    assert consumer.acked  # the queued message was processed before stopping
