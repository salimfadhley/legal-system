"""The hosted goldberg MCP server (M14).

Two tool families over one core (observability + evidence query), served over the
``streamable-http`` transport so any number of agents connect to one live endpoint.
Every tool is a thin, read-only wrapper over ``aggregate()`` / ``CorpusQuery`` /
``read_trace`` — the same functions behind the CLI — so there is no second
implementation to drift.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from goldberg_system.observability.health import run_doctor
from goldberg_system.observability.state import _recent, aggregate
from goldberg_system.observability.trace import read_trace
from goldberg_system.query import CorpusQuery

EVENTS_INDEX = os.environ.get("GOLDBERG_EVENTS_INDEX", "goldberg_pipeline_events")

mcp = FastMCP(
    "goldberg",
    instructions=(
        "Visibility and query over the Goldberg legal-evidence pipeline. Use the "
        "observability tools to report system/indexing health; use the evidence tools "
        "to answer questions about the case corpus — always cite doc_id + raw_path + "
        "speaker + date, and never invent facts."
    ),
    host=os.environ.get("GOLDBERG_MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("GOLDBERG_MCP_PORT", "8765")),
)


@lru_cache(maxsize=1)
def _q() -> CorpusQuery:
    return CorpusQuery.from_env()


# ── Observability: "how's the system?" ────────────────────────────────────────


@mcp.tool()
def system_status() -> dict[str, Any]:
    """Current pipeline health and shape: health checks, corpus size, per-stage/
    status counts, wiki page counts, and DLQ (failed/skipped) depth. Answers
    'how's indexing going?' and 'are there bottlenecks?'."""
    return aggregate(_q().client).model_dump()


@mcp.tool()
def recent_failures(limit: int = 20) -> list[dict[str, Any]]:
    """Documents that failed or were skipped in the pipeline, most recent first —
    answers 'has anything failed today?'. Each entry has the stage, reason, and
    raw_path/doc_id so you can follow up with trace_document."""
    return _recent(_q().client, EVENTS_INDEX, ["failed", "skipped"], size=limit)


@mcp.tool()
def trace_document(identifier: str) -> list[dict[str, Any]]:
    """The full pipeline timeline for one document — where it stopped and why.
    ``identifier`` may be a raw_path, a sha256 (the correlation ID), or a doc_id."""
    return [e.model_dump() for e in read_trace(_q().client, identifier)]


@mcp.tool()
def component_health() -> dict[str, Any]:
    """The ``goldberg doctor`` liveness board as structured data — every pipeline
    component (Elasticsearch, Docling, the enricher, the MCP server, the live-index
    watcher, wiki synthesis) probed read-only and time-bounded, plus the worst-status
    overall verdict. Answers 'is every component actually up?'. Each entry carries a
    status (UP/DEGRADED/DOWN), a one-line reason and latency."""
    return run_doctor(es_client=_q().client).model_dump(mode="json")


# ── Evidence: "what does the corpus say?" ─────────────────────────────────────


@mcp.tool()
def search_evidence(
    query: str,
    matter: str | None = None,
    author: str | None = None,
    document_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Full-text search the evidence documents (pass a natural query, not an ES
    query). Optional filters: ``matter`` (case number), ``author`` (who is
    speaking), ``document_type``. Returns citable hits: doc_id, raw_path, summary,
    matters, author, and highlighted snippets."""
    hits = _q().search(
        query,
        matters=[matter] if matter else None,
        author=author,
        document_type=document_type,
        size=limit,
    )
    return [h.model_dump() for h in hits]


@mcp.tool()
def find_claims(
    speaker: str | None = None,
    subject: str | None = None,
    object: str | None = None,
    text: str | None = None,
    matter: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Attributed claims — who asserted what about whom. Use ``speaker`` for 'what
    did X say', ``subject``/``object`` for 'what was said about Y', and compare
    results across speakers/time to surface contradictions. Each claim carries its
    source doc_id + raw_path."""
    hits = _q().claims(
        asserted_by=speaker,
        subject=subject,
        object=object,
        text=text,
        matters=[matter] if matter else None,
        size=limit,
    )
    return [h.model_dump() for h in hits]


@mcp.tool()
def search_concepts(
    query: str, layer: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Search the synthesised concept wiki (entities / concepts / contradictions) —
    the 'map' of who's who and how the case connects. ``layer`` filters to
    entity|concept|comparison. Follow a page's sources back into the documents to
    quote the underlying evidence."""
    pages = _q().wiki(query, layer=layer, size=limit)
    return [p.model_dump() for p in pages]


@mcp.tool()
def get_document(doc_id: str) -> dict[str, Any] | None:
    """Fetch one document's full extracted text + metadata by doc_id, to quote it
    precisely. Returns None if the id is unknown."""
    return _q().get(doc_id)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
