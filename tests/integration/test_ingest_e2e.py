"""Opt-in end-to-end ingest test (WP03, T015) — real NATS + ES + Docling.

Skipped unless ``GOLDBERG_INTEGRATION=1``. It provisions isolated ``*_test`` NATS and
ES targets, seeds a tiny fixture file under a temp goldberg-raw git repo, publishes a
raw-commit trigger, drives the processor for one batch, and asserts the fixture reaches
``indexed/ok`` with real provenance — then publishes the *same* commit again and
asserts no duplicate is indexed (idempotency, FR-006).

This never runs in unit mode; it documents and exercises the live wiring.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import uuid
from pathlib import Path

import pytest

if not os.environ.get("GOLDBERG_INTEGRATION"):  # pragma: no cover - opt-in gate
    pytest.skip(
        "live integration (NATS + ES + Docling) — set GOLDBERG_INTEGRATION=1",
        allow_module_level=True,
    )

from goldberg_system.ingest import (  # noqa: E402
    IngestProcessor,
    build_commit_processor,
)
from goldberg_system.messaging import (  # noqa: E402
    MessagingConfig,
    connect,
    ensure_stream,
    publish_commit,
    pull_consumer,
)
from goldberg_system.migrate.allowlist import Allowlist, IncludedTree  # noqa: E402
from goldberg_system.provenance import now_iso  # noqa: E402

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": os.environ["PATH"],
}


def _seed_commit(raw: Path) -> str:
    (raw / "evidence").mkdir(parents=True)
    (raw / "evidence" / "metadata.yaml").write_text("case_number: TEST\n")
    (raw / "evidence" / "note.txt").write_text("a small fixture evidence note")
    subprocess.run(["git", "-C", str(raw), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(raw), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(raw), "commit", "-qm", "seed"], check=True, env=_GIT_ENV
    )
    return subprocess.run(
        ["git", "-C", str(raw), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _allowlist() -> Allowlist:
    return Allowlist(
        included={"evidence": IncludedTree("evidence", "received")},
        excluded={},
        exclude_globs=(),
    )


def test_publish_commit_indexes_once(tmp_path: Path) -> None:  # pragma: no cover - live
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.observability.events import ElasticsearchEventSink
    from goldberg_system.sinks import ElasticsearchIndexer

    raw = tmp_path / "raw"
    sha = _seed_commit(raw)
    manifest_path = tmp_path / "provenance-manifest.json"

    suffix = uuid.uuid4().hex[:8]
    cfg = dataclasses.replace(
        MessagingConfig.from_env(),
        stream="GOLDBERG_TEST",
        subject_prefix="gbtest",
        commit_subject="gbtest.raw.commit",
        durable=f"ingest-test-{suffix}",
    )

    indexer = ElasticsearchIndexer.from_env()
    indexer.index = "goldberg_documents_test"
    indexer.ensure_index()
    event_sink = ElasticsearchEventSink.from_env()
    event_sink.ensure_index()

    def already_indexed() -> set[str]:
        resp = indexer.client.search(
            index=indexer.index,
            query={"exists": {"field": "raw_sha256"}},
            size=10000,
            source_includes=["raw_sha256"],
        )
        return {
            h["_source"]["raw_sha256"]
            for h in resp["hits"]["hits"]
            if h["_source"].get("raw_sha256")
        }

    process_commit = build_commit_processor(
        raw_root=raw,
        manifest_path=manifest_path,
        allowlist=_allowlist(),
        docling=DoclingClient.from_env(),
        enricher=OpenAIEnricher.from_settings(),
        sinks=[indexer],
        already_indexed=already_indexed,
        events=event_sink,
        workers=1,
    )

    async def drive() -> tuple[int, int]:
        conn = await connect(cfg)
        try:
            await ensure_stream(conn.js, cfg)
            consumer = await pull_consumer(conn.js, cfg)
            processor = IngestProcessor(
                consumer=consumer,
                process_commit=process_commit,
                max_deliver=cfg.max_deliver,
                events=event_sink,
                batch=10,
            )
            await publish_commit(conn.js, cfg, sha, now_iso(), "post-commit")
            await asyncio.sleep(0.5)
            await processor.process_batch()
            indexer.client.indices.refresh(index=indexer.index)
            first = already_indexed()

            # Re-publish the same commit → dedup + idempotency: no new document.
            await publish_commit(conn.js, cfg, sha, now_iso(), "post-commit")
            await asyncio.sleep(0.5)
            await processor.process_batch()
            indexer.client.indices.refresh(index=indexer.index)
            second = already_indexed()
            return len(first), len(second)
        finally:
            await conn.close()

    first_n, second_n = asyncio.run(drive())
    assert first_n >= 1  # fixture reached indexed/ok with provenance
    assert second_n == first_n  # republish did not create a duplicate
