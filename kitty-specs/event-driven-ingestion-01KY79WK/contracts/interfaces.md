# Phase 1 Contracts — Event-Driven Trigger-Based Ingestion

The system is internal (a bus + a service + a CLI), so contracts are the NATS
message schema, the CLI surface, and the consumer behavior — not an HTTP API.

## 1. NATS message contract — `goldberg.raw.commit`

**Publish** (trigger → stream `GOLDBERG`):

- Headers: `Nats-Msg-Id: <sha>` (idempotent dedup).
- Body (JSON):
  ```json
  { "sha": "<40-hex commit sha>", "ts": "<ISO-8601 UTC>", "source": "post-commit" }
  ```
- Guarantees: publish is best-effort from the hook's perspective — a failure must
  **not** fail `git commit` (FR-002); the loss is recovered by startup catch-up.

**Consume** (stream → processor, durable `ingest-processor`):

- On receipt: resolve changed allowlisted files for `sha`; for each, run the
  provenance-first ingest; ack when all reach a terminal state.
- Retry: transient failure → `nak` (redeliver, backoff); after `max_deliver` → `term`
  + DLQ event.

## 2. CLI contract

### `goldberg publish-commit <sha> [--subject goldberg.raw.commit] [--source post-commit]`

- Publishes one commit event. Exit 0 on publish ack. Non-zero exit is tolerated by
  the hook (logged, non-fatal).
- Resolves `NATS_URL`, subject, stream from config + env.

### `goldberg ingest-serve [--durable ingest-processor] [--workers 2] [--max-deliver 5] [--batch 50] [--health-port 8098] [--no-catchup]`

- Startup: run **one** bounded catch-up pass (unless `--no-catchup`), then open the
  durable pull consumer and process events until stopped.
- Exposes `GET /health` with last-activity + catch-up summary.
- Never dies on a per-document error (per-file DLQ; service continues).

### `goldberg ingest catchup [--batch N]`

- Runs a single bounded catch-up pass and exits (manual escape hatch / backfill; FR-012).

### Removed: `goldberg watch`

- The polling reconciler command is deleted at cutover (FR-011). Its raw-vs-indexed
  diff logic is extracted into `ingest/catchup.py` **before** removal.

## 3. Git hook contract (`hooks/goldberg-raw/`)

- `post-commit` and `post-merge` (installed via `core.hooksPath`): each runs
  `goldberg publish-commit "$(git rev-parse HEAD)" --source <hook>` and **always
  exits 0** (publish failure is logged, never blocks git — FR-002).

## 4. Health / observability contract

- `GET /health` (processor) → `{ status, last_event_ts, catchup: {ran, indexed, elapsed} }`.
- Pipeline events unchanged (FR-010): `goldberg status`, `goldberg dlq`,
  `goldberg trace <key>` continue to work against `goldberg_pipeline_events`.

## 5. Enrichment contract (behavioral, FR-008)

- `enrich(request)` MUST NOT raise `context_length_exceeded` for any input size:
  the body is token-budgeted before the call, and a context-length rejection is
  retried with a shrunk body. Output shape (summary/keywords/entities/author/
  document_type/claims) is unchanged.
