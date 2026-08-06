# legal-system

A document-intelligence pipeline that turns a corpus of raw documents into a searchable, LLM-queryable knowledge base with citation-grade provenance — built for legal document sets, where every answer must trace back to a primary source.

📐 **[`doc/architecture.md`](doc/architecture.md) is the canonical technical description** — components, contracts, external dependencies, the provenance and data models, deployment, and the reasoning behind each choice. It is written so the system could be rebuilt from it. Read that first.

[`doc/workflow.md`](doc/workflow.md) is the short version: what happens to a document when it is ingested.

## How it works in one paragraph

A human or agent commits an original document into an immutable **raw** repository (which the pipeline never writes to). A `post-commit` hook publishes the commit sha to **NATS JetStream**; a durable consumer records the file's provenance (`sha256` → `raw_path` + `raw_commit` + matter) *before* indexing anything, extracts it via a self-hosted **Docling** OCR service, enriches it with an LLM into a summary plus **attributed claims** (`subject`/`predicate`/`object`/`asserted_by`), and indexes it into **Elasticsearch**. A CLI and a hosted **MCP server** then let an agent answer questions with citations — and compare claims across documents to surface contradictions.

## Repositories

The pipeline spans a small set of repositories; this one holds the tooling, the others hold data and are private.

| Repo | Role |
|---|---|
| this repo | The pipeline & tooling: ingest → extract → enrich → index → search |
| raw store *(private)* | Immutable original documents; the push that triggers the pipeline |
| extracted store *(private)* | Machine-generated markdown + metadata (a regenerable artifact store) |
| casework *(private)* | Authored work product: analysis, drafting, research |

Cross-project paths are recorded in [`config/projects.yaml`](config/projects.yaml) — the single source of truth for locations.

## Quick start (uv)

```bash
uv sync                       # create the environment
uv run pytest                 # run the tests (TDD is mandatory here)
uv run goldberg config        # print the resolved project/service locations
uv run goldberg doctor        # is every component up?
uv run goldberg status --yaml # system state, LLM-readable
```

## Where things run

The processing services (Docling OCR, the ingest service, the MCP server) deploy as one portable compose stack — [`deploy/docker-compose.yml`](deploy/docker-compose.yml). Elasticsearch and NATS are external shared infrastructure and are deliberately **not** in the stack; see [ADR 0012](doc/decisions/0012-deployment-topology.md).

## Governance

This project is managed with **Spec Kitty** (`.kittify/`, `AGENTS.md`). Mission-style work goes through `/spec-kitty.specify`; the files under `doc/` are the human-facing design record and are maintained directly.

---
*See [`doc/index.md`](doc/index.md) for the full documentation index.*
