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
| [0003](./0003-document-management-papra-integration.md) | Document management — integrate existing Papra (+ Docling) | Accepted |
| [0004](./0004-metadata-representation.md) | Metadata representation — markdown + YAML frontmatter | Accepted |

*Resolved outside an ADR (recorded here for completeness): trigger locus =
Halob-local filesystem watcher; data boundary = cloud LLM/Ragie permitted (logged
charter exception).*
