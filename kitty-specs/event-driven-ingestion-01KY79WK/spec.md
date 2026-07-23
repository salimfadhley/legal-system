# Specification: Event-Driven Trigger-Based Ingestion

**Mission**: event-driven-ingestion-01KY79WK
**Type**: software-dev
**Target branch**: feat/goldberg-nats-es-archive
**Created**: 2026-07-23

## Overview

The Goldberg platform promises that a document committed to `goldberg-raw` is
automatically extracted, enriched, indexed, and made queryable — with full
provenance — with no manual step. Today that promise is kept by a **polling
reconciler** (`goldberg watch`) that re-hashes the entire allowlisted corpus every
two minutes, ingests the difference, and retries failures forever. This is
wasteful (constant whole-corpus hashing and repeated LLM calls on documents that
can never succeed) and contradicts charter **DIR-004 — "trigger, don't poll;
event-driven by default, NATS-first."**

This mission replaces the reconciler with an **event-driven** ingestion flow:

1. A **git `post-commit` trigger** on `goldberg-raw` publishes each commit to a
   NATS JetStream subject.
2. A **durable processor** (JetStream consumer) performs provenance-first
   `extract → enrich → index` for the files changed by that commit.
3. A **bounded, one-shot startup catch-up** sweeps anything committed while the
   processor was down, so no document is silently dropped.

It also fixes an enrichment defect where oversized documents exceed the language
model's context window and fail permanently, and it **decommissions the
reconciler** (service + daemon + ADR).

The downstream `nats-es-archive` forensic-archive mission is **out of scope** and
consumes the same bus separately.

## User Scenarios & Testing

### Primary scenario — a document is committed

1. A contributor adds `evidence/.../new_witness_statement.pdf` to `goldberg-raw`
   and commits it.
2. The commit fires the trigger, which publishes a `goldberg.raw.commit` message
   (carrying the commit SHA) to NATS JetStream.
3. The processor receives the message, resolves the files the commit
   added/modified, and for each: registers provenance in the manifest **before**
   indexing, extracts text (Docling for OCR-needing files; passthrough for
   text/markdown), enriches it, and indexes it into Elasticsearch — emitting a
   pipeline event at each stage.
4. Within a short bound of the commit, the document is queryable, with a
   `raw_commit` + `raw_sha256` + `matters` provenance record.

### Exception — processor was offline when the commit happened

1. The processor is down (redeploy) when three commits land.
2. On next startup, the **one-shot catch-up** diffs `goldberg-raw` against what is
   already indexed (by content SHA-256) and ingests only the missing documents,
   then hands over to live event processing. Nothing is silently missed.

### Exception — a document cannot be enriched normally (oversized)

1. A committed file is far larger than the model context window (e.g. a large OCR
   `combined.tsv`).
2. Enrichment bounds the text sent to the model so the request stays within the
   context window; the document is indexed (on the bounded text) rather than
   hard-failing.
3. If a document still cannot be processed after the configured delivery attempts,
   it is **parked in the DLQ** (a durable failed record) and is **not** retried in
   a tight loop.

### Exception — Docling (OCR) is unreachable

1. An OCR-needing file is committed while Docling is down.
2. That message fails and is redelivered by JetStream with backoff; text/markdown
   files committed in the same window still flow. When Docling recovers, the
   parked message is processed. No document is indexed empty.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-001 | A git `post-commit` trigger on `goldberg-raw` publishes a commit-level message (containing the commit SHA) to a NATS JetStream subject on the shared bus for every commit that changes allowlisted files. | Draft |
| FR-002 | Publishing the trigger message must not block or fail the developer's `git commit`; a publish failure is logged and recoverable via the catch-up path, never lost silently. | Draft |
| FR-003 | A durable JetStream consumer (the processor) receives commit messages and, for each, resolves the set of files that commit added or modified under the allowlisted trees. | Draft |
| FR-004 | For each resolved file, the processor registers a provenance manifest entry (sha256 + raw_commit + matters/document_type/origin) **before** the document is indexed. | Draft |
| FR-005 | After provenance, the processor extracts (Docling for OCR-needing files, passthrough for text/markdown), enriches, and indexes each document, reusing the existing proven per-document ingestion path (not a reimplementation). | Draft |
| FR-006 | Ingestion is idempotent by content SHA-256: re-delivery of a message, or re-processing of an already-indexed document, indexes nothing new and creates no duplicate (deterministic doc_id). | Draft |
| FR-007 | On startup the processor runs exactly one bounded catch-up pass that diffs `goldberg-raw` against the already-indexed set and ingests only the difference, then transitions to live event processing. | Draft |
| FR-008 | Enrichment bounds the document text supplied to the language model to stay within the model context window, so an oversized document is enriched on bounded text instead of failing with a context-length error. | Draft |
| FR-009 | A message that still fails after the configured maximum delivery attempts is parked in the DLQ as a durable failed record and is not retried further until explicitly requeued. | Draft |
| FR-010 | Every stage (received / extracted / enriched / indexed / failed) emits a pipeline event consistent with the existing observability model, so `goldberg status`, `dlq`, and `trace` continue to work unchanged. | Draft |
| FR-011 | The polling reconciler is decommissioned: the `goldberg-reconciler` deployment service and the `goldberg watch` daemon path are removed, and an ADR supersedes ADR 0011. | Draft |
| FR-012 | A one-time backfill re-ingests the documents left unindexed by the retired reconciler (the oversized OCR files) via the new path, so the corpus is complete after cutover. | Draft |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|----|-------------|-----------|--------|
| NFR-001 | Timeliness: a committed document becomes queryable promptly after the commit under normal load. | ≤ 60 seconds median from commit to indexed (single document, Docling healthy). | Draft |
| NFR-002 | No steady-state polling: in the absence of new commits the system performs no repeated whole-corpus hashing or scanning. | 0 whole-corpus scans while idle (the only scan is the one-shot startup catch-up). | Draft |
| NFR-003 | No silently dropped documents: every committed allowlisted document is eventually indexed or has a durable failed (DLQ) record. | 100% of committed allowlisted files reach `indexed/ok` or a DLQ record; verified by `goldberg audit`. | Draft |
| NFR-004 | Bounded failure handling: no document is retried more than the configured delivery-attempt limit before being parked. | ≤ configured max-deliver (default ≤ 5) attempts per document before DLQ. | Draft |
| NFR-005 | Resource-bounded on the petite Halob host: bounded consumer concurrency and Docling memory cap; a large document degrades (DLQ + retry) rather than taking the host down. | Conservative concurrency (≤ 2 workers default); host stays responsive during a large-scan attempt. | Draft |
| NFR-006 | Test-first: new behavior is covered by tests written before implementation (project TDD standard), including oversized-enrichment and idempotent-redelivery cases. | Failing test precedes each implementation change; suite green at completion. | Draft |

### Constraints

| ID | Constraint | Status |
|----|------------|--------|
| C-001 | Provenance-before-index is inviolable (DIR-001): no document may be indexed without a manifest entry written first. | Draft |
| C-002 | Elasticsearch and NATS are shared infrastructure already running on Halob; they must be connected to over TCP, never re-created or bundled. The existing indexed corpus must not be endangered. | Draft |
| C-003 | `goldberg-raw` + the provenance manifest remain the system of record; Elasticsearch is a derived, rebuildable view. | Draft |
| C-004 | Reuse the existing per-document ingestion path and observability event model; do not fork parallel extract/enrich/index or event logic. | Draft |
| C-005 | Documentation must stay synchronized (DIR-002): the ingestion runbook, deployment topology (ADR 0012), and ADR 0011 supersession are updated as part of the mission. | Draft |
| C-006 | Bringing the new processor up on Halob and stopping the reconciler service in the live deployment is an operational step gated on verification; the reconciler container has already been stopped. | Draft |

## Success Criteria

- **SC-001**: A document committed to `goldberg-raw` is queryable within ≤ 60s
  (median) with correct provenance, driven purely by the commit event.
- **SC-002**: While no commits occur, the system performs zero whole-corpus scans
  (observably no reconcile-style events), and consumes no LLM calls.
- **SC-003**: After a processor restart during which documents were committed, the
  startup catch-up indexes exactly the missing documents and nothing else.
- **SC-004**: An oversized document that previously failed with a context-length
  error now indexes successfully (on bounded text); the DLQ no longer accumulates
  repeated failures for the same document.
- **SC-005**: `goldberg audit` reports 100% coverage (every committed allowlisted
  file indexed or with a durable DLQ record); `goldberg status` returns to
  `healthy`.
- **SC-006**: The reconciler is fully decommissioned — no `goldberg-reconciler`
  service, no `goldberg watch` daemon path — and an ADR records the supersession.

## Key Entities

- **Commit event** — a NATS JetStream message keyed by a `goldberg-raw` commit
  SHA; the unit of ingestion work.
- **Provenance manifest entry** — `sha256 → {raw_path, raw_commit, matters,
  document_type, origin}`; written before indexing.
- **Document** — a derived, enriched, indexed record with a deterministic
  `doc_id` and provenance linking back to raw.
- **DLQ record** — a durable "failed" pipeline event for a document that exhausted
  delivery attempts.
- **Processor** — the durable event consumer (replaces the reconciler daemon).
- **Trigger** — the git `post-commit` hook/publisher (replaces the poll).

## Domain Language

- **Reconciler** — the *retired* polling daemon (`goldberg watch`). Do not use
  "reconciler" for the new component.
- **Processor** — the new event-driven consumer. Canonical term for the new
  ingest worker.
- **Trigger** — the git-hook publisher that emits commit events.
- **Catch-up** — the *one-shot, startup-only* reconcile sweep. Not a poll; avoid
  calling it "the reconciler."
- **Provenance-before-index** — the invariant that a manifest entry is written
  before any document is indexed.

## Assumptions

- Commits reach Halob's `goldberg-raw` working tree such that a `post-commit`
  (and/or `post-merge`) hook can fire where `git` runs; NATS is reachable from
  both the Halob host and the SMB-mounting client. **The plan phase resolves the
  exact commit-delivery path** (authored on the mounted tree vs pulled) and
  whether a lightweight periodic backstop is warranted; the baseline in scope is
  the one-shot startup catch-up only.
- A *fast-forward* `git pull` does not fire commit hooks; documents arriving that
  way are covered by the next startup catch-up (accepted baseline; revisited in
  plan).
- JetStream stream/subject naming aligns with the existing `goldberg.>`
  convention referenced by the downstream archive mission.
- OpenAI (cloud) enrichment remains permitted per ADR 0001 / the charter's logged
  data-boundary exception.

## Out of Scope

- The `nats-es-archive` forensic-archive mission (archiving `goldberg.>` messages
  to an Elasticsearch forensic index) — separate, already specified, downstream.
- Recreating or migrating Elasticsearch / NATS.
- Rewriting the extraction, enrichment, or indexing internals beyond the
  token-safety fix and the reuse wiring.
- A GitHub-webhook trigger path (the accepted trigger is the local git-hook;
  webhook remains a possible future increment).
