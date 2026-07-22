# Architecture Decision Records

This directory holds the project's Architecture Decision Records (ADRs) — short
documents that capture a material decision, the options considered, why one was
chosen, and the consequences that follow. They exist to satisfy the charter's
decision-documentation requirement: a future contributor should be able to read
an ADR and understand *why* a path was chosen and *what constraints must remain
true*.

## Format

Each ADR follows the same sections:

- **Context** — the situation and the decision drivers.
- **Options considered** — the alternatives, with trade-offs.
- **Decision** — the single chosen option.
- **Consequences** — what follows, including follow-ups and costs accepted.
- **Downstream** — which mission(s) the decision unblocks.

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](./0001-wiki-rag-sink-backend.md) | Wiki / RAG sink backend | Accepted |
| [0002](./0002-large-binary-handling.md) | Large-binary handling in `goldberg-raw` | Accepted |
| [0003](./0003-document-management-papra-integration.md) | Document management — integrate existing Papra (+ Docling) | Accepted · retired from deploy by [0012](./0012-deployment-topology.md) |
| [0004](./0004-metadata-representation.md) | Metadata representation — markdown + YAML frontmatter | Accepted |
| [0005](./0005-live-service-webhook-driven.md) | Live pipeline — Papra-webhook-driven service (v1) | Superseded by 0011 |
| [0006](./0006-ingestion-provenance-architecture.md) | Ingestion & provenance architecture (git-raw + manifest) | Accepted |
| [0007](./0007-concept-wiki-output.md) | Concept wiki output | Accepted |
| [0008](./0008-observability-architecture.md) | Observability architecture (events / DLQ / trace) | Accepted |
| [0009](./0009-operations-dashboard.md) | Operations dashboard | Accepted |
| [0010](./0010-mcp-server.md) | Hosted MCP server | Accepted |
| [0011](./0011-auto-ingestion-reconciler.md) | Auto-ingestion reconciler (`goldberg watch`) | Accepted |
| [0012](./0012-deployment-topology.md) | Deployment topology — portable processing stack vs. external ES | Accepted |

*Resolved outside an ADR (recorded here for completeness): trigger locus =
Halob-local filesystem watcher; data boundary = cloud LLM/Ragie permitted (logged
charter exception).*
