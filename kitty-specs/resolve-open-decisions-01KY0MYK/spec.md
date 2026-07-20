# Specification: M0 — Resolve Open Platform Decisions

**Mission type:** software-dev (lightweight research spike — deliverables are decision records, not code)
**Status:** Draft
**Feature branch:** `feat/resolve-open-decisions`

## Overview

Before the Goldberg ingestion pipeline can be built, two architecture decisions
remain open and block downstream work. This mission settles them by producing a
documented recommendation for each, so the foundations mission (M1) and the
missions that depend on these choices (M4 sinks, M8 migration) proceed on settled
ground. No production code is written; the output is two Architecture Decision
Records (ADRs).

The two open decisions:

1. **Wiki / RAG sink backend** — which backend serves the searchable, attributed
   knowledge layer: Ragie (managed RAG, cloud permitted; Mind of Steele already
   ships an uploader), an Obsidian vault, or RAG-on-Elasticsearch.
2. **Large-binary handling in `goldberg-raw`** — how large binary originals (big
   PDFs, media) are stored: plain git vs git-LFS.

Two related decisions are already settled and are explicitly **out of scope**:
trigger locus (Halob-local filesystem watcher) and the data boundary (cloud
LLM/Ragie permitted as a logged exception).

## User Scenarios & Testing

### Primary scenario

As the **platform maintainer**, before starting the build, I need the wiki-sink
and large-binary questions settled so I can begin M1/M4/M8 without ambiguity. I
open `doc/decisions/`, read two ADRs, and each tells me the chosen option and why
it was chosen given the project's constraints.

### Acceptance scenarios

1. **Given** the two open decisions, **when** this mission completes, **then**
   `doc/decisions/` contains two ADRs, each stating a single recommended option
   with rationale, and no in-scope decision is left open.
2. **Given** an ADR, **when** a future contributor reads it, **then** they can
   understand the options considered, why the chosen option was selected, and
   which constraints must remain true — without consulting the author.
3. **Given** the settled decisions, **when** M4 (sinks) and M8 (migration) begin,
   **then** each finds its blocking decision already recorded and cited.

### Edge cases

- If a decision genuinely cannot be finalised without a prototype, its ADR records
  the **recommended** option as provisional and names the specific spike needed —
  but a firm recommendation is the default expectation.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | Produce an ADR that selects the wiki/RAG sink backend from {Ragie, Obsidian vault, RAG-on-Elasticsearch}, stating one recommended option with rationale. | Proposed |
| FR-002 | Produce an ADR that selects the large-binary handling approach for `goldberg-raw` from {plain git, git-LFS}, stating one recommended option with rationale. | Proposed |
| FR-003 | Each ADR documents: context, the options considered, the decision drivers, the decision, and its consequences. | Proposed |
| FR-004 | Each ADR names the downstream mission(s) it unblocks (wiki-sink → M4; large-binary → M8). | Proposed |
| FR-005 | The wiki-sink ADR evaluates each option against the attributed-retrieval and provenance needs (answers must cite source document, raw commit, speaker, date). | Proposed |
| FR-006 | The ADRs are stored under `doc/decisions/` in a consistent format and are discoverable from the documentation index. | Proposed |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|---|---|---|---|
| NFR-001 | Completeness — in-scope decisions resolved with a chosen option + rationale before acceptance. | 2 of 2 decisions resolved (100%) | Proposed |
| NFR-002 | Traceability — each ADR references the governing constraint(s) and the downstream mission(s) it unblocks. | ≥1 constraint reference and ≥1 downstream mission per ADR | Proposed |
| NFR-003 | Reviewability — each ADR follows the same section template (context / options / decision / consequences). | 100% of ADRs conform | Proposed |

### Constraints

| ID | Constraint | Status |
|---|---|---|
| C-001 | No production/pipeline code is produced in this mission (research spike only). | Proposed |
| C-002 | Decisions must respect the charter risk boundaries: provenance must remain preservable and `goldberg-raw` immutable; the cloud LLM/Ragie data-boundary exception applies. | Proposed |
| C-003 | The wiki-sink decision must account for reuse of Mind of Steele (which already provides a Ragie uploader) and Halob-hosted Elasticsearch. | Proposed |
| C-004 | Already-resolved decisions (trigger locus = Halob watcher; data boundary = cloud permitted) must not be re-litigated. | Proposed |

## Success Criteria

| ID | Criterion |
|---|---|
| SC-001 | Both open decisions (2 of 2) are resolved with a documented recommendation, so M1/M4/M8 can proceed without blocking on these questions. |
| SC-002 | A reader can determine, from each ADR alone, why the chosen option was selected and what constraints must remain true. |
| SC-003 | Zero unresolved decision markers remain at mission close. |

## Key Entities

- **Architecture Decision Record (ADR):** a short document capturing context,
  options considered, decision drivers, the decision, and consequences, plus the
  downstream mission(s) it unblocks.
- **Decision (in scope):** `wiki-sink-backend`; `large-binary-handling`.

## Assumptions

- Cloud services (OpenAI/Anthropic/Ragie) are permitted per the charter's logged
  data-boundary exception.
- Mind of Steele's Ragie uploader is reusable for the Ragie option.
- Halob hosts Elasticsearch and NATS, available to any on-network option.
- The corpus contains large binary originals (big PDFs, media), which is why the
  large-binary question is material.

## Out of Scope

- Trigger locus (already decided: Halob-local filesystem watcher).
- Data boundary (already decided: cloud LLM/Ragie permitted).
- Any pipeline implementation, and the wiring of the chosen backend (that is M4).
