---
work_package_id: WP01
title: Foundation library (schema, events, sinks, doc-id, adapter)
dependencies: []
requirement_refs:
- FR-001
- FR-002
- FR-003
- FR-004
- FR-005
- FR-006
- FR-007
- FR-008
- FR-009
- FR-010
tracker_refs: []
planning_base_branch: feat/foundations-and-contracts
merge_target_branch: feat/foundations-and-contracts
branch_strategy: Planning artifacts for this mission were generated on feat/foundations-and-contracts. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/foundations-and-contracts unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
- T005
- T006
- T007
phase: Phase 1 - Foundations
assignee: ''
agent: "claude"
shell_pid: "20040"
history:
- timestamp: '2026-07-20T21:15:00Z'
  agent: system
  action: Prompt generated via /spec-kitty.tasks
authoritative_surface: src/
create_intent: []
execution_mode: code_change
owned_files:
- src/**
- tests/**
- doc/reuse/**
tags: []
---

# Work Package Prompt: WP01 – Foundation library

## Objective

Build the pure, tested foundation library under `src/goldberg_system/` per the plan
and `doc/design.md`. TDD: a failing test precedes each unit.

## Tasks

- **T001** `metadata/schema.py`: `DocumentMetadata` (pydantic v2) with every field
  in design.md's schema table + `matters` (list) / `primary_matter` + the Papra
  `documentId`↔`raw_path`/`raw_commit` mapping.
- **T002** `metadata/inheritance.py`: resolve `metadata.yaml` down a tree with
  locked / overridable / non-inherited / irreversible semantics; legal-handling
  flags default to the safe value when absent (two-tier).
- **T003** `identity/docid.py`: deterministic doc-id keyed on the raw file (path +
  content) + content-hash staleness check.
- **T004** `events/contracts.py`: `RawIngestedEvent` / `IndexedEvent` — source-agnostic,
  optional Papra `documentId`, lossless round-trip.
- **T005** `sinks/base.py`: `Sink` interface (Protocol/ABC) M4 writers implement;
  a fake sink in tests.
- **T006** `enrichment/adapter.py`: `EnrichmentAdapter` Protocol (MoS boundary) +
  `doc/reuse/mind_of_steele.md` documenting how MoS is obtained.
- **T007** Quality gate: `uv run pytest` green; `ruff`/`black`/`mypy` clean; suite
  runs offline.

## Acceptance

- Public surface (schema, inheritance, events, sink, doc-id/staleness, adapter)
  importable and covered by passing tests.
- Handling flags default safe; doc-id deterministic; models round-trip.
- `ruff`/`black`/`mypy` clean.

## Constraints

- Pure library — no network/external services. TDD (failing test first). Reuse
  goldberg-meta inheritance semantics; provenance fields first-class.

## Activity Log

- 2026-07-20T21:46:28Z – claude – shell_pid=16976 – Assigned agent via action command
- 2026-07-20T22:00:00Z – claude – shell_pid=16976 – Foundation library complete: 47 tests pass, ruff/black/mypy clean
- 2026-07-20T22:00:26Z – claude – shell_pid=20040 – Started review via action command
- 2026-07-20T22:01:23Z – user – shell_pid=20040 – Review passed: schema/inheritance/events/sinks/doc-id/adapter implemented per plan; 47 tests pass; ruff/black/mypy clean; FR-001..010 covered.
