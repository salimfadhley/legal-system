# Tasks: M1 — Foundations & Contracts

Single work package (pure library; TDD). Subtasks are ordered: schema first, then
dependents.

## WP01 — Foundation library

- [ ] T001 (IC-01) `DocumentMetadata` pydantic model — all `doc/design.md` fields + Papra mapping + `matters` list/`primary_matter`. Failing tests first. (FR-001, FR-004, FR-009)
- [ ] T002 (IC-01) Directory-inheritance resolver (locked / overridable / non-inherited / irreversible) + two-tier safe-default handling flags. Failing tests first. (FR-002, FR-003, NFR-002)
- [ ] T003 (IC-04) Deterministic doc-id + content-hash staleness. Failing tests first. (FR-007, FR-008, NFR-003)
- [ ] T004 (IC-02) Event contracts `RawIngestedEvent` / `IndexedEvent`, source-agnostic + optional Papra `documentId`. Failing tests first. (FR-005)
- [ ] T005 (IC-03) Sink interface (Protocol/ABC) + a fake sink in tests. (FR-006)
- [ ] T006 (IC-05) `EnrichmentAdapter` Protocol + `doc/reuse/mind_of_steele.md`. Boundary test. (FR-010)
- [ ] T007 Quality gate: `ruff`/`black`/`mypy` clean, full suite green, offline. (NFR-001, NFR-004, NFR-005)

## Dependencies

- WP01 has no dependencies (single work package).
