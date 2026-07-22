# ADR 0011 — Auto-ingestion reconciler: the canonical automatic ingest path (M15)

**Status:** Accepted (built + tested) · **Date:** 2026-07-22 · **Supersedes:** [ADR 0005](./0005-live-service-webhook-driven.md) (trigger) · **Builds on:** [ADR 0006](./0006-ingestion-provenance-architecture.md), [ADR 0008](./0008-observability-architecture.md)

## Context

The system's core promise is: *a file placed in `goldberg-raw` is automatically
extracted, enriched, indexed and made queryable — with full provenance — with no
manual step.* That promise was not being kept.

- **M5–M7** (ADR 0005) built a live pipeline triggered by **Papra `document:created`
  webhooks**, extracting via Papra. It indexed *before* registering `goldberg-raw`
  provenance, so documents landed with no `raw_commit` / `matters` (Papra's filename
  as `raw_path`). ADR 0006 identified this as the wrong system-of-record.
- **M8** (ADR 0006) made `goldberg-raw` (git) the system-of-record and provenance
  source: a **manifest** (`sha256 → {raw_path, raw_commit, matters, …}`) built by
  walking `goldberg-raw`, and a **direct-Docling** bulk ingest (`reingest_from_raw`)
  that bypasses Papra's broken extraction.
- **But the trigger was never migrated.** After M8, nothing watches `goldberg-raw`.
  A dropped file is silently ignored until a human runs `goldberg migrate reingest`.

So the auto-pipeline (Papra) and the real ingest path (goldberg-raw + manifest +
direct Docling) had diverged, and the automatic route was both un-wired and
provenance-unsafe.

## Options considered

1. **Fix the Papra webhook to attach provenance.** Rejected: it keeps Papra's
   extraction (the thing M8 abandoned as broken), depends on webhook delivery, and
   still races provenance against indexing. It doubles down on the retired path.
2. **Filesystem-event watcher (inotify/FSEvents) on `goldberg-raw`.** Rejected as the
   trigger: `goldberg-raw` lives on an SMB-mounted NAS where change-notification
   delivery is unreliable (see the Halob mount gotcha). Events would be silently
   missed — unacceptable for a legal corpus where a dropped document is an invisible
   hole.
3. **Polling reconciler (content reconciliation).** **Chosen.** Periodically compare
   `goldberg-raw` against what is already indexed (by content SHA-256) and ingest the
   difference, reusing the M8 manifest + `reingest_from_raw` path. Robust over SMB
   (no dependence on event delivery), idempotent, and provenance-first by
   construction.

## Decision

Add a **reconciler daemon** — `goldberg watch` /
`goldberg_system.reconcile.Reconciler` — as the single canonical automatic ingest
path. One **reconcile cycle**:

1. **Refresh provenance (before indexing).** Walk the allowlisted trees; for every
   file whose content SHA-256 is not yet in the manifest, register a provenance entry
   (`sha256` + git `raw_commit` + `matters`/`document_type`/`origin` from the tree
   `metadata.yaml`) and persist `config/provenance-manifest.json` atomically. This
   reuses the exact per-file derivation behind `goldberg migrate manifest`
   (`manifest.build_entry`) — no forked provenance logic — and bounds git-commit
   lookups to genuinely new files.
2. **Compute the resume set.** Query Elasticsearch for the `raw_sha256` values already
   indexed.
3. **Ingest a bounded batch of the difference** via `reingest_from_raw` (direct
   Docling → enrich → index), emitting pipeline events.

`run_forever(interval)` loops the cycle, emits a **heartbeat** pipeline event each
cycle (so the observability watcher probe infers liveness from recent
`goldberg_pipeline_events`), sleeps `interval`, and **never dies on a per-file
error**: a bad document dead-letters to the DLQ (a `failed` event) and the daemon
continues. A stdlib `GET /health` endpoint exposes the last cycle for Halob /
monitoring.

**Docling reachability is handled by graceful degradation, not a gate.** Text /
passthrough files (`.md`/`.txt`/`.json`/`.csv`/`.tsv`) are read without Docling, so
they always flow. OCR-needing files, when Docling is unreachable, raise a
`DoclingError` that dead-letters that one document (retried next cycle) — never
indexed empty, never crashing the daemon.

**Forward-progress / bounded-batch design.** The batch is selected from *pending*
work: entries not already indexed, not media, and not proven-non-indexable this run
(empty extractions and media are recorded so they don't re-fill the bounded batch
every cycle and starve genuinely new files). Extraction/enrichment *failures* are
deliberately **not** marked non-indexable, so a Docling-down OCR file is retried.

## Consequences

- **Provenance-safe by construction (C-002 / NFR-001):** no document is indexed
  without a manifest entry (`raw_commit` + `raw_sha256` + `matters`) written first.
- **Idempotent & resumable (FR-004):** already-indexed shas are skipped; re-running
  ingests nothing new. Deterministic `doc_id` means re-ingest updates, never
  duplicates.
- **Resource-bounded (NFR-003):** configurable `--interval` / `--workers` / `--batch`
  with conservative defaults (300s / 2 / 50) for the 4-core Halob host.
- **Observable (NFR-004):** per-document events + a per-cycle heartbeat keep the
  watcher probe UP; `/health` gives an out-of-band liveness check.
- **Papra retired as a trigger (FR-009):** ADR 0005 is superseded; the
  `goldberg_system.service` code is kept for reference but not deployed. Papra remains
  usable as a human drop-target / viewer, but is no longer on the ingest path.
- **Polling cost:** each cycle hashes every allowlisted file to detect new content.
  This is I/O-bound and acceptable; the CPU-bound work (extraction/enrichment) is what
  the batch bounds. The resume query reads up to 10k `raw_sha256` (consistent with
  `migrate reingest --resume`); a corpus beyond that needs a scroll/PIT follow-up.
- **Deployment is operational (C-004):** this mission ships the runnable artifact
  (`Dockerfile.reconciler`) + the deploy runbook; putting it on Halob is a separate
  operational step, gated on how Halob reaches Docling.

## Downstream

Unblocks M15 (automatic ingestion). Reuses ADR 0006 (manifest + direct Docling) and
ADR 0008 (pipeline events / DLQ / watcher). A durable NATS-JetStream DLQ with
automatic retry (deferred in ADR 0005/0008) remains the natural next increment behind
the same event interface.
