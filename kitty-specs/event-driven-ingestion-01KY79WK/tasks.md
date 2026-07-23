# Tasks: Event-Driven Trigger-Based Ingestion

**Mission**: event-driven-ingestion-01KY79WK
**Planning base**: feat/goldberg-nats-es-archive · **Merge target**: feat/goldberg-nats-es-archive

6 work packages. WP01 (enrich fix) and WP02 (messaging) are independent and can
start in parallel; WP03 depends on both; WP04→WP03; WP05→WP03,WP04; WP06→WP01,WP03.
**MVP**: WP01 alone already closes the observed failure class (oversized-doc storm).

## Subtask Index

| ID | Description | WP | Parallel |
|----|-------------|----|----------|
| T001 | Failing test: oversized body enrich stays under token budget / recovers from 400 | WP01 | |
| T002 | tiktoken token-budget truncation in `_build_messages` | WP01 | |
| T003 | Defensive `context_length_exceeded` retry (shrink body) in `enrich()` | WP01 | |
| T004 | Replace `_MAX_BODY_CHARS` char cap; add `tiktoken` dep | WP01 | |
| T005 | Run suite; verify green + storm-file case indexes | WP01 | |
| T006 | `messaging/config.py` — resolve NATS_URL/stream/subject/durable | WP02 | [P] |
| T007 | `messaging/client.py` — JetStream connect + ensure stream (idempotent) | WP02 | |
| T008 | `messaging/publisher.py` — publish commit event with `Nats-Msg-Id=sha` | WP02 | |
| T009 | `messaging/client.py` — durable pull-consumer helper (fetch/ack/nak/term) | WP02 | |
| T010 | Unit tests with injected fake NATS (publish + consume) | WP02 | |
| T011 | `ingest/commit_files.py` — resolve changed allowlisted files for a SHA + test | WP03 | |
| T012 | `ingest/processor.py` — consume → per-file `process_one`; ack/nak/term + DLQ + test | WP03 | |
| T013 | `ingest/catchup.py` — extract reconcile diff into bounded one-shot pass + test | WP03 | |
| T014 | `cli.py` — `ingest-serve` (catchup→consume) + `/health`; `publish-commit`; `ingest catchup` | WP03 | |
| T015 | Integration test: commit→publish→consume→index (opt-in) | WP03 | |
| T016 | `hooks/post-commit` + `hooks/post-merge` → `goldberg publish-commit`; always exit 0 | WP04 | |
| T017 | `core.hooksPath` install steps + runbook | WP04 | |
| T018 | Manual delivery validation note | WP04 | |
| T019 | Remove `reconcile/` daemon + `goldberg watch` CLI command | WP05 | |
| T020 | `deploy/docker-compose.yml`: replace `reconciler` with `ingest`; `Dockerfile.ingest` | WP05 | |
| T021 | ADR 0013 (supersedes 0011); update ADR 0011/0012 status + ingestion runbook | WP05 | |
| T022 | Run `goldberg ingest catchup` to backfill the two oversized OCR files | WP06 | |
| T023 | Verify: `audit` 100%, `status` healthy, `trace` indexed/ok, no reconcile heartbeat | WP06 | |
| T024 | Record verification-results artifact | WP06 | |

---

## WP01 — Enrichment token-safety (the fold-in fix)

**Goal**: Oversized documents index on token-bounded text instead of failing
`context_length_exceeded` and storming the DLQ. **Priority**: P0 (MVP — closes the
observed failure). **Independent test**: feed a >128k-token body to `enrich()`; the
outgoing request stays within budget and returns a normal result; a simulated
context-length 400 is recovered by a shrink-and-retry.
**Depends on**: none.

- [ ] T001 Failing test: oversized body enrich stays under token budget / recovers from 400 (WP01)
- [ ] T002 tiktoken token-budget truncation in `_build_messages` (WP01)
- [ ] T003 Defensive `context_length_exceeded` retry (shrink body) in `enrich()` (WP01)
- [ ] T004 Replace `_MAX_BODY_CHARS` char cap; add `tiktoken` dep (WP01)
- [ ] T005 Run suite; verify green + storm-file case indexes (WP01)

Prompt: `tasks/WP01-enrich-token-safety.md` (~230 lines)

## WP02 — Messaging boundary (NATS JetStream)

**Goal**: A single injectable seam for connect / ensure-stream / publish / durable
consume so nothing else imports `nats-py`. **Priority**: P0 (foundation).
**Independent test**: with an injected fake NATS client, `publish_commit` sets
`Nats-Msg-Id` and the consumer helper acks/naks/terms correctly.
**Depends on**: none. **Parallel with**: WP01.

- [ ] T006 `messaging/config.py` — resolve NATS_URL/stream/subject/durable (WP02)
- [ ] T007 `messaging/client.py` — JetStream connect + ensure stream idempotent (WP02)
- [ ] T008 `messaging/publisher.py` — publish commit event with `Nats-Msg-Id=sha` (WP02)
- [ ] T009 `messaging/client.py` — durable pull-consumer helper (WP02)
- [ ] T010 Unit tests with injected fake NATS (WP02)

Prompt: `tasks/WP02-messaging-boundary.md` (~260 lines)

## WP03 — Ingest service (processor + catch-up + CLI)

**Goal**: The event-driven service: consume commit events → provenance-first
`process_one` per changed file (ack/nak/term + DLQ), a bounded one-shot startup
catch-up, and the CLI surface. **Priority**: P0 (core).
**Independent test**: publish a commit event referencing a fixture file → it becomes
`indexed/ok` with provenance; redelivery indexes nothing new; startup catch-up
ingests only the raw-vs-indexed difference.
**Depends on**: WP01, WP02.

- [ ] T011 `ingest/commit_files.py` — resolve changed allowlisted files for a SHA + test (WP03)
- [ ] T012 `ingest/processor.py` — consume → per-file `process_one`; ack/nak/term + DLQ + test (WP03)
- [ ] T013 `ingest/catchup.py` — extract reconcile diff into bounded one-shot pass + test (WP03)
- [ ] T014 `cli.py` — `ingest-serve` + `/health`; `publish-commit`; `ingest catchup` (WP03)
- [ ] T015 Integration test: commit→publish→consume→index (opt-in) (WP03)

Prompt: `tasks/WP03-ingest-service.md` (~380 lines)

## WP04 — Trigger (git hooks)

**Goal**: Every `goldberg-raw` commit publishes a commit event without blocking
`git`. **Priority**: P1. **Independent test**: committing on the wired clone yields a
`goldberg.raw.commit` message; a publish failure does not fail the commit.
**Depends on**: WP03 (needs the `publish-commit` command).

- [ ] T016 `hooks/post-commit` + `hooks/post-merge` → `goldberg publish-commit`; always exit 0 (WP04)
- [ ] T017 `core.hooksPath` install steps + runbook (WP04)
- [ ] T018 Manual delivery validation note (WP04)

Prompt: `tasks/WP04-trigger-hooks.md` (~170 lines)

## WP05 — Decommission reconciler + deployment + docs

**Goal**: Remove the retired polling path and record the decision. **Priority**: P1.
**Independent test**: no `goldberg watch` command, no `reconcile/` daemon, compose
runs `ingest` not `reconciler`; ADR 0013 supersedes 0011.
**Depends on**: WP03, WP04 (new path must work before old is removed).

- [ ] T019 Remove `reconcile/` daemon + `goldberg watch` CLI command (WP05)
- [ ] T020 `deploy/docker-compose.yml`: replace `reconciler` with `ingest`; `Dockerfile.ingest` (WP05)
- [ ] T021 ADR 0013 (supersedes 0011); update ADR 0011/0012 status + ingestion runbook (WP05)

Prompt: `tasks/WP05-decommission-and-deploy.md` (~230 lines)

## WP06 — Backfill + verification

**Goal**: Re-ingest the documents the retired reconciler left unindexed and prove
completeness. **Priority**: P1. **Independent test**: `audit` reports 100% coverage,
`status` is healthy, the two OCR `.tsv` files trace to `indexed/ok`.
**Depends on**: WP01, WP03.

- [ ] T022 Run `goldberg ingest catchup` to backfill the two oversized OCR files (WP06)
- [ ] T023 Verify: `audit` 100%, `status` healthy, `trace` indexed/ok, no reconcile heartbeat (WP06)
- [ ] T024 Record verification-results artifact (WP06)

Prompt: `tasks/WP06-backfill-verify.md` (~150 lines)
