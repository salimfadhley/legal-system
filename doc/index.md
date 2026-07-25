# Documentation Index

Documentation for **goldberg-system**, the pipeline and tooling for the Goldberg document
analysis platform.

## Start here

- **[Architecture](./architecture.md) — the canonical technical description.** Written so
  the system could be rebuilt from it: components, contracts, external dependencies
  (Elasticsearch, NATS, Docling, OpenAI), the provenance and data models, deployment
  topology, and the reasoning behind every significant choice — including the options
  tried and rejected.
- [Workflow](./workflow.md) — what happens to a document when it is ingested, from the
  document author's point of view (what *you* are responsible for, what is automatic).
- [Decisions (ADRs)](./decisions/) — the dated decision record. Each ADR captures one
  decision with its options and spike results; the
  [index in architecture.md §16](./architecture.md#16-decision-record-index) lists every
  ADR with its live status (several are superseded).

## Runbooks

- [Querying the corpus](./runbooks/querying-the-corpus.md) — the query tools and how an
  agent answers with citations
- [Wiring the ingest trigger](./runbooks/wiring-the-ingest-trigger.md) — pointing a
  `goldberg-raw` clone at the versioned git hooks
- [Verifying the system is up](./runbooks/verifying-the-system-is-up.md) — the operator's
  acceptance procedure
- [Component health](./runbooks/component-health.md) — the `goldberg doctor` board
- [Auditing completeness](./runbooks/auditing-completeness.md) — `goldberg audit`:
  missing / extra / stale, and `--orphans` (documents deleted from `goldberg-raw`)
- [Auto-ingestion reconciler](./runbooks/auto-ingestion-reconciler.md) — *historical*
  (the polling daemon, retired; see ADR 0013)
- [The live-index service](./runbooks/live-index-service.md) — *historical* (the
  Papra-webhook path, retired; see ADR 0011/0013)
- [Hard-case testing](./runbooks/hard-case-testing.md)

## Infrastructure

- [Servers → halob](./servers/halob.md) — the NAS that runs everything

## Verification & history

- [Event-driven ingestion results](./verification/event-driven-ingestion-results.md) —
  live-cutover evidence for the current ingest path
- [Design](./design.md) — **historical**: the 2026-07-20 design session. Still the best
  explanation of the corpus model and metadata schema; its pipeline and trigger sections
  are superseded.
- [Roadmap](./roadmap.md) — mission history and deferred work
- [resume/](./resume/) — timestamped session hand-off prompts

## Configuration

- [`config/projects.yaml`](../config/projects.yaml) — where the sibling repos and services
  live (the single source of truth for cross-project paths)
- [`config/evidence-allowlist.yaml`](../config/evidence-allowlist.yaml) — which trees are
  evidence, which are excluded and why
- [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) — the portable processing
  stack

## Related projects

- **Mind of Steele** — the sibling project that supplied the pattern (LLM summarisation,
  Elasticsearch indexing, a NATS-driven service loop). Referential, not a dependency;
  shared Halob/NATS infrastructure is documented there, not here.

---
*Last updated: 2026-07-24*
