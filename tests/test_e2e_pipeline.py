"""End-to-end pipeline test (charter: major components need an E2E test).

Exercises the WHOLE ingest chain against its real integrations —
goldberg-raw → Docling → enrich (OpenAI) → Elasticsearch → query — into an
**isolated** ``goldberg_documents_e2e`` index (never the live corpus), and guards
performance (a single small document must complete under a time budget, catching a
major slowdown).

Opt-in: `GOLDBERG_INTEGRATION=1 uv run pytest -m integration`. Skips cleanly when the
flag or the services are absent, so the default unit-test run is unaffected.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.integration

# a single small document should traverse the whole pipeline well under this
_PERF_BUDGET_SECONDS = 180.0


def _require_integration() -> None:
    if os.environ.get("GOLDBERG_INTEGRATION") != "1":
        pytest.skip("set GOLDBERG_INTEGRATION=1 to run E2E integration tests")


def test_full_pipeline_end_to_end() -> None:
    _require_integration()
    from goldberg_system.config import project_path
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.migrate.manifest import Manifest
    from goldberg_system.migrate.reingest import reingest_from_raw
    from goldberg_system.query import CorpusQuery
    from goldberg_system.sinks import ElasticsearchIndexer

    docling = DoclingClient.from_env()
    if not docling.health():
        pytest.skip(f"docling not reachable at {docling.base_url}")

    raw_root = project_path("raw")
    manifest = Manifest.load(
        project_path("system") / "config" / "provenance-manifest.json"
    )

    # pick a small real PDF fixture that exists in goldberg-raw
    target = None
    for _sha, entry in manifest.items():
        rp = entry.get("raw_path", "")
        p = raw_root / rp
        if rp.lower().endswith(".pdf") and p.is_file() and p.stat().st_size < 250_000:
            target = rp
            break
    if target is None:
        pytest.skip("no small PDF fixture available in goldberg-raw")

    index = "goldberg_documents_e2e"
    indexer = ElasticsearchIndexer.from_env()
    indexer.index = index  # ISOLATED — never the live corpus
    indexer.ensure_index()
    indexer.client.delete_by_query(index=index, query={"match_all": {}}, refresh=True)

    enricher = OpenAIEnricher.from_settings()

    started = time.monotonic()
    report = reingest_from_raw(
        raw_root, manifest, docling, enricher, [indexer], only={target}
    )
    elapsed = time.monotonic() - started

    try:
        # 1. it integrated: the doc went all the way to the index
        assert report.indexed == 1, f"expected 1 indexed, got {report}"

        # 2. performance guard: a single small doc must not blow the budget
        assert elapsed < _PERF_BUDGET_SECONDS, (
            f"single-doc E2E took {elapsed:.0f}s (> {_PERF_BUDGET_SECONDS}s budget) — "
            "possible performance regression"
        )

        # 3. it is queryable with real provenance + enrichment
        indexer.client.indices.refresh(index=index)
        q = CorpusQuery(indexer.client, index)
        hits = q.search(None, size=10)
        doc = next((h for h in hits if h.raw_path == target), None)
        assert doc is not None, f"{target} not queryable after ingest"

        full = q.get(doc.doc_id)
        assert full is not None
        assert full.get("raw_path") == target
        assert full.get("raw_sha256"), "missing correlation ID (raw_sha256)"
        assert full.get("raw_commit"), "missing raw_commit provenance"
        assert (full.get("content") or "").strip(), "no extracted content"
        assert (full.get("summary") or "").strip(), "not enriched (no summary)"
    finally:
        indexer.client.indices.delete(index=index, ignore_unavailable=True)
