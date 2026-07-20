# Specification: M1 — Foundations & Contracts

**Mission type:** software-dev · **Status:** Draft · **Branch:** `feat/foundations-and-contracts`

## Overview

M1 delivers the shared, tested **foundation library** every downstream pipeline
mission builds against — so extract (M2), enrich (M3), sinks (M4), and trigger
(M6) depend on fixed, typed contracts rather than inventing their own. It is
**pure, mockable library code**: no external services are required to build or
test it.

Scope: (1) a typed **metadata schema**; (2) the **NATS event contracts**; (3) the
**sink interface**; (4) **deterministic doc-id + content-hash staleness**; (5)
**Mind of Steele reuse resolution**. It incorporates the Papra cross-store mapping
from [ADR 0003](../../doc/decisions/0003-document-management-papra-integration.md).

## User Scenarios & Testing

### Primary scenario

As a **developer building a later pipeline mission**, I import the foundation
library and get a validated metadata model, the event message shapes, the sink
abstraction, and the doc-id/staleness rule — all typed and tested — so I build my
stage against them without re-deriving shared definitions.

### Acceptance scenarios

1. **Given** a set of `metadata.yaml` files down a directory tree, **when** they
   are resolved, **then** inheritance follows the agreed semantics (locked /
   overridable / non-inherited / irreversible) and conflicts on locked fields
   raise a clear error.
2. **Given** a document with no human-authored legal-handling flags, **when** its
   metadata is materialised, **then** each handling flag takes its **safe**
   default (treated as sensitive/unknown until cleared).
3. **Given** the same raw file, **when** the doc-id is computed twice, **then**
   the id is identical; **and** an unchanged raw file is reported **not stale**.
4. **Given** an `goldberg.raw.ingested` event produced by either the filesystem
   watcher or the Papra webhook bridge, **when** it is (de)serialised, **then** it
   round-trips losslessly and carries an optional Papra `documentId`.

### Edge cases

- A locked field set to conflicting values at two levels → error (not silent
  last-wins).
- `matters` is a list; a document may belong to several, with an optional
  `primary_matter`.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | Provide a typed metadata model covering all fields in `doc/design.md`'s schema table (ported goldberg-meta fields plus `author`/`source_party`, `matters`, `origin`/`role`, `entities`, `raw_path`, `raw_commit`, `source_channel`, `disclosure_status`, `cpia_s17`, `privileged`, `sensitivity`, `relates_to`). | Proposed |
| FR-002 | Resolve directory-inherited `metadata.yaml` files with locked / overridable / non-inherited / irreversible semantics; locked-field conflicts raise. | Proposed |
| FR-003 | Two-tier population: machine-derived fields are optional/overridable; legal-handling flags (`cpia_s17`, `privileged`, `sensitivity`, `disclosure_status`, `source_channel`) are human-authored and default to the **safe** value when absent. | Proposed |
| FR-004 | Represent `matters` as a list with an optional `primary_matter`. | Proposed |
| FR-005 | Provide serialisable event-contract models for `goldberg.raw.ingested` and `goldberg.indexed` (raw path, commit sha, mime, metadata), source-agnostic (watcher or Papra webhook) with an optional Papra `documentId`. | Proposed |
| FR-006 | Provide a **sink interface** (abstraction) that M4 writers (extracted writer, Elasticsearch indexer, RAG sink) implement. | Proposed |
| FR-007 | Provide deterministic **doc-id** derivation keyed on the raw file (path + content), stable and reproducible across runs. | Proposed |
| FR-008 | Provide a **content-hash staleness** check that decides whether a raw file needs reprocessing. | Proposed |
| FR-009 | Represent the Papra cross-store mapping (`documentId` ↔ `raw_path`/`raw_commit`) in the schema (ADR 0003). | Proposed |
| FR-010 | Resolve **Mind of Steele reuse**: define the import boundary (a typed adapter/Protocol for the enrichment capability M3 will wire) and document how MoS is obtained (git source / vendored). | Proposed |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|---|---|---|---|
| NFR-001 | Models validate input and round-trip (serialise/deserialise) losslessly. | 100% of public models covered by passing tests | Proposed |
| NFR-002 | Legal-handling flags default safe when absent. | verified by a test per flag (5/5) | Proposed |
| NFR-003 | doc-id is deterministic. | identical inputs → identical id, asserted by test | Proposed |
| NFR-004 | The test suite runs with no external services. | 0 network/service dependencies; suite passes offline | Proposed |
| NFR-005 | Quality gates pass. | `ruff` clean, `black`-formatted, `mypy` clean, all tests green | Proposed |

### Constraints

| ID | Constraint | Status |
|---|---|---|
| C-001 | Pure library code — no network/external services, no pipeline execution. | Proposed |
| C-002 | TDD — a failing test is written before each implementation. | Proposed |
| C-003 | Provenance is first-class — `raw_path` + `raw_commit` are required carriers on derived records. | Proposed |
| C-004 | Reuse goldberg-meta's inheritance semantics; do not reinvent the model. | Proposed |

## Success Criteria

| ID | Criterion |
|---|---|
| SC-001 | A later mission can import the schema, event contracts, sink interface, and doc-id/staleness utilities and build against them unchanged. |
| SC-002 | The metadata schema expresses every field in `doc/design.md`'s schema table plus the Papra mapping. |
| SC-003 | 100% of the foundation's public surface is covered by passing tests; `mypy`/`ruff`/`black` clean. |

## Key Entities

- **DocumentMetadata** — the typed metadata model + inheritance resolver.
- **MatterRef / matters** — multi-valued case identifiers + optional primary.
- **RawIngestedEvent / IndexedEvent** — the NATS message contracts.
- **Sink** — the interface M4 writers implement.
- **doc-id / staleness** — deterministic id + content-hash reprocessing rule.
- **EnrichmentAdapter (Protocol)** — the Mind of Steele reuse boundary.

## Assumptions

- pydantic v2 is the modelling library (already a dependency).
- MoS is resolved behind an adapter now; actual wiring happens in M3.
- Field semantics follow `doc/design.md` and the goldberg-meta port.

## Out of Scope

- Actual extraction, enrichment, indexing, triggering (M2–M6).
- Wiring the real Mind of Steele implementation (M3) or the Papra client (M2).
- Any running service or network I/O.
