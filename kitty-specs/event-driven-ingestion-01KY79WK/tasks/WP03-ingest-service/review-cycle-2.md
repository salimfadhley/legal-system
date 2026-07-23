---
cycle_number: 2
wp_id: WP03
mission_slug: event-driven-ingestion-01KY79WK
reviewer_agent: claude:opus:reviewer-renata:reviewer
verdict: approved
reviewed_at: "2026-07-23T13:29:55Z"
affected_files:
- path: src/goldberg_system/ingest/commit_files.py
- path: src/goldberg_system/ingest/processor.py
- path: src/goldberg_system/ingest/catchup.py
- path: src/goldberg_system/messaging/config.py
- path: src/goldberg_system/messaging/client.py
- path: src/goldberg_system/cli.py
reproduction_command: uv run --extra dev pytest tests/unit/test_ingest_*.py tests/unit/test_messaging_*.py -q
---

# WP03 review — cycle 2 (re-review after cycle-1 silent-drop fixes) — APPROVED

Cycle-1 (independent Codex adversarial review) BLOCKED on three findings. All three
are now genuinely closed and no regression was introduced. Verdict: **approved**.

## FIX 1 (HIGH — the silent-document-drop bug) — CLOSED (verified empirically)

The central invariant (DIR-001 / FR-002 / NFR-003: no silently dropped legal
document) is now enforced end to end.

- `commit_files.py::_run_git` RAISES `GitResolutionError` on a non-zero git exit or
  a subprocess/OS error — it no longer returns `""` on failure. Confirmed by reading
  the code and by test (`test_git_failure_raises_git_resolution_error`,
  `test_unknown_sha_raises_git_resolution_error`).
- Merge/root resolution uses a single
  `git diff-tree -m --root --no-commit-id --name-status -r <sha>`. I built a temp
  repo, made a **non-fast-forward merge** that introduced an allowlisted file, and
  confirmed `changed_files` returns that file rather than `[]`:
  `changed_files(merge) = ['evidence/new.pdf', 'evidence/other.txt']`. Root-commit
  resolution via `--root` also verified. (`-m` over-includes base-side files, which
  is idempotency-safe via `skip_shas` and errs toward "never drop" — the correct
  direction.)
- Contract confirmed: `changed_files` returns `[]` ONLY for a genuinely resolved
  zero-allowlisted-files commit (ackable); an unresolvable commit RAISES.
- `processor.py` path traced: `process_commit` lets `GitResolutionError` propagate;
  `_handle` catches it as `exc`; the ack branch requires `exc is None`, so an
  unresolved commit → **nak** (retry), and at `max_deliver` → **term + DLQ event**.
  It can never ack an unresolved/empty-due-to-failure commit. Tests:
  `test_git_resolution_failure_naks_not_acks`,
  `test_git_resolution_failure_terms_and_dlqs_at_max_deliver`, plus the genuinely
  empty commit → ack case.

## FIX 2 (HIGH — retry timing) — CLOSED

- `messaging/config.py` default `ack_wait` is now `300.0s` (research.md R3), still
  env-overridable via `GOLDBERG_NATS_ACK_WAIT`. Tests assert both.
- `messaging/client.py` naks with a non-zero, delivery-count-indexed backoff delay
  AND the durable consumer carries a matching server-side `backoff` schedule for
  ack_wait-timeout redeliveries. The schedule is positive, non-decreasing, and
  truncated to `len < max_deliver` so nats-server accepts it (verified by
  `_consumer_backoff` + tests).

## FIX 3 (MEDIUM — catch-up backlog) — CLOSED

- `catchup.py::count_pending` computes the true unbounded pending total;
  `CatchupReport` exposes `remaining_pending` + a `degraded` property; `/health`
  (cli.py) surfaces `remaining_pending` and sets `status=degraded` when a backlog
  remains. The pass stays a single bounded pass (NFR-002 — no reintroduced loop).
  Test `test_backlog_beyond_batch_is_surfaced_and_degraded` confirms
  `remaining_pending` correct + `degraded True`.

## Regression / anti-pattern checks

- `uv run --extra dev pytest tests/unit/test_ingest_*.py tests/unit/test_messaging_*.py`
  → **43 passed**.
- WP02's existing messaging tests (`test_messaging_client.py`,
  `test_messaging_publisher.py`) → **18 passed** — no regression from the
  coordinated cross-WP edit into WP02-owned `messaging/config.py` + `client.py`
  (noted and expected; shared-file ownership documented in the fix commit).
- Diff-scoped `ruff check` → All checks passed.
- `goldberg ingest-serve --help` works.
- Anti-pattern checklist: no dead code (`count_pending` called by `run_catchup`;
  `GitResolutionError` raised + caught), tests invoke real production paths (real
  git repos, real `_handle`, real `pull_consumer`) — not synthetic fixtures; the new
  `raise` is a fail-loud-then-bounded-retry, not a bare crash.

Approved.
