# Documentation Index

Documentation for **goldberg-system**, the pipeline and tooling for the Goldberg document analysis platform.

## Start here
- [Architecture](./architecture.md) — the four-repo topology, Halob infrastructure, and code reuse
- [Workflow](./workflow.md) — what happens to a document when it is ingested (the pipeline)
- [Design](./design.md) — corpus model (two-axis taxonomy), metadata schema, and the two ingest paths
- [Roadmap](./roadmap.md) — the M0–M8 mission breakdown for taking the pipeline live

## Infrastructure
- [Servers → halob](./servers/halob.md) — the NAS that runs everything (Elasticsearch, NATS, Docker)

## Session hand-off
- [resume/](./resume/) — timestamped resume prompts for continuing this work in a fresh session

## Configuration
- [`config/projects.yaml`](../config/projects.yaml) — the single source of truth for where the sibling repos and Halob services live

## Related projects
- **Mind of Steele** (`~/workspace/mind_of_steele`) — the reference implementation the pipeline reuses (NATS-triggered live summarize + Elasticsearch indexer + Ragie uploader)

---
*Last updated: 2026-07-20*
