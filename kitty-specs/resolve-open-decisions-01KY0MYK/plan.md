# Implementation Plan: M0 — Resolve Open Platform Decisions

**Branch**: `feat/resolve-open-decisions` | **Date**: 2026-07-20 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `kitty-specs/resolve-open-decisions-01KY0MYK/spec.md`

## Summary

Settle the two remaining open architecture decisions — the wiki/RAG sink backend
and large-binary handling in `goldberg-raw` — by evaluating each option against
the project's decision drivers and recording a firm recommendation as an
Architecture Decision Record (ADR). Output is two ADRs under `doc/decisions/`,
discoverable from the documentation index. No production code.

## Technical Context

**Language/Version**: Python 3.12 (repository language; this mission produces
Markdown ADRs only — no code is written)
**Primary Dependencies**: None (documentation deliverable). Options under
evaluation reference Mind of Steele (Ragie uploader + Elasticsearch indexer),
Halob-hosted Elasticsearch, and Ragie.
**Storage**: Files — `doc/decisions/*.md` (ADRs)
**Testing**: No code under test. Acceptance = both ADRs present, each with a
single recommended option + rationale, and reviewed against the checklist.
**Target Platform**: Repository documentation (Markdown)
**Project Type**: single
**Performance Goals**: N/A (no runtime component)
**Constraints**: Must respect charter risk boundaries (provenance preservable,
`goldberg-raw` immutable); the cloud LLM/Ragie data-boundary exception applies.
**Scale/Scope**: 2 ADRs; 2 in-scope decisions.

## Charter Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **TDD mandatory** — N/A: no code is produced. Not violated (documentation-only
  mission); recorded as a deliberate scope, not a TDD waiver for code.
- **Provenance / raw-immutability risk boundaries** — respected: the ADRs must
  choose options that keep provenance preservable and raw immutable (C-002).
- **Decision documentation (DIRECTIVE_003) + traceable-decisions** — this mission
  exists to satisfy exactly these: each decision is captured as a traceable ADR.
- **Data-boundary exception** — honoured: cloud options (Ragie) remain admissible.

PASS.

## Project Structure

### Documentation (this mission)

```
kitty-specs/resolve-open-decisions-01KY0MYK/
├── plan.md              # This file
├── spec.md              # Requirements
└── tasks/               # Work packages (/spec-kitty.tasks output)
```

### Deliverables (repository root)

```
doc/
├── decisions/
│   ├── README.md                         # ADR index + format note
│   ├── 0001-wiki-rag-sink-backend.md     # ADR for the wiki/RAG sink
│   └── 0002-large-binary-handling.md     # ADR for raw large-binary storage
└── index.md                              # updated to link doc/decisions/
```

**Structure Decision**: Documentation-only mission. ADRs live under
`doc/decisions/` following the standard ADR format (Context / Options /
Decision / Consequences), indexed by `doc/decisions/README.md` and surfaced from
`doc/index.md`.

## Implementation Concern Map

> Concerns are not work packages. `/spec-kitty.tasks` translates them into WPs.

### IC-01 — Wiki / RAG sink backend decision

- **Purpose**: choose the backend for the searchable, attributed knowledge layer
  so M4 (sinks) can implement against a settled target.
- **Relevant requirements**: FR-001, FR-003, FR-004, FR-005, NFR-001, NFR-002,
  NFR-003, C-002, C-003.
- **Affected surfaces**: `doc/decisions/0001-wiki-rag-sink-backend.md`.
- **Sequencing/depends-on**: none.
- **Risks**: attributed retrieval + claim-comparison fidelity and provenance are
  the decisive drivers; option must not compromise citation-to-raw-commit.

### IC-02 — Large-binary handling in goldberg-raw

- **Purpose**: choose how large binary originals are stored so M8 (migration) can
  land the corpus without hitting host limits or bloating the repo.
- **Relevant requirements**: FR-002, FR-003, FR-004, NFR-001, NFR-002, NFR-003,
  C-002.
- **Affected surfaces**: `doc/decisions/0002-large-binary-handling.md`.
- **Sequencing/depends-on**: none.
- **Risks**: GitHub file-size limits vs repo bloat vs LFS quota; must preserve
  per-file commit-sha provenance and keep real files in the working tree for the
  Halob watcher.

### IC-03 — ADR index + documentation wiring

- **Purpose**: make the ADRs discoverable and establish the `doc/decisions/`
  convention for future ADRs.
- **Relevant requirements**: FR-006, NFR-003.
- **Affected surfaces**: `doc/decisions/README.md`, `doc/index.md`.
- **Sequencing/depends-on**: IC-01, IC-02.
- **Risks**: low.
