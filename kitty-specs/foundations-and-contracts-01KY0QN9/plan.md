# Implementation Plan: M1 — Foundations & Contracts

**Branch**: `feat/foundations-and-contracts` | **Date**: 2026-07-20 | **Spec**: [spec.md](./spec.md)

## Summary

Build the pure, tested foundation library: a typed metadata schema with
directory-inheritance and two-tier population, the NATS event contracts, the sink
interface, deterministic doc-id + content-hash staleness, and a Mind of Steele
enrichment adapter boundary. TDD throughout; no external services.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: pydantic v2 (models/validation), pyyaml (metadata.yaml),
python-frontmatter (markdown frontmatter). All already declared.
**Storage**: Files (`metadata.yaml`, markdown frontmatter) — no database in M1.
**Testing**: pytest via `uv run pytest`; TDD (failing test first); fully offline
(no network/services).
**Target Platform**: Python library imported by later missions.
**Project Type**: single
**Performance Goals**: N/A (pure in-memory library; doc-id hashing is O(file size)).
**Constraints**: no external services; `mypy`/`ruff`/`black` clean; provenance
(`raw_path`/`raw_commit`) first-class.
**Scale/Scope**: ~5 modules + tests under `src/goldberg_system/` and `tests/`.

## Charter Check

*GATE: Must pass before Phase 0.*

- **TDD mandatory** — honoured: failing tests precede each module. PASS.
- **Quality gates** — ruff/black/mypy clean + green tests before merge. PASS.
- **Provenance / raw-immutability** — the schema carries `raw_path`/`raw_commit`;
  doc-id/staleness key off the raw file. PASS.
- **Decision fidelity** — schema matches `doc/design.md`; Papra mapping per ADR
  0003. PASS.

PASS.

## Project Structure

```
src/goldberg_system/
├── metadata/
│   ├── schema.py        # DocumentMetadata (pydantic) + field semantics
│   └── inheritance.py   # locked/overridable/non-inherited/irreversible resolver
├── events/
│   └── contracts.py     # RawIngestedEvent, IndexedEvent
├── sinks/
│   └── base.py          # Sink interface (Protocol/ABC)
├── identity/
│   └── docid.py         # deterministic doc-id + content-hash staleness
└── enrichment/
    └── adapter.py       # EnrichmentAdapter Protocol (Mind of Steele boundary)

tests/
├── test_metadata_schema.py
├── test_metadata_inheritance.py
├── test_events.py
├── test_sinks.py
├── test_docid.py
└── test_enrichment_adapter.py

doc/
└── reuse/mind_of_steele.md   # how MoS is resolved (git source / vendored)
```

**Structure Decision**: single package `goldberg_system`; one sub-package per
concern. Pure library — imported by later missions; no runtime entrypoint added
beyond the existing CLI.

## Implementation Concern Map

### IC-01 — Metadata schema + inheritance
- **Purpose**: the typed metadata model (all design.md fields + Papra mapping) and
  the directory-inheritance resolver with two-tier population + safe defaults.
- **Relevant requirements**: FR-001, FR-002, FR-003, FR-004, FR-009, NFR-001,
  NFR-002, C-003, C-004.
- **Affected surfaces**: `metadata/schema.py`, `metadata/inheritance.py`, tests.
- **Sequencing/depends-on**: none.
- **Risks**: getting inheritance semantics + safe-default flags exactly right.

### IC-02 — Event contracts
- **Purpose**: serialisable `goldberg.raw.ingested` / `goldberg.indexed` models,
  source-agnostic, optional Papra `documentId`.
- **Relevant requirements**: FR-005, NFR-001.
- **Affected surfaces**: `events/contracts.py`, tests.
- **Sequencing/depends-on**: IC-01 (embeds metadata).

### IC-03 — Sink interface
- **Purpose**: the abstraction M4 writers implement.
- **Relevant requirements**: FR-006.
- **Affected surfaces**: `sinks/base.py`, tests (a fake sink).
- **Sequencing/depends-on**: IC-01, IC-02.

### IC-04 — doc-id + staleness
- **Purpose**: deterministic id keyed on the raw file; content-hash staleness rule.
- **Relevant requirements**: FR-007, FR-008, NFR-003.
- **Affected surfaces**: `identity/docid.py`, tests.
- **Sequencing/depends-on**: none.

### IC-05 — Mind of Steele adapter boundary
- **Purpose**: a typed Protocol for the enrichment capability M3 will wire; plus a
  doc resolving how MoS is obtained.
- **Relevant requirements**: FR-010.
- **Affected surfaces**: `enrichment/adapter.py`, `doc/reuse/mind_of_steele.md`.
- **Sequencing/depends-on**: none.
