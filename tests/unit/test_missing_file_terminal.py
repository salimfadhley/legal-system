"""A raw file missing from goldberg-raw is a TERMINAL failure, not an infinite retry.

Casework saw the same vanished path fail every 20-40 min forever. Two rules:

* the reingest path removes the STALE index entry (an indexed doc citing a raw_path that
  can no longer be opened reads as fabrication) and returns the terminal ``missing-file``
  status; and
* the processor treats ``missing-file`` as terminal → it ACKS (stops redelivery) instead
  of naking forever.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from goldberg_system.ingest.processor import CommitResult, FileResult, IngestProcessor
from goldberg_system.migrate.manifest import Manifest
from goldberg_system.migrate.reingest import reingest_from_raw
from goldberg_system.sinks.base import SinkResult


class _TombstoningSink:
    """A sink that records writes AND supports stale-entry removal by raw_path."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.removed: list[str] = []

    @property
    def name(self) -> str:
        return "tombstoning"

    def write(self, document: Any) -> SinkResult:
        self.written.append(document.raw_path)
        return SinkResult(sink=self.name, ok=True)

    def remove_by_raw_path(self, raw_path: str) -> int:
        self.removed.append(raw_path)
        return 1


class _FakeDocling:
    def convert_file(self, path: Path | str) -> str:  # pragma: no cover - never reached
        raise AssertionError("docling must not be called for a missing file")


class _FakeEnricher:  # pragma: no cover - never reached for a missing file
    def enrich(self, request: Any) -> Any:
        raise AssertionError("enricher must not be called for a missing file")


def test_reingest_missing_file_is_terminal_and_removes_stale_entry(tmp_path: Path) -> None:
    # The manifest references a file that is NOT on disk (deleted from goldberg-raw).
    manifest = Manifest({"s1": {"raw_path": "evidence/gone.txt", "size": 1}})
    sink = _TombstoningSink()
    statuses: dict[str, str] = {}

    report = reingest_from_raw(
        tmp_path,
        manifest,
        _FakeDocling(),
        _FakeEnricher(),
        [sink],
        on_doc=lambda rp, st: statuses.__setitem__(rp, st),
    )

    assert statuses == {"evidence/gone.txt": "missing-file"}
    assert report.missing_file == 1
    assert report.indexed == 0
    # the stale index entry was tombstoned (removed), not left to read as fabrication
    assert sink.removed == ["evidence/gone.txt"]
    assert sink.written == []


def test_es_indexer_remove_by_raw_path_issues_term_delete() -> None:
    from goldberg_system.sinks.elasticsearch_indexer import ElasticsearchIndexer

    class _FakeES:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def delete_by_query(self, index: str, query: dict, refresh: bool = False) -> dict:
            self.calls.append({"index": index, "query": query})
            return {"deleted": 3}

    es = _FakeES()
    n = ElasticsearchIndexer(es, "idx").remove_by_raw_path("evidence/gone.txt")

    assert n == 3
    assert es.calls[0]["query"] == {"term": {"raw_path": "evidence/gone.txt"}}


# --------------------------------------------------------------------------- #
# processor: missing-file is terminal → ACK, not an endless nak
# --------------------------------------------------------------------------- #
def test_file_result_missing_file_is_terminal() -> None:
    assert FileResult("evidence/gone.txt", "missing-file").ok is True
    # the transient synthetic "missing" (unpulled LFS / not-yet-processed) stays retryable
    assert FileResult("evidence/pending.txt", "missing").ok is False


class _FakeConsumer:
    def __init__(self) -> None:
        self.acked: list[Any] = []
        self.naked: list[Any] = []
        self.termed: list[Any] = []

    async def ack(self, msg: Any) -> None:
        self.acked.append(msg)

    async def nak(self, msg: Any) -> None:
        self.naked.append(msg)

    async def term(self, msg: Any) -> None:
        self.termed.append(msg)


class _FakeMsg:
    def __init__(self, num_delivered: int = 1) -> None:
        self.data = json.dumps({"sha": "c0ffee"}).encode()
        self.metadata = SimpleNamespace(num_delivered=num_delivered)


def test_processor_acks_a_missing_file_commit_instead_of_retrying() -> None:
    result = CommitResult(
        commit_sha="c0ffee",
        results=[FileResult("evidence/gone.txt", "missing-file", "s1")],
    )
    consumer = _FakeConsumer()
    proc = IngestProcessor(
        consumer=consumer,
        process_commit=lambda sha, rid: result,
        max_deliver=5,
        clock=lambda: "t",
    )

    asyncio.run(proc._handle(_FakeMsg(num_delivered=1)))

    # terminal → acked once; never naked (which would retry every 20-40 min forever)
    assert consumer.acked and not consumer.naked and not consumer.termed
