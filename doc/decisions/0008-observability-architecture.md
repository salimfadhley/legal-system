# ADR 0008 — Observability: event backbone, dead-letter queue, reconciliation (M12)

**Status:** Proposed (spec for mission M12) · **Date:** 2026-07-21

## Context

The pipeline is autonomous and can fail **silently**. For a legal evidence corpus
that is a correctness defect, not just an ops gap: a document that never ingests is
invisible — absent from search and the wiki — so every answer is quietly skewed, and
we can't tell. M12 makes the pipeline **auditable, gap-detecting, and
self-verifying**, and must answer three questions: *is it working?*, *did anything
not ingest?*, *why did X not ingest?*

This ADR fixes the observability architecture so M12 (and the M8 migration it will
verify) can be built against a stable design.

## Decisions

### 1. Canonical event model — `PipelineEvent`

Every stage boundary, for every document, emits one structured event:

```
PipelineEvent:
  ts:          ISO-8601 UTC
  run_id:      groups a batch/backfill run (or the live service session)
  component:   live-service | backfill | wiki-sink | migrate
  stage:       received | extracted | enriched | indexed | wiki_authored
  status:      started | ok | skipped | failed
  doc_id:      deterministic gb_<sha256…> (may be absent pre-extraction)
  sha256:      content hash — the reconciliation join key
  raw_path:    where known (from the manifest / Papra)
  attempt:     retry counter
  reason:      human string for skipped/failed (e.g. "docling returned empty",
               "enrichment 429", "mime message/rfc822 skipped")
  error:       structured error detail for failed (type, message, truncated trace)
```

The existing `BackfillReport` counters (`processed/indexed/skipped_empty/failures/
with_provenance`) become **derived** from these events rather than a parallel path.

### 2. Transport — NATS JetStream is the durable event backbone

JetStream is already in the stack. Three streams (long retention — legal audit):

- **`goldberg.events.<stage>.<status>`** — the full audit trail (all events above).
- **`goldberg.dlq.<stage>`** — the **dead-letter queue**: a document that fails a
  stage terminally (exceeds `MaxDeliver`, or a handler `term()`s it) is republished
  here with its payload + stage + reason. Routing is driven by JetStream's
  `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES` advisories. DLQ entries are durable,
  inspectable, and **reprocessable** once the cause is fixed.
- **`goldberg.errors.<component>`** — process-level crashes/exceptions (unhandled
  errors, startup/connection failures) — *why a process died*, distinct from *why a
  document failed*.

Durability + replay + DLQ are exactly JetStream's strengths; this is why the event
bus is NATS, not direct-to-ES.

### 3. Query surface — an ES projection `goldberg_pipeline_events`

A durable consumer subscribes to `goldberg.events.>` / `.dlq.>` / `.errors.>` and
indexes each event into ES (`doc_id`/`stage`/`status`/`component`/`run_id` keyword,
`reason`/`error` text, `ts` date). NATS gives durability + DLQ + replay; **ES gives
the query surface** — filter by `doc_id` to answer "why did X not ingest", by
`status:failed` for "what failed today", aggregations for status counts. (This
resolves the earlier open question: use **both**, each for its strength.)

### 4. Reconciliation — expected vs actual, by SHA-256

The completeness check is a set join on the content hash (validated in the ADR 0006
spike):

- **Expected** = the goldberg-raw provenance manifest (`sha256 → raw_path`) — the
  authoritative "should exist" set.
- **Extracted** = Papra (`original_sha256_hash`) — distinguishes "never reached
  Papra" from "extracted but not indexed".
- **Actual** = ES `goldberg_documents` (by `doc_id`/`content_hash`).

Report **missing** (expected − actual), **extra** (actual − expected), **stale**
(`content_hash` changed since indexing). For each *missing* doc, join the event
projection / DLQ to attach the last-known stage + reason. This is the direct answer
to "is there something that did not ingest?".

### 4a. Correlation ID — the raw SHA-256 (preserved in metadata)

**Decision: the raw file's SHA-256 is the pipeline's preservable correlation ID**, and
it is **stamped into the document metadata** (`raw_sha256`) so any ingested artifact
traces back to its original source bytes (user, 2026-07-21). It is preferred over a
minted UUID because it is *content-addressed*: deterministic, identical at every
stage and in every representation, and stable across re-runs / re-extraction /
re-indexing (unlike `doc_id = sha256(raw_path + content)`, which shifts if extraction
output changes). The same value already appears as Papra's `original_sha256_hash`, the
manifest key (ADR 0006), and the `sha256` on every `PipelineEvent`.

`raw_sha256` is carried on `DocumentMetadata` → the ES document, the extracted
frontmatter (serialised from the schema), and — for M11 — wiki pages. So one ID
correlates **goldberg-raw → Papra → events → ES doc → extracted → wiki**, and
`goldberg trace <sha256>` walks the whole journey. It also lets reconciliation join by
hash (path-independent) rather than only by `raw_path`.

*(A per-execution trace ID — an OTel-style UUID minted at ingestion and propagated in
NATS message headers — is the complementary "one specific run" identifier; it arrives
with the NATS/OTel increment (§2, §7) and does not replace the content correlation ID.)*

### 5. Instrumentation — a thin `emit` + a stage wrapper

A small `observability` module: `emit(event)` (publishes to JetStream) and an
`observed_stage(...)` context manager wrapping each pipeline stage so it emits
`started` then `ok`/`skipped`/`failed`, and on terminal failure also publishes to the
DLQ. Wire it into `pipeline.py` (backfill), the live `service`, and the M11 wiki
sink. Emission must **never block or break** the pipeline (best-effort; a telemetry
failure is logged, not fatal).

### 6. CLI surface

- `goldberg audit` — reconciliation summary (expected/actual/missing/extra/stale).
- `goldberg audit --missing` — the list of un-ingested `raw_path`s + last reason.
- `goldberg trace <raw_path|sha256|doc_id>` — one document's stage timeline + stop
  point (reads the event projection + DLQ).
- `goldberg status` — health + per-stage counts + DLQ depth + freshness (also the
  data source for M13; see ADR 0009).
- `goldberg dlq list` / `goldberg dlq retry <doc_id|all>` — inspect + reprocess
  dead-lettered documents (idempotent via deterministic doc-id).

### 6b. Component health (`goldberg doctor`)

Audit/trace/status answer *"did this document ingest?"* — a **data-plane** question.
They do **not** answer *"is the pipeline itself up right now?"* — the **control-plane**
question an operator asks when ingestion has silently stopped. A paused live-index
watcher or an unreachable Docling used to be found only by hand-probing with `curl`.
`goldberg doctor` closes that gap: a per-component liveness board over the six major
components, assembled by `observability/health.py` and reused by `goldberg status`
and the MCP layer (one implementation, no drift).

**Status semantics** (a three-value enum, so DOWN is structurally distinct from a
data-plane DEGRADED):

- **UP** — reachable and functioning as intended.
- **DEGRADED** — reachable but not fully correct (missing index, stale synthesis).
- **DOWN** — unreachable, erroring, or timed out.

The overall verdict is the **worst** component status; the command's **exit code**
follows it (UP → 0, DEGRADED/DOWN → non-zero) so it is usable in CI/cron.

**The probes** (each read-only, individually time-bounded to ≤5s, and never raising —
any failure becomes DOWN with a reason; all six run concurrently on a thread pool so
the board returns ≤10s even with every component down):

| Component | Probe | UP / DEGRADED / DOWN |
|-----------|-------|----------------------|
| **elasticsearch** | `_cluster/health` + a `_count` on each required index | all indices present → UP; a required index missing → DEGRADED; cluster unreachable → DOWN |
| **docling** | `DoclingClient.health()` (`GET /health` == `{"status":"ok"}`) | ok → UP; else/unreachable → DOWN |
| **enricher** (OpenAI) | `GET /v1/models` with the API key — a **metadata/list** call, never a completion (no tokens billed) | HTTP 200 → UP; **no key → DEGRADED**; other/unreachable → DOWN |
| **mcp_server** | a real streamable-http `initialize` handshake (`POST /mcp`) | HTTP 200 (+ `Mcp-Session-Id`) → UP; refused/timeout/non-200 → DOWN |
| **live_index_watcher** | **inferred** from pipeline-event freshness (newest `ts` within a window, default 15 min) | fresh → UP; stale/none → DOWN — **always `inferred=true`** |
| **wiki_synthesis** | `silverbullet-goldberg` presence vs ingest | missing → DOWN; present but not refreshed from ingest → DEGRADED; present + wired → UP |

**Inferred caveats (honest, not optimistic).** Two legs cannot be probed as a true
liveness check and are reported as such:

- The **live-index watcher** runs on a remote host (Halob) unreachable from wherever
  the command runs (C-003), so its liveness is *inferred* from event freshness — a
  green here means "events are flowing", not "the process was pinged". Every result
  carries `inferred=true`.
- **Wiki synthesis** has **no synthesis pipeline wired yet**: the index exists but is
  not refreshed from ingest. The probe therefore reports **DEGRADED** by default
  (`synthesis_wired=False`) — it deliberately does not paint this leg green until the
  refresh mechanism exists.

Related fix (same increment): `no_recent_failures` in `status` was permanently red
because it counted *all* historical failed events; it now counts only failures within
a configurable recent window (default 24h) via a range query on the event `ts`.

### 7. OpenTelemetry — an optional later layer, not core

Researched (2026-07-21): there is **no turnkey OTel gateway for NATS** — the OTel
Collector is the gateway, but an official NATS receiver/exporter is community WIP
(contrib #39540). When wanted: **prometheus-nats-exporter** + NATS monitoring
endpoints for server/JetStream metrics (throughput, slow consumers, consumer lag);
and app-level OTel tracing with **trace context propagated in NATS message headers**
(NATS 2.2+) → an OTel Collector → any backend. Layer it *over* the DLQ/errors/audit
backbone later; it is not a prerequisite.

## Consequences

- The DLQ makes the **M8 full migration self-verifying**: failures are captured +
  reprocessable, and a closing `goldberg audit` proves "N expected, N indexed, 0
  missing". This is the argument for building M12 (core) before M8.
- New: an `observability` module (event model + emit + stage wrapper), a JetStream
  stream set, an ES projection consumer (a small long-running service on Halob), and
  the CLI verbs. Reuses the SHA-256 join and the manifest.
- Shared infra (NATS streams) → documented in Mind of Steele per [[shared-infra-docs]].

## Phases (M12)

1. **Event backbone** — `PipelineEvent` model + `emit`/stage-wrapper; JetStream
   streams (events/dlq/errors); wire pipeline + live service + wiki sink; ES
   projection consumer.
2. **Reconciliation** — `goldberg audit` (expected vs actual → gaps + reasons).
3. **Trace + DLQ ops** — `goldberg trace`, `goldberg status`, `goldberg dlq
   list/retry`.
4. **Stretch** — OTel (prometheus-nats-exporter + header tracing) + alerting.

**Core = phases 1–3** (the part that de-risks M8). Phase 4 is later polish.
