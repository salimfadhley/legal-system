---
work_package_id: WP02
title: Messaging boundary (NATS JetStream)
dependencies: []
requirement_refs:
- FR-001
- FR-003
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: Planning artifacts for this mission were generated on feat/goldberg-nats-es-archive. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/goldberg-nats-es-archive unless the human explicitly redirects the landing branch.
subtasks:
- T006
- T007
- T008
- T009
- T010
agent: "claude:sonnet:reviewer-renata:reviewer"
shell_pid: "37237"
history:
- created by /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: src/goldberg_system/messaging/
create_intent:
- src/goldberg_system/messaging/__init__.py
- src/goldberg_system/messaging/config.py
- src/goldberg_system/messaging/client.py
- src/goldberg_system/messaging/publisher.py
- tests/unit/test_messaging_publisher.py
- tests/unit/test_messaging_client.py
execution_mode: code_change
owned_files:
- src/goldberg_system/messaging/**
- tests/unit/test_messaging_publisher.py
- tests/unit/test_messaging_client.py
role: implementer
tags: []
---

## ⚡ Do This First: Load Agent Profile

`/ad-hoc-profile-load python-pedro` (role: implementer). Adopt TDD discipline.

## Objective

Introduce a single, injectable NATS JetStream seam under
`src/goldberg_system/messaging/` so the rest of the codebase never imports
`nats-py` directly. It provides: config resolution, connect + idempotent
stream-ensure, publish (with dedup id), and a durable pull-consumer helper.

## Context

- New dependency: `nats-py` (add to `pyproject.toml` here). `nats-py` is async;
  wrap it so callers get a small synchronous-feeling API (use `asyncio.run` or an
  internal loop) OR expose async functions consumed by an async service — pick one
  and keep it consistent; document the choice in the module docstring.
- Naming must align with the downstream `nats-es-archive` mission: stream
  `GOLDBERG`, subjects `goldberg.>`, trigger subject `goldberg.raw.commit`.
- Everything must be unit-testable with an **injected fake** NATS/JetStream object
  — no live broker in unit tests. Integration tests (WP03) exercise the real bus.

## Subtasks

### T006 — `messaging/config.py`
Resolve from config + env (reuse the project config loader / `GOLDBERG_*` pattern):
- `nats_url` (`NATS_URL`, default `nats://192.168.86.31:4222`)
- `stream` (`GOLDBERG`), `subject_prefix` (`goldberg`), `commit_subject`
  (`goldberg.raw.commit`), `durable` (`ingest-processor`)
Return a frozen dataclass `MessagingConfig`. Unit-test env overrides.

### T007 — `messaging/client.py` connect + ensure-stream
- `connect(config) -> JetStreamContext` (or a thin wrapper).
- `ensure_stream(js, config)`: create the `GOLDBERG` stream over `goldberg.>` if
  absent (retention `limits`, dedup window keyed on `Nats-Msg-Id`); idempotent —
  safe to call every startup. Accept an injected `js` in tests.

### T008 — `messaging/publisher.py`
- `publish_commit(js, config, sha, ts, source) -> ack`: publish JSON
  `{sha, ts, source}` to `commit_subject` with header `Nats-Msg-Id = sha`.
- Return the publish ack; raise on failure (callers decide fatality).
- Unit-test: fake `js.publish` records subject/headers/body; assert `Nats-Msg-Id`.

### T009 — `messaging/client.py` durable pull-consumer helper
- `pull_consumer(js, config, ...)`: ensure a durable pull consumer
  (`durable=ingest-processor`, explicit ack, `max_deliver`, `ack_wait`,
  `filter_subject=commit_subject`).
- Provide `fetch(batch, timeout)` returning messages, plus thin `ack/nak/term`
  wrappers so the processor (WP03) stays broker-agnostic.
- Unit-test ack/nak/term dispatch against a fake message.

### T010 — Unit tests
`tests/unit/test_messaging_publisher.py` and `test_messaging_client.py` covering
config overrides, ensure-stream idempotency (called twice = one create), publish
headers, and consumer ack/nak/term. All with injected fakes.

## Branch Strategy

Planning base and merge target both `feat/goldberg-nats-es-archive`. Worktrees are
per-lane from `lanes.json` at implement time.

## Definition of Done

- [ ] `messaging/{config,client,publisher}.py` implemented, `nats-py` added to deps.
- [ ] No other module imports `nats-py` (the boundary holds).
- [ ] ensure-stream is idempotent; publish sets `Nats-Msg-Id=sha`.
- [ ] Consumer helper exposes fetch + ack/nak/term.
- [ ] Unit tests green with injected fakes; no live broker needed.

## Risks / Reviewer guidance

- **Risk**: async/sync boundary leaks complexity. Reviewer: confirm the chosen
  model is consistent and the public API is clean.
- **Risk**: stream config drift vs the archive mission. Reviewer: confirm
  `GOLDBERG` / `goldberg.>` naming.

## Activity Log

- 2026-07-23T11:52:28Z – claude:sonnet:python-pedro:implementer – shell_pid=33887 – Assigned agent via action command
- 2026-07-23T12:10:50Z – claude:sonnet:python-pedro:implementer – shell_pid=33887 – Messaging boundary: config/client/publisher; ensure_stream idempotent; Nats-Msg-Id dedup; fake-NATS unit tests green (14 passed); ruff 0; async public API (documented)
- 2026-07-23T12:11:38Z – claude:sonnet:reviewer-renata:reviewer – shell_pid=37237 – Started review via action command
- 2026-07-23T12:14:14Z – user – shell_pid=37237 – Review passed: NATS JetStream boundary complete — messaging/{config,client,publisher} + injected-fake unit tests (14 passed), ruff clean, boundary holds (no nats imports outside messaging/), ensure_stream idempotent creating GOLDBERG over goldberg.> with dedup window, publish_commit sets Nats-Msg-Id=sha to goldberg.raw.commit with JSON body, durable pull-consumer exposes fetch+ack/nak/term, async-only API coherent (no per-call teardown), naming aligned with nats-es-archive.
