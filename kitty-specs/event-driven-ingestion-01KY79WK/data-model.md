# Phase 1 Data Model — Event-Driven Trigger-Based Ingestion

Entities and message shapes. Existing entities are reused unchanged; only the new
event/message types are introduced.

## Commit event (NEW — NATS message)

The unit of ingest work. Published by the trigger, consumed by the processor.

| Field | Type | Notes |
|-------|------|-------|
| `sha` | string (40 hex) | `goldberg-raw` commit SHA. Also set as `Nats-Msg-Id` for dedup. |
| `ts` | string (ISO-8601 UTC) | Commit/publish time. |
| `source` | string | Emitter tag, e.g. `post-commit` / `post-merge` / `manual`. |

- **Subject**: `goldberg.raw.commit`
- **Stream**: `GOLDBERG` (subjects `goldberg.>`), durable, `limits` retention,
  dedup on `Nats-Msg-Id`.
- **Invariant**: a message references a commit; the processor derives the file set
  from the commit (never trusts a file list in the payload).

## Durable consumer config (NEW)

| Field | Value | Notes |
|-------|-------|-------|
| `durable` | `ingest-processor` | Survives restarts; resume from last ack. |
| `ack_policy` | explicit | ack on success, `term` on terminal failure, `nak` on transient. |
| `max_deliver` | 5 (default, configurable) | NFR-004 bound before DLQ. |
| `ack_wait` | ~300s | Covers Docling OCR + enrich latency. |
| `filter_subject` | `goldberg.raw.commit` | Only commit events. |

## Provenance manifest entry (EXISTING — reused, unchanged)

`sha256 → {raw_path, raw_commit, matters, document_type, origin}`. Written by
`refresh_provenance` / `build_entry` **before** indexing (C-001). Persisted atomically
to `config/provenance-manifest.json`.

## Document (EXISTING — reused, unchanged)

Enriched, indexed record with deterministic `doc_id` (from `raw_path` + content) and
provenance fields linking to raw (`raw_commit`, `raw_sha256`, `matters`). Produced by
`build_enriched_from_raw` + sinks.

## Pipeline event (EXISTING — reused, unchanged)

`PipelineEvent` (`source`, `stage`, `status`, `run_id`, `sha256`, `raw_path`,
`reason`, `error`, `doc_id`, `ts`) written to `goldberg_pipeline_events`. Stages:
`received | extracted | enriched | indexed` × `ok | started | skipped | failed`.
The processor emits the same events so `status`/`dlq`/`trace` are unchanged (FR-010).
A terminal (post-`max_deliver`) failure is recorded as an `enriched`/`indexed`
`failed` event = the DLQ record (FR-009).

## State transitions (per commit message)

```
received(commit) ──> for each changed allowlisted file:
    provenance-registered ──> extracted ──(non-empty)──> enriched ──> indexed(ok)  ──ACK
                                   │                          │
                                   └─(docling down)─ failed ──┤ (transient) ─ NAK ─> redeliver
                                                              └─(exhausted max_deliver)─ TERM ─> DLQ event
already-indexed SHA ──> skipped (idempotent) ──ACK
```

- **Atomicity**: per-file; one bad file dead-letters without failing sibling files or
  the whole commit. A commit message is ACKed once all its files reach a terminal
  state (indexed/skipped/DLQ).
- **Idempotency**: `Nats-Msg-Id = sha` (stream dedup) + `skip_shas = already_indexed()`
  + deterministic `doc_id` ⇒ redelivery/overlap indexes nothing new.

## Catch-up pass (startup, one-shot) — no new persistent state

Reads the manifest + `already_indexed()` set, selects the bounded pending difference,
runs `process_one` per file, emits events. Emits a distinct `run_id` prefix
(`catchup-…`) to distinguish from live `ingest-…` events in `trace`.
