# Automatic Ingestion Reconciler

## Purpose

Deliver the core promise of the system: **a file placed in `goldberg-raw` is
automatically extracted, enriched, indexed and made queryable — with full
provenance — with no manual step.** Today nothing watches `goldberg-raw`: the
M5–M7 auto-pipeline triggers off Papra webhooks and extracts via Papra, but M8
moved real ingestion to `goldberg-raw` + provenance manifest + direct-Docling
and the trigger was never migrated. Every dropped file is silently ignored until
a human runs a reingest. This mission closes that gap with a provenance-safe
reconciler.

## User Scenarios & Testing

### Primary scenario
The user commits a new document into `goldberg-raw` (e.g. a report under
`reports/…` or an analysis under `analysis/…`). Within one reconcile interval,
the reconciler notices it, registers its provenance in the manifest
(sha256 + `raw_commit` + `matters`/`document_type` from the tree's
`metadata.yaml`), extracts it via direct-Docling, enriches it, indexes it into
Elasticsearch, and emits pipeline events. The user finds it via `goldberg
search` / the MCP tools without running anything.

### Exception / edge scenarios
- **OCR needed but Docling unreachable.** A scanned PDF arrives while Docling is
  down. The reconciler does not crash and does not index an empty doc — it
  retries on later cycles / dead-letters, while text-passthrough files continue
  to flow.
- **Re-drop / change.** The same file (same sha) is already indexed → skipped
  (idempotent). A changed file → re-ingested and deterministically updated, never
  duplicated.
- **Backlog.** Many files appear at once → the reconciler processes a bounded
  batch per cycle so it never saturates the (4-core) Halob CPU.
- **Bad document.** One file fails extraction/enrichment → it dead-letters (DLQ)
  and the reconciler continues; one bad doc never stops the daemon.

### Acceptance
- A file added to `goldberg-raw` is queryable automatically within the target
  latency, with full provenance, and no manual reingest was run.
- `goldberg doctor`'s `live_index_watcher` probe reports **UP** while the
  reconciler runs (it emits fresh pipeline events).
- No document is ever indexed without `raw_commit` + `raw_sha256` provenance.

## Domain Language
- **Reconciler** — a long-running daemon that periodically compares `goldberg-raw`
  against what is already indexed and ingests the difference.
- **Reconcile cycle** — one scan-and-ingest pass over `goldberg-raw`.
- **Provenance registration** — writing a file's manifest entry (sha256,
  `raw_commit`, `matters`, `document_type`, `origin`) before it is indexed.
- **Canonical auto-ingestion path** — the single supported automatic route
  (this reconciler); the Papra-webhook service is retired/deprecated.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-001 | Provide a reconciler daemon (a `goldberg`-CLI runnable service) that periodically scans `goldberg-raw` and ingests any allowlisted file not yet indexed, with no manual step. | Draft |
| FR-002 | Auto-register provenance for each new file in the manifest — sha256, `raw_commit` (from git), and `matters`/`document_type`/`origin` from the nearest tree `metadata.yaml` — **before** indexing. Never index a document without provenance. | Draft |
| FR-003 | Extract → enrich → index via the existing direct-Docling `reingest_from_raw` path (not Papra). | Draft |
| FR-004 | Be idempotent and resumable: a file already indexed (by `raw_sha256`) is skipped; a changed file updates deterministically (no duplicates). | Draft |
| FR-005 | Emit a pipeline event per processed document so observability (`goldberg doctor` watcher probe, `goldberg status`) reflects live activity and freshness. | Draft |
| FR-006 | Detect new/changed files by **content reconciliation** (sha vs manifest/index) using polling, so it is robust over the SMB-mounted corpus (not dependent on filesystem-event delivery). | Draft |
| FR-007 | Bound the work per cycle (configurable batch size and interval) so it does not saturate the Halob CPU. | Draft |
| FR-008 | Degrade gracefully when Docling is unreachable: OCR-needing files are retried/dead-lettered, text passthrough still flows, and one bad document never crashes the daemon. | Draft |
| FR-009 | Establish this reconciler as the single canonical auto-ingestion path and retire/deprecate the Papra-webhook live-index trigger (update or supersede ADR 0005). | Draft |
| FR-010 | Be packaged for **always-on deployment on Halob** — container image + run/compose recipe + env + `GET /health` — with a documented configuration for how the Halob service reaches Docling. | Draft |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|----|-------------|-----------|--------|
| NFR-001 | Provenance integrity — every auto-indexed doc carries full provenance. | 100% of auto-indexed docs have `raw_commit` + `raw_sha256` + `matters`; **zero** un-provenanced docs. | Draft |
| NFR-002 | Latency — a new text/passthrough file becomes queryable promptly. | ≤ one reconcile interval + 2 min (interval configurable; default ≤ 5 min). | Draft |
| NFR-003 | Resource-bounded on the 4-core Halob CPU. | Configurable concurrency/batch; conservative defaults; must not peg all cores continuously. | Draft |
| NFR-004 | Liveness is observable. | Exposes `/health` and emits pipeline events such that `doctor` reports the watcher UP within one interval of starting. | Draft |

### Constraints

| ID | Constraint | Status |
|----|-----------|--------|
| C-001 | Reuse `reingest_from_raw` + the manifest/allowlist logic; do not fork a parallel ingest path. | Draft |
| C-002 | Provenance-safe by construction — register provenance before indexing (this is what the retired Papra service got wrong). | Draft |
| C-003 | Ships with tests (unit + an integration guard against an isolated `*_test` index) and docs (an ADR superseding/updating 0005, and a runbook), per charter DIR-002 and the E2E-testing rule. | Draft |
| C-004 | Deploying onto Halob requires Halob host access, which is operational and outside this repo; the mission delivers the runnable artifact + deploy runbook, and the deploy step is performed against Halob separately. | Draft |

## Success Criteria
- **SC-001**: A file committed to `goldberg-raw` becomes queryable automatically within the target latency, with full provenance, and no manual reingest was run.
- **SC-002**: While the reconciler runs, `goldberg doctor` reports `live_index_watcher` **UP**.
- **SC-003**: Across a run that ingests N new files, exactly N documents are indexed, all with provenance, and re-running ingests 0 (idempotent, no duplicates, no un-provenanced docs).
- **SC-004**: When Docling is down, the reconciler keeps running, text files still flow, and OCR files are visible in the DLQ rather than lost or indexed empty.

## Key Entities
- **ReconcileCycle** — { started_at, scanned, new, indexed, skipped, dead_lettered, elapsed }.
- **ManifestEntry** — provenance record written per new file (sha256, raw_commit, raw_path, matters, document_type, origin).
- **PipelineEvent** — per-document audit/heartbeat used by observability.

## Assumptions
- `goldberg-raw` lives on the NAS; the reconciler polls the filesystem rather than relying on SMB change-notifications.
- Docling reachability from Halob is a deployment decision resolved in design: the Halob service either calls the Mac's Docling (when awake) or a Halob-local Docling; text/passthrough files need no Docling regardless.
- The allowlist trees and per-tree `metadata.yaml` are the source of `matters`/`document_type`/`origin`, consistent with how the manifest is built today.
- Always-on deployment happens on Halob; running the same daemon on the Mac is an acceptable interim, but the target is Halob.
