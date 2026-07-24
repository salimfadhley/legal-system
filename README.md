# goldberg-system

Code and tooling for the **Goldberg document analysis platform** — the pipeline that turns raw legal documents into a searchable, LLM-queryable knowledge base.

This is one of four repositories (plus a frozen predecessor).

📐 **[`doc/architecture.md`](doc/architecture.md) is the canonical technical description** — components, contracts, external dependencies, the provenance and data models, deployment, and the reasoning behind each choice. It is written so the system could be rebuilt from it. Read that first.

[`doc/workflow.md`](doc/workflow.md) is the short version: what happens to a document when it is ingested.

## How it works in one paragraph

A human or agent commits an original document into **goldberg-raw** (a git repo that the pipeline never writes to). A `post-commit` hook publishes the commit sha to **NATS JetStream**; a durable consumer records the file's provenance (`sha256` → `raw_path` + `raw_commit` + legal matter) *before* indexing anything, extracts it via a self-hosted **Docling** OCR service, enriches it with an LLM into a summary plus **attributed claims** (`subject`/`predicate`/`object`/`asserted_by`), and indexes it into **Elasticsearch**. A CLI and a hosted **MCP server** then let an agent answer questions with citations — and compare claims across documents to surface contradictions.

## The four repositories

| Repo | Role |
|---|---|
| **goldberg-system** (this) | The pipeline & tooling: ingest → extract → summarize → index → wiki |
| **goldberg-raw** | Immutable original documents; the push that triggers the pipeline |
| **goldberg-extracted** | Machine-generated markdown + metadata (regenerable artifact store) |
| **goldberg-casework** | Authored work product: briefings, applications, analysis, research |

The predecessor repo `the_goldberg_files` is **frozen** as an archive / rollback point and is not modified.

Locations of all repos and services are recorded in [`config/projects.yaml`](config/projects.yaml) — the single source of truth for cross-project paths.

## Quick start (uv)

```bash
uv sync                       # create the environment
uv run pytest                 # run the tests (TDD is mandatory here)
uv run goldberg config        # print the resolved project/service locations
uv run goldberg doctor        # is every component up?
uv run goldberg status --yaml # system state, LLM-readable
```

## Where things run

Everything runs on **Halob** (the home NAS, `192.168.86.31`): Elasticsearch (`:9200`), NATS (`:4222`), Docker/Portainer. See [`doc/servers/halob.md`](doc/servers/halob.md).

The processing services (Docling, the ingest service, the MCP server) deploy as one portable compose stack — [`deploy/docker-compose.yml`](deploy/docker-compose.yml). Elasticsearch and NATS are external shared infrastructure and are deliberately **not** in the stack; see [ADR 0012](doc/decisions/0012-deployment-topology.md).

## Governance

This project is managed with **Spec Kitty** (`.kittify/`, `AGENTS.md`). Mission-style work goes through `/spec-kitty.specify`; the files under `doc/` are the human-facing design record and are maintained directly.

---
*See [`doc/index.md`](doc/index.md) for the full documentation index, and [`doc/resume/`](doc/resume/) for session hand-off prompts.*
