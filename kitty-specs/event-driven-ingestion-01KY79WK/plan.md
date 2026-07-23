# Implementation Plan: Event-Driven Trigger-Based Ingestion

**Branch**: `feat/goldberg-nats-es-archive` | **Date**: 2026-07-23 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `kitty-specs/event-driven-ingestion-01KY79WK/spec.md`

## Summary

Replace the polling reconciler (`goldberg watch`) with an event-driven ingest
flow per charter DIR-004. A git `post-commit`/`post-merge` hook on `goldberg-raw`
publishes each commit SHA to a NATS JetStream subject (`goldberg.raw.commit`) via
a new `goldberg publish-commit` CLI. A durable JetStream **processor** consumes
those messages and, for each commit's changed allowlisted files, runs the existing
provenance-first `process_one` path (manifest → Docling/passthrough → enrich →
index), with JetStream `max_deliver` + DLQ for retries. A bounded **one-shot
startup catch-up** diffs raw-vs-indexed once on boot to sweep anything missed while
down. The enrichment step is made token-safe so oversized documents index on
bounded text instead of failing with `context_length_exceeded`. The reconciler is
then decommissioned (CLI `watch` path + compose service removed; ADR 0013
supersedes ADR 0011). The downstream `nats-es-archive` archive mission is out of
scope and consumes the same bus separately.

## Technical Context

**Language/Version**: Python 3.12+ (uv for deps and execution)
**Primary Dependencies**: `nats-py` (JetStream client — new), `openai` (existing),
`tiktoken` (new — precise token budgeting for the enrich fix), and existing
in-repo modules reused verbatim: `migrate/reingest.py` (`process_one`),
`enrichment/`, `migrate/manifest.py`, `extract/docling_client.py`, `sinks/`,
`observability/events.py`, `reconcile/reconciler.py` (catch-up diff logic), `click` CLI.
**Storage**: Elasticsearch (external/shared on Halob — corpus + `goldberg_pipeline_events`),
NATS JetStream (external/shared — durable `GOLDBERG` stream is the event system of
record), provenance manifest JSON (`config/provenance-manifest.json`, writable volume).
**Testing**: pytest, TDD (failing test precedes each change); unit tests with injected
fake NATS + fake OpenAI clients; opt-in integration tests (`GOLDBERG_INTEGRATION=1`)
against real NATS/ES/Docling into isolated `*_test` streams/indices.
**Target Platform**: Linux (docker container) on Halob — petite 4-core host.
**Project Type**: single (Python package + CLI + long-running service).
**Performance Goals**: ≤ 60s median commit→indexed (single doc, Docling healthy);
zero whole-corpus scans while idle.
**Constraints**: provenance-before-index inviolable (DIR-001); connect-only to ES/NATS,
never recreate (C-002); idempotent by content SHA-256; ≤ 2 concurrent workers; a large
document degrades to DLQ+retry rather than OOMing the host.
**Scale/Scope**: ~2,000-document corpus; low commit rate (human-driven); single processor
instance.

## Charter Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **DIR-001 (provenance-loss is the highest-cost failure)** — ✅ Satisfied. The
  processor reuses `process_one`, which registers the manifest entry before
  indexing; C-001 makes this inviolable. No new provenance path is forked.
- **DIR-002 (docs stay synchronized)** — ✅ In scope. ADR 0013 (supersedes 0011),
  ADR 0012 deploy-topology update, and the ingestion runbook are mission deliverables
  (C-005, IC-06).
- **DIR-003 (diagnose from observability first)** — ✅ Reuses the existing pipeline-event
  model verbatim; `status`/`dlq`/`trace` keep working (FR-010).
- **DIR-004 (trigger, don't poll — NATS-first)** — ✅ This mission's core purpose:
  commit-triggered, NATS-first, no steady-state polling (NFR-002).
- **DIR-005 (query-usage docs release-current)** — ✅ Not affected (no query-surface change).

**No violations.** Complexity Tracking omitted.

## Project Structure

### Documentation (this mission)

```
kitty-specs/event-driven-ingestion-01KY79WK/
├── plan.md              # This file
├── research.md          # Phase 0 output — resolved design decisions
├── data-model.md        # Phase 1 output — entities & message shapes
├── quickstart.md        # Phase 1 output — run/deploy/verify
├── contracts/           # Phase 1 output — NATS + CLI + consumer contracts
└── tasks.md             # Phase 2 (/spec-kitty.tasks — NOT created here)
```

### Source Code (repository root)

```
src/goldberg_system/
├── messaging/                 # NEW — NATS JetStream boundary (connect/publish/consume)
│   ├── __init__.py
│   ├── client.py              # JetStream connect + stream/consumer ensure (idempotent)
│   ├── publisher.py           # publish a commit event (used by the CLI trigger)
│   └── config.py              # NATS_URL / stream / subject / durable names from config+env
├── ingest/                    # NEW — the event-driven ingest service
│   ├── __init__.py
│   ├── commit_files.py        # resolve changed allowlisted files for a commit SHA (git diff-tree)
│   ├── processor.py           # durable consumer: message -> per-file process_one; ack/nak/term + DLQ
│   └── catchup.py             # bounded one-shot startup reconcile (reuses reconcile diff)
├── enrichment/
│   └── openai_enricher.py     # MODIFIED — token-safe budgeting (tiktoken) + context-length retry
├── cli.py                     # MODIFIED — add `publish-commit`, `ingest-serve`; remove `watch`
├── reconcile/                 # REMOVED at cutover (daemon path retired; catch-up diff extracted first)
└── service/                   # unchanged (already-retired Papra path; untouched)

hooks/
└── goldberg-raw/post-commit   # NEW — shipped hook: calls `goldberg publish-commit "$(git rev-parse HEAD)"`
                               #        (installed via core.hooksPath; post-merge symlink for pull-merges)

deploy/
├── docker-compose.yml         # MODIFIED — replace `reconciler` service with `ingest` (goldberg ingest-serve)
└── Dockerfile.ingest          # NEW (or reuse Dockerfile.reconciler renamed)

doc/decisions/0013-event-driven-ingestion.md   # NEW — supersedes ADR 0011
doc/runbooks/…                                  # MODIFIED — ingestion runbook

tests/
├── unit/        # enrich token-budget, commit_files resolution, processor ack/nak/term, catchup diff
└── integration/ # end-to-end: commit -> publish -> consume -> index (opt-in)
```

**Structure Decision**: Single Python package. Two new sub-packages —
`messaging/` (the NATS boundary, so JetStream specifics live behind one seam and
are injectable/fakeable in tests) and `ingest/` (the service that wires the bus to
the existing `process_one`). The existing ingest internals (`reingest.process_one`,
`enrichment`, `manifest`, `docling_client`, `sinks`, `observability.events`) are
reused unchanged except for the enricher token fix (C-004: no forked logic). The
reconciler's raw-vs-indexed **diff** logic is extracted for the catch-up before the
daemon path is deleted.

## Complexity Tracking

*No Charter Check violations — section intentionally empty.*

## Implementation Concern Map

> Concerns are architectural areas, not work packages. `/spec-kitty.tasks` maps
> these to WPs.

### IC-01 — Enrichment token-safety (the fold-in fix)

- **Purpose**: Bound the text sent to the model so oversized documents index on
  truncated text instead of failing `context_length_exceeded` and storming.
- **Relevant requirements**: FR-008, NFR-004, SC-004.
- **Affected surfaces**: `enrichment/openai_enricher.py`; new unit tests.
- **Sequencing/depends-on**: none (independent; highest immediate value — closes the
  observed failure class regardless of the trigger rework).
- **Risks**: token estimate vs true tokenizer; mitigate with `tiktoken` + a
  defensive context-length retry that shrinks the body.

### IC-02 — Messaging boundary (NATS JetStream)

- **Purpose**: One injectable seam for connecting, ensuring the durable stream, and
  publishing/consuming — so the rest of the code never imports `nats-py` directly.
- **Relevant requirements**: FR-001, FR-003, FR-009, C-002.
- **Affected surfaces**: `messaging/` (new); config/env for `NATS_URL`, stream/subject/durable.
- **Sequencing/depends-on**: none (foundation).
- **Risks**: stream/consumer must be ensured idempotently; naming must align with the
  downstream `nats-es-archive` `goldberg.>` convention.

### IC-03 — Trigger (git hook + publisher CLI)

- **Purpose**: Emit a commit event on every `goldberg-raw` commit without blocking `git`.
- **Relevant requirements**: FR-001, FR-002.
- **Affected surfaces**: `messaging/publisher.py`, `cli.py` (`publish-commit`),
  `hooks/goldberg-raw/post-commit` (+ `post-merge`), install docs.
- **Sequencing/depends-on**: IC-02.
- **Risks**: hook must be non-fatal on publish failure (FR-002); fast-forward pulls
  don't fire hooks (covered by IC-05 catch-up).

### IC-04 — Processor (durable consumer → provenance-first ingest)

- **Purpose**: Consume commit events and ingest each commit's changed files via the
  existing `process_one`, with bounded retries and DLQ.
- **Relevant requirements**: FR-003, FR-004, FR-005, FR-006, FR-009, FR-010, NFR-001, NFR-004, NFR-005.
- **Affected surfaces**: `ingest/processor.py`, `ingest/commit_files.py`, `cli.py`
  (`ingest-serve`), reuse of `reingest.process_one` + `observability.events`.
- **Sequencing/depends-on**: IC-02 (bus), IC-01 (so oversized files don't DLQ).
- **Risks**: ack/nak/term semantics; ack_wait sized for Docling+enrich latency;
  idempotency on redelivery (skip already-indexed SHA).

### IC-05 — Startup catch-up (bounded one-shot)

- **Purpose**: On processor boot, ingest exactly the raw-vs-indexed difference once,
  then hand over to live events — the no-silently-dropped-document safety net.
- **Relevant requirements**: FR-007, NFR-002, NFR-003, SC-003.
- **Affected surfaces**: `ingest/catchup.py` (reuses the extracted reconcile diff),
  wired into `ingest-serve` startup.
- **Sequencing/depends-on**: IC-04 (shares the ingest path).
- **Risks**: must be one-shot (not a loop) to satisfy NFR-002; bounded batch to
  protect the petite host.

### IC-06 — Decommission reconciler + deployment + docs

- **Purpose**: Remove the retired polling path and record the decision.
- **Relevant requirements**: FR-011, C-005, SC-006.
- **Affected surfaces**: delete `reconcile/` daemon + `cli watch`; `deploy/docker-compose.yml`
  (`reconciler`→`ingest`); ADR 0013 (supersedes 0011); ADR 0012 + ingestion runbook edits.
- **Sequencing/depends-on**: IC-03, IC-04, IC-05 (new path must work before removing old).
- **Risks**: don't delete the catch-up diff logic when removing the daemon (extract first, IC-05).

### IC-07 — Backfill + verification

- **Purpose**: Re-ingest the documents the retired reconciler left unindexed (the two
  oversized OCR `.tsv`) via the new path; prove completeness.
- **Relevant requirements**: FR-012, NFR-003, SC-005.
- **Affected surfaces**: one-shot `ingest catchup` run; `goldberg audit` + `status` checks.
- **Sequencing/depends-on**: IC-01 (token fix), IC-05 (catch-up).
- **Risks**: verify audit reports 100% coverage and status returns healthy post-cutover.
