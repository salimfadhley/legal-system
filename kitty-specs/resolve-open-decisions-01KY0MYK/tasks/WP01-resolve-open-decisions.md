---
work_package_id: WP01
title: Resolve open decisions (both ADRs + index)
dependencies: []
requirement_refs:
- FR-001
- FR-002
- FR-003
- FR-004
- FR-005
- FR-006
tracker_refs: []
planning_base_branch: feat/resolve-open-decisions
merge_target_branch: feat/resolve-open-decisions
branch_strategy: Planning artifacts for this mission were generated on feat/resolve-open-decisions. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/resolve-open-decisions unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
- T005
phase: Phase 1 - Decisions
assignee: ''
agent: ''
history:
- timestamp: '2026-07-20T20:59:00Z'
  agent: system
  action: Prompt generated via /spec-kitty.tasks
authoritative_surface: doc/
create_intent:
- doc/decisions/README.md
- doc/decisions/0001-wiki-rag-sink-backend.md
- doc/decisions/0002-large-binary-handling.md
execution_mode: doc_change
owned_files:
- doc/**
tags: []
---

# Work Package Prompt: WP01 – Resolve open decisions (both ADRs + index)

## Objective

Resolve the two open platform decisions by producing two ADRs under
`doc/decisions/`, plus an ADR index, and wire them into the docs. No production
code.

## Scope

Deliverables:
- `doc/decisions/README.md` — ADR index + format note.
- `doc/decisions/0001-wiki-rag-sink-backend.md` — recommendation for the wiki/RAG
  sink backend (Ragie vs Obsidian vs RAG-on-Elasticsearch).
- `doc/decisions/0002-large-binary-handling.md` — recommendation for large-binary
  handling in `goldberg-raw` (plain git vs git-LFS).
- Updated `doc/index.md` (and `doc/roadmap.md` cross-reference) linking the ADRs.

## Tasks

- **T001** Create `doc/decisions/README.md` (ADR convention + index).
- **T002** Author ADR 0001 (wiki/RAG sink). Evaluate each option against:
  attributed retrieval (cite source + raw commit + speaker + date), claim-level
  comparison / contradiction detection, provenance fidelity, reuse of Mind of
  Steele, on-network vs cloud, and vendor lock-in. State one recommended option
  with rationale + consequences, and name the downstream mission (M4).
- **T003** Author ADR 0002 (large-binary handling). Evaluate plain git vs git-LFS
  against: GitHub file-size limits, repo bloat under an append-mostly immutable
  raw store, per-file commit-sha provenance, LFS quota/cost, and the Halob
  file-watcher needing real files in the working tree. State one recommended
  option + rationale + consequences, and name the downstream mission (M8).
- **T004** Link `doc/decisions/` from `doc/index.md`; cross-reference from
  `doc/roadmap.md` M0.
- **T005** Verify against the spec's checklist (NFR-001/002/003, SC-001/002/003).

## Acceptance

- Both ADRs exist, each with exactly one recommended option, rationale, and
  consequences, and each cites ≥1 governing constraint and its downstream mission.
- ADRs follow a consistent template and are discoverable from the docs index.
- No unresolved decision markers remain.

## Constraints

- Respect charter risk boundaries (provenance preservable; `goldberg-raw`
  immutable). Do not re-litigate already-settled decisions (trigger locus; data
  boundary). No production code.
