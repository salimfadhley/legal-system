# ADR 0005 — Live pipeline: Papra-webhook-driven service (v1)

**Status:** Accepted · **Date:** 2026-07-21 · **Delivers:** M5 (service) + M6 (trigger) + M7 (deploy)

## Context

The pipeline needs to run automatically: a document lands → it is extracted,
enriched, and indexed without a manual `goldberg reindex`. The roadmap framed this
as a NATS-driven service (M5) fed by a Halob file-watcher or a Papra
`document:created` webhook bridged to NATS (M6).

Reality after M2–M5: the corpus is **Papra-centric**. Papra already ingests +
extracts (Docling) and fires a `document:created` webhook (Standard Webhooks, HMAC)
carrying the document. Our pipeline's job is the *downstream* enrich + index.

## Decision

For v1, run a single **`live-index` service** driven directly by the **Papra
webhook** — no NATS, no separate file-watcher:

- An HTTP endpoint `POST /webhooks/papra` receives `document:created`, returns 200
  immediately, and processes the document in a **background thread**: fetch content
  via the Papra API → enrich (OpenAI) → write to the sinks (ES indexer + extracted
  writer). `GET /health` for liveness.
- Deployed as a container on Halob; the Papra webhook points at it.
- Built on the stdlib HTTP server + threads (no new web framework dependency).

Rationale: fewest moving parts that deliver "drop a doc → it's indexed"; the Papra
webhook *is* the trigger (M6), the background processing *is* the service (M5), and
one container is the deploy (M7). Papra's own store/queue gives us durability up to
the webhook; our processing is idempotent (deterministic doc-id), so a missed
webhook is recovered by re-running `goldberg reindex`.

## Consequences

- **NATS is deferred**, not used in v1. If we later need cross-service decoupling,
  durable retries, or a `goldberg.indexed` event bus, add JetStream then. The
  config's NATS subjects remain reserved.
- At-most-once webhook processing: a crash mid-process drops that one document;
  `goldberg reindex` is the backstop (idempotent).
- HMAC signature verification is supported but optional (LAN-internal); enable by
  configuring the webhook signing secret.
- The `.eml` gap remains: Papra doesn't extract email bodies, so those won't
  produce content via this path until we route them through our own eml extractor
  (a full-migration / M8 concern).

## Downstream

Supersedes the NATS framing of M5/M6 for v1 (recorded, not deleted). M7 = the
container deploy. A future ADR can reinstate NATS if durability needs grow.
