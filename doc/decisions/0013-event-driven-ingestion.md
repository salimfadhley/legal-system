# ADR 0013 — Event-driven ingestion: git-hook → NATS → durable processor

**Status:** Accepted · **Date:** 2026-07-23 · **Supersedes:** [ADR 0011](./0011-auto-ingestion-reconciler.md) (the polling reconciler) · **Builds on:** [ADR 0006](./0006-ingestion-provenance-architecture.md) (git-raw + manifest, direct Docling), [ADR 0008](./0008-observability-architecture.md) (events / DLQ), [ADR 0012](./0012-deployment-topology.md) (portable stack)

## Context

[ADR 0011](./0011-auto-ingestion-reconciler.md) made a **polling reconciler**
(`goldberg watch`) the canonical automatic ingest path: every `--interval` (300s) it
walked the allowlisted trees, **re-hashed every file** in `goldberg-raw` to detect new
content, computed the difference against what ES already held, and ingested it. That
kept the promise — *a file dropped in `goldberg-raw` is ingested with full provenance,
no manual step* — but at a standing cost.

- **The re-hash storm.** The reconciler's steady state is *doing nothing usefully*: on
  a quiescent corpus it still SHA-256s the entire allowlisted tree every cycle, forever,
  to discover that nothing changed. Over the SMB-mounted NAS (the Halob mount gotcha)
  that is a recurring I/O storm whose cost scales with corpus size, not with change
  rate — the opposite of what we want. This is the waste **DIR-004** (do not burn
  resources on no-op work) flags directly.
- **Latency floor.** A dropped file waits up to a full `--interval` before ingest even
  begins; there is no way to make it faster without making the storm worse.
- **A trigger already exists at the source.** `goldberg-raw` is a git working tree
  (ADR 0006). A commit is the *exact, authoritative* signal that content changed, and it
  already names precisely which files changed — no scan needed to find them.
- **NATS is already shared infra.** ADR 0012 keeps NATS as an external, always-on bus.
  Nothing on the ingest path used it yet; a durable JetStream consumer was noted there
  and in ADR 0008 as the natural next increment.

The reconciler was the right *provenance model* (provenance-first, manifest + direct
Docling) wired to the wrong *trigger* (blind polling). This ADR keeps the model and
replaces the trigger.

## Options considered

1. **Keep polling, just widen the interval.** Rejected: trades latency for less storm
   or vice-versa — the re-hash cost is inherent to polling and never goes to zero on a
   quiescent corpus (DIR-004).
2. **Filesystem-event watcher (inotify/FSEvents).** Rejected again, for the ADR 0011
   reason: change-notification over the SMB NAS is unreliable, so events are silently
   missed — unacceptable for a legal corpus.
3. **Git-commit → message-bus trigger, durable consumer.** **Chosen.** The commit is a
   reliable, exact, source-of-truth signal that also enumerates the changed files. A
   durable NATS consumer makes delivery survive processor downtime; a bounded one-shot
   startup catch-up closes any gap that opened while the processor was down, so we keep
   the reconciler's robustness without its steady-state cost.

## Decision

Replace the polling reconciler with an **event-driven ingest service**,
`goldberg ingest-serve`:

1. **Trigger — git hooks publish a commit event (WP04).** A `goldberg-raw` clone points
   `core.hooksPath` at this repo's versioned [`hooks/`](../../hooks/); `post-commit` and
   `post-merge` run `goldberg publish-commit`, publishing one `goldberg.raw.commit`
   message (carrying the commit SHA + source) onto the `GOLDBERG` JetStream stream. The
   hook is fire-and-forget and **never fails `git`** (FR-002): a broker outage costs a
   trigger, not a commit — the startup catch-up recovers it.
2. **Transport — durable JetStream consumer.** A **durable** pull consumer
   (`ingest-processor`) binds the `goldberg.raw.commit` subject. Durability means an
   event published while the service is down is delivered when it returns; redelivery on
   nak gives at-least-once processing.
3. **Process — provenance-first `process_one`, per changed file.** For each triggering
   commit the processor resolves the changed allowlisted files (via `git`), and for each
   runs the **same** provenance-first pipeline the reconciler used: register the manifest
   entry (`sha256` + `raw_commit` + `matters`/`document_type`/`origin`) **before**
   indexing, then extract (direct Docling) → enrich → index, emitting pipeline events.
   All-files-terminal → **ack**; a retryable failure → **nak** (redeliver); after
   `--max-deliver` (default 5) → **term** + a `failed` DLQ event. Idempotent by
   deterministic `doc_id` and the already-indexed resume set — a redelivered or
   replayed commit re-ingests nothing new.
4. **Startup catch-up — one bounded pass, not a loop.** On start (unless `--no-catchup`)
   the service runs exactly **one** bounded catch-up (`ingest/catchup.py`, extracted from
   the reconciler's cycle in WP03) to ingest anything that changed while it was down —
   then it goes idle and waits for events. This is the reconciler's diff logic reused
   *once at boot*, not on a timer: robustness without the storm.
5. **Enrich token-safety.** Enrichment stays optional and bounded (text/passthrough
   files ingest without Docling; OCR-needing files dead-letter and retry on redelivery),
   so a Docling or enricher outage degrades one document, never the service.
6. **Health.** `GET /health` on **8098** reports last activity, handled/dead-lettered
   counts, and the startup catch-up summary (a startup backlog surfaces as
   `degraded`, FR-007).

The `goldberg watch` command and the `goldberg_system.reconcile` daemon package are
**removed** (their catch-up diff now lives in `goldberg_system.ingest.catchup`); the
deployment `reconciler` service is replaced by an `ingest` service running
`goldberg ingest-serve` (see [ADR 0012](./0012-deployment-topology.md)).

## Consequences

- **No steady-state storm (DIR-004):** a quiescent corpus costs *nothing* — the service
  blocks on the consumer. Work is done only when a commit says there is work, and it
  touches only that commit's changed files, not the whole tree.
- **Low latency:** ingest begins on commit, not up to an interval later.
- **Same provenance guarantees (C-002 / NFR-001):** provenance-first `process_one` is
  the reconciler's model unchanged — no document is indexed without a manifest entry
  written first. Idempotent and resumable as before.
- **Durable + gap-safe:** the durable consumer survives processor downtime; the one-shot
  startup catch-up closes any window a lost trigger (or downtime) opened, so the "dropped
  document is an invisible hole" risk that killed the FSEvents option is covered without
  polling.
- **Reuses shared infra (ADR 0012):** rides the existing external NATS; no new stateful
  component, no bundled bus.
- **New dependency on the hook wiring:** ingest now depends on `goldberg-raw` clones
  having `core.hooksPath` set (WP04, [`doc/runbooks/wiring-the-ingest-trigger.md`](../runbooks/wiring-the-ingest-trigger.md)).
  A clone without the hook silently stops triggering — but the startup catch-up and the
  `audit` completeness check (ADR 0008) both surface the resulting backlog, and a
  re-`ingest-serve` (or `goldberg ingest catchup`) recovers it.
- **ADR 0011 superseded:** the reconciler daemon, its `goldberg watch` CLI, its
  `Dockerfile.reconciler`, and the `reconciler` compose service are retired.

## Downstream

Completes the event-driven ingestion mission (FR-011). The durable-DLQ-with-retry
increment noted in ADR 0005/0008/0011 is now realised on the same event interface
(nak/term + `failed` events). `goldberg-raw` + the manifest remain the source of truth
(ADR 0006/0012); ES stays a derived, rebuildable artifact.
