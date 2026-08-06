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
| [0003](./0003-document-management-papra-integration.md) | Document management — integrate existing Papra (+ Docling) | **Partly superseded** — Docling kept (called directly); Papra retired by [0011](./0011-auto-ingestion-reconciler.md)/[0012](./0012-deployment-topology.md) |
| [0004](./0004-metadata-representation.md) | Metadata representation — markdown + YAML frontmatter | Accepted |
| [0005](./0005-live-service-webhook-driven.md) | Live pipeline — Papra-webhook-driven service (v1) | Superseded by 0011 |
| [0006](./0006-ingestion-provenance-architecture.md) | Ingestion & provenance architecture (git-raw + manifest) | Accepted |
| [0007](./0007-concept-wiki-output.md) | Concept wiki output | Accepted — **partially built** (renderer only; synthesis not wired) |
| [0008](./0008-observability-architecture.md) | Observability architecture (events / DLQ / trace) | Accepted — **core delivered** (events go direct to ES, not via JetStream) |
| [0009](./0009-operations-dashboard.md) | Operations dashboard | Accepted — **phases 1–2 built** (not deployed as a container) |
| [0010](./0010-mcp-server.md) | Hosted MCP server | Accepted |
| [0011](./0011-auto-ingestion-reconciler.md) | Auto-ingestion reconciler (`legal_system watch`) | Superseded by 0013 |
| [0012](./0012-deployment-topology.md) | Deployment topology — portable processing stack vs. external ES | Accepted (ingest service per 0013) |
| [0013](./0013-event-driven-ingestion.md) | Event-driven ingestion — git-hook → NATS → durable processor (`legal_system ingest-serve`) | Accepted |

The **current** state of the system — with each of these decisions folded into one
coherent description — is [`doc/architecture.md`](../architecture.md). Where an ADR and
that document disagree, the ADR is the history and `architecture.md` is the truth.

*Resolved outside an ADR (recorded here for completeness): data boundary = cloud LLM
permitted, a logged charter exception (Ragie is specified but not used). The trigger
locus was resolved twice by ADR — a filesystem watcher was rejected both times
(unreliable change notification over the SMB-mounted NAS) in favour of polling
([0011](./0011-auto-ingestion-reconciler.md)) and finally a git-commit event
([0013](./0013-event-driven-ingestion.md)).*
