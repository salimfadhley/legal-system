---
work_package_id: WP03
title: Ingest service (processor + catch-up + CLI)
dependencies:
- WP01
- WP02
requirement_refs:
- FR-003
- FR-004
- FR-005
- FR-006
- FR-007
- FR-009
- FR-010
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: Planning artifacts for this mission were generated on feat/goldberg-nats-es-archive. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/goldberg-nats-es-archive unless the human explicitly redirects the landing branch.
subtasks:
- T011
- T012
- T013
- T014
- T015
agent: "claude:sonnet:reviewer-renata:reviewer"
shell_pid: "42704"
history:
- created by /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: src/goldberg_system/ingest/
create_intent:
- src/goldberg_system/ingest/__init__.py
- src/goldberg_system/ingest/commit_files.py
- src/goldberg_system/ingest/processor.py
- src/goldberg_system/ingest/catchup.py
- tests/unit/test_ingest_commit_files.py
- tests/unit/test_ingest_processor.py
- tests/unit/test_ingest_catchup.py
- tests/integration/test_ingest_e2e.py
execution_mode: code_change
owned_files:
- src/goldberg_system/ingest/**
- src/goldberg_system/cli.py
- tests/unit/test_ingest_commit_files.py
- tests/unit/test_ingest_processor.py
- tests/unit/test_ingest_catchup.py
- tests/integration/test_ingest_e2e.py
role: implementer
tags: []
---

## ⚡ Do This First: Load Agent Profile

`/ad-hoc-profile-load python-pedro` (role: implementer). TDD throughout.

## Objective

Build the event-driven ingest service under `src/goldberg_system/ingest/`: consume
commit events (via the WP02 messaging boundary), resolve each commit's changed
allowlisted files, run the **existing** provenance-first `process_one`
(`migrate/reingest.py`) per file with ack/nak/term + DLQ, run a bounded **one-shot
startup catch-up**, and expose the CLI (`ingest-serve`, `publish-commit`,
`ingest catchup`).

## Context — reuse, do not reinvent (C-004)

- `migrate/reingest.py::process_one` already does provenance→extract→enrich→index
  per file and emits pipeline events. **Reuse it** (or `reingest_from_raw(..., only=...)`)
  — do not fork extract/enrich/index logic.
- `reconcile/reconciler.py` holds the raw-vs-indexed diff: `refresh_provenance`,
  `already_indexed()`, `_select_pending`. **Extract** the diff into `ingest/catchup.py`
  as a bounded one-shot; the reconciler file itself is removed later in WP05
  (extract here, delete there).
- `observability/events.py` emits `PipelineEvent`s — the processor MUST emit the
  same events so `status`/`dlq`/`trace` keep working (FR-010).
- Enrichment is now token-safe (WP01), so oversized docs won't DLQ on size.

## Subtasks

### T011 — `ingest/commit_files.py`
- `changed_files(raw_root, sha, allowlist) -> list[str]`: run
  `git -C raw_root diff-tree --no-commit-id --name-status -r <sha>` (fallback
  `git show --name-status` for a root commit), keep Added/Modified, drop deletions,
  filter through `Allowlist` + `_SKIP_EXT`.
- Unit-test with a temp git repo fixture (commit a file, assert it's resolved;
  assert an excluded path is filtered).

### T012 — `ingest/processor.py`
- A durable-consumer loop: `fetch` a batch → for each commit message, resolve files
  (T011) → map each to its manifest SHA → `process_one`. Ack the message when all
  its files reach a terminal state (indexed/skipped/DLQ).
- **Retry semantics**: transient error (e.g. Docling down / DoclingError surfaced) →
  `nak` (redeliver with backoff); after `max_deliver` deliveries → `term` + emit a
  terminal `failed` (DLQ) pipeline event (FR-009).
- **Idempotency** (FR-006): pass `skip_shas = already_indexed()`; deterministic
  `doc_id` means redelivery indexes nothing new.
- Emit `run_id` prefix `ingest-<ts>`.
- Never die on a per-document error (per-file DLQ; loop continues).
- Unit-test with injected fake consumer + fake `process_one` (or a stub sink):
  assert ack on success, nak on transient, term+DLQ event after max_deliver,
  skip on already-indexed.

### T013 — `ingest/catchup.py`
- `run_catchup(raw_root, manifest_path, allowlist, ..., batch) -> CatchupReport`:
  one bounded pass — refresh provenance, compute `already_indexed()`, select the
  bounded pending difference, `process_one` each, emit events with `run_id` prefix
  `catchup-<ts>`. **One pass, no loop** (NFR-002).
- Reuse the reconciler's selection logic (extract, don't duplicate).
- Unit-test: given a manifest with N entries of which K indexed, exactly N−K (capped
  at batch) are processed; a second call after indexing processes nothing.

### T014 — `cli.py` commands
- `goldberg ingest-serve [--durable ingest-processor] [--workers 2] [--max-deliver 5] [--batch 50] [--health-port 8098] [--no-catchup]`:
  on start run one catch-up (unless `--no-catchup`), then open the consumer and
  process until stopped; expose `GET /health` (stdlib) with last-activity +
  catch-up summary.
- `goldberg publish-commit <sha> [--source post-commit]`: publish one commit event
  (uses WP02 publisher). Exit non-zero on failure (hook tolerates it).
- `goldberg ingest catchup [--batch N]`: run one catch-up pass and exit.
- Keep `cli.py` edits additive; the `watch` command is removed later (WP05).

### T015 — Integration test (opt-in)
`tests/integration/test_ingest_e2e.py` guarded by `GOLDBERG_INTEGRATION=1`: against
real NATS/ES/Docling into isolated `*_test` stream+index, publish a commit event for
a small fixture file and assert it reaches `indexed/ok` with provenance; publish the
same again and assert no duplicate.

## Branch Strategy

Planning base + merge target `feat/goldberg-nats-es-archive`; per-lane worktrees
from `lanes.json` at implement time.

## Definition of Done

- [ ] `ingest/{commit_files,processor,catchup}.py` implemented; CLI commands added.
- [ ] `process_one` reused (no forked extract/enrich/index); same pipeline events.
- [ ] ack/nak/term + DLQ after `max_deliver`; idempotent on redelivery.
- [ ] Catch-up is a single bounded pass (no loop).
- [ ] Unit tests green; opt-in integration test present.

## Risks / Reviewer guidance

- **Risk**: ack before all files terminal → message loss on crash. Reviewer:
  confirm ack happens only after every file reaches a terminal state.
- **Risk**: catch-up accidentally loops (reintroducing polling). Reviewer: confirm
  it runs exactly once per `ingest-serve` start.
- **Risk**: `ack_wait` shorter than Docling+enrich → premature redelivery.
  Reviewer: confirm `ack_wait` sizing.

## Activity Log

- 2026-07-23T12:19:18Z – claude:sonnet:python-pedro:implementer – shell_pid=39175 – Assigned agent via action command
- 2026-07-23T12:41:09Z – claude:sonnet:python-pedro:implementer – shell_pid=39175 – Ingest service: processor ack/nak/term+DLQ, one-shot catchup, CLI ingest-serve/publish-commit/ingest catchup; reuses process_one; unit tests green; ruff 0
- 2026-07-23T12:42:16Z – claude:sonnet:reviewer-renata:reviewer – shell_pid=42704 – Started review via action command
- 2026-07-23T12:47:01Z – user – shell_pid=42704 – Review passed: reuses process_one (no forked extract/enrich/index); provenance refreshed before index; ack ONLY after all files terminal-ok (crash mid-commit redelivers, already-indexed skip idempotently via skipped-indexed); nak<max_deliver then term+DLQ failed event; catch-up is one bounded pass (no loop); ack_wait configurable via GOLDBERG_NATS_ACK_WAIT and premature redelivery is idempotency-safe; CLI ingest-serve/publish-commit/ingest catchup live; 33 unit tests pass, integration skips without GOLDBERG_INTEGRATION, ruff clean.
- 2026-07-23T13:08:50Z – user – shell_pid=42704 – Moved to planned
