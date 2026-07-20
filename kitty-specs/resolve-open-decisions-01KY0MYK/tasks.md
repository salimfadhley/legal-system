# Tasks: M0 — Resolve Open Platform Decisions

Single work package (documentation-only mission — two ADRs + an index). Tasks are
sequential within the one lane.

## WP01 — Resolve open decisions (both ADRs + index)

- [x] T001 Establish the `doc/decisions/` ADR convention: create `doc/decisions/README.md` (ADR index + format: Context / Options / Decision / Consequences).
- [x] T002 Write ADR `0001-wiki-rag-sink-backend.md` — evaluate Ragie vs Obsidian vault vs RAG-on-Elasticsearch against attributed-retrieval, claim-comparison, provenance, and reuse drivers; record one recommended option with rationale and consequences. (FR-001, FR-003, FR-004, FR-005)
- [x] T003 Write ADR `0002-large-binary-handling.md` — evaluate plain git vs git-LFS for `goldberg-raw` against host limits, repo bloat, provenance, and the Halob watcher; record one recommended option with rationale and consequences. (FR-002, FR-003, FR-004)
- [x] T004 Wire discoverability: link `doc/decisions/` from `doc/index.md` and cross-reference from `doc/roadmap.md` (M0). (FR-006)
- [x] T005 Verify against the spec checklist: both decisions resolved (NFR-001), each ADR cites ≥1 constraint + downstream mission (NFR-002), consistent template (NFR-003), no unresolved markers (SC-003).

## Dependencies

- WP01 has no dependencies (single work package).
