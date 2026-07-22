# NATS to Elasticsearch Message Archive

> **Status: DEFERRED / future mission.** Defined now for the roadmap; built after
> the event-driven git-hook → NATS ingestion redesign (DIR-004) lands, since it
> archives that message stream.

## Purpose

Give the ingestion *process* a durable, searchable, forensically-inspectable
history. As defence evidence, it is not enough to know a document's provenance;
we must be able to show **how it entered the corpus** — when, from which commit,
what was extracted/enriched, and what failed. This mission projects every NATS
pipeline message into Elasticsearch so that history is queryable alongside the
corpus itself.

## User Scenarios & Testing

### Primary scenario
An operator (or, later, the defence) asks "show me everything that happened to
document X" or "every extraction failure in July." Because every `goldberg.>`
NATS message was archived into Elasticsearch, they get a complete, ordered,
searchable trail — trigger event, extraction, enrichment, index, and any
dead-letter — without replaying streams by hand.

### Exception / edge scenarios
- **Archiver was down.** JetStream retains the messages; when the archiver
  restarts, its durable consumer resumes from its last position and back-fills —
  nothing is lost.
- **Redelivery / replay.** A message delivered more than once is written to the
  same ES document (idempotent), never duplicated.
- **High burst.** The archiver batches/bulk-indexes and applies backpressure
  rather than dropping messages.

### Acceptance
- Every message published to `goldberg.>` appears exactly once in the ES archive.
- The archive is queryable by document, matter, time, subject, and outcome.
- Restarting the archiver loses nothing (resumes from JetStream).

## Domain Language
- **System of record** — the JetStream stream(s): the durable, replayable log.
- **Forensic view** — the Elasticsearch archive index: the searchable projection.
- **Archiver** — the durable JetStream consumer that mirrors messages into ES.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-001 | Run a durable **JetStream consumer** on the pipeline subjects (`goldberg.>`) that resumes from its last acknowledged position after any restart. | Draft |
| FR-002 | Project each message into an **Elasticsearch archive index** (extend/align with the existing `goldberg_pipeline_events` schema). | Draft |
| FR-003 | Be **idempotent**: derive the ES document id deterministically from the message (JetStream sequence / message id) so replays/redeliveries update, never duplicate. | Draft |
| FR-004 | Preserve enough of each message to reconstruct the process trail: subject, timestamp, correlation id (`raw_sha256`/doc_id), stage, status, and payload. | Draft |
| FR-005 | Make the archive queryable by document, matter, time window, subject, and outcome (success/failure). | Draft |
| FR-006 | Configure JetStream retention generously (the durable log) while relying on ES for long-term forensic search; document the retention/ILM choices. | Draft |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|----|-------------|-----------|--------|
| NFR-001 | No message loss across archiver restarts. | 100% of published messages archived after a restart (JetStream-backed resume). | Draft |
| NFR-002 | No duplicates under redelivery/replay. | Re-delivered message → same ES doc id; archive count unchanged. | Draft |
| NFR-003 | Lean footprint on the petite host. | Bulk/batched indexing; conservative resource use; no unbounded memory growth under burst. | Draft |

### Constraints

| ID | Constraint | Status |
|----|-----------|--------|
| C-001 | Reuse ES + NATS as the already-provisioned shared infrastructure; do not stand up new datastores. | Draft |
| C-002 | Align the archive schema with the existing `PipelineEvent`/`goldberg_pipeline_events` model rather than inventing a divergent one. | Draft |
| C-003 | Prefer a small custom archiver (control over mapping + idempotent id + schema consistency); off-the-shelf (Vector/Benthos/Logstash NATS→ES) is an acceptable fast-path if it meets FR-003. | Draft |
| C-004 | Ships tests + docs (an ADR + a runbook), per charter DIR-002. | Draft |

## Success Criteria
- **SC-001**: For any indexed document, the full ordered process trail (trigger → extract → enrich → index → any DLQ) is retrievable from the ES archive.
- **SC-002**: Killing and restarting the archiver mid-stream results in zero lost and zero duplicated archived messages.
- **SC-003**: The archive answers time/subject/outcome queries (e.g. "all extraction failures last week") without stream replay.

## Key Entities
- **ArchivedMessage** — { es_doc_id (deterministic), subject, ts, correlation_id, stage, status, payload }.
- **Archive index** — the ES index holding ArchivedMessages (a `goldberg_pipeline_events` successor/extension).

## Assumptions
- The event-driven redesign (DIR-004) publishes the ingestion lifecycle to NATS `goldberg.>` subjects; this mission consumes that stream.
- ES and NATS are external shared infrastructure (already running); this adds one consumer, no new storage.

## Future consideration (out of scope for v1, do not preclude)
- **Tamper-evident audit.** Because this is defence evidence, a later iteration may
  make the archive append-only and **hash-chained** (each event carrying the hash
  of its predecessor) so the ingestion history is evidentially defensible and
  cannot be silently rewritten. An append-only ES index / JetStream stream
  supports this; the v1 schema should leave room for a `prev_hash`/`hash` field.
