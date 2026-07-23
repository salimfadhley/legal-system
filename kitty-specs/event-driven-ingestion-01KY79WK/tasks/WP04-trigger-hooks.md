---
work_package_id: WP04
title: Trigger (git hooks)
dependencies:
- WP03
requirement_refs:
- FR-001
- FR-002
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: Planning artifacts for this mission were generated on feat/goldberg-nats-es-archive. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/goldberg-nats-es-archive unless the human explicitly redirects the landing branch.
subtasks:
- T016
- T017
- T018
agent: "claude:sonnet:reviewer-renata:reviewer"
shell_pid: "49870"
history:
- created by /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: hooks/
create_intent:
- hooks/post-commit
- hooks/post-merge
- doc/runbooks/wiring-the-ingest-trigger.md
execution_mode: code_change
owned_files:
- hooks/**
- doc/runbooks/wiring-the-ingest-trigger.md
role: implementer
tags: []
---

## ⚡ Do This First: Load Agent Profile

`/ad-hoc-profile-load python-pedro` (role: implementer).

## Objective

Ship the git-hook trigger so every `goldberg-raw` commit publishes a
`goldberg.raw.commit` event — without ever blocking or failing the developer's
`git commit` (FR-002). The hook calls the `goldberg publish-commit` command
introduced in WP03.

## Context

- `goldberg-raw`'s working tree lives on Halob's filesystem (edited on-box or over
  the SMB mount). A hook fires wherever `git` runs; NATS is reachable from both.
- Hooks are shipped **in this repo** under `hooks/` and wired to `goldberg-raw` via
  `git config core.hooksPath` (versioned, not hand-copied into `.git/hooks`).
- `post-commit` covers authored commits; `post-merge` covers non-fast-forward
  pull-merges. Fast-forward pulls fire neither — that gap is covered by the WP03
  startup catch-up (documented, accepted).

## Subtasks

### T016 — Hook scripts
- `hooks/post-commit` and `hooks/post-merge` (executable, `#!/usr/bin/env bash`):
  ```bash
  sha="$(git rev-parse HEAD)"
  goldberg publish-commit "$sha" --source post-commit >/dev/null 2>&1 || \
    logger -t goldberg-hook "publish-commit failed for $sha (will be caught up at startup)"
  exit 0
  ```
  (`post-merge` uses `--source post-merge`.) **Always `exit 0`** — a publish failure
  must never fail `git` (FR-002); the loss is recovered by startup catch-up.
- Keep them tiny and dependency-free (only assume `goldberg` on PATH).

### T017 — Install steps + runbook
- `doc/runbooks/wiring-the-ingest-trigger.md`: how to point a `goldberg-raw` clone
  at these hooks (`git -C <raw> config core.hooksPath <path-to>/hooks`), how to
  verify (`git commit --allow-empty` → a message on the stream), and the FF-pull
  caveat + the `goldberg ingest catchup` escape hatch.

### T018 — Manual delivery validation
- Add a short "validate delivery" section (commands only) the operator runs once on
  Halob to confirm a real commit produces a stream message and gets indexed. No
  automated test here (it requires the live clone); note this explicitly.

## Branch Strategy

Planning base + merge target `feat/goldberg-nats-es-archive`; per-lane worktrees
from `lanes.json`.

## Definition of Done

- [ ] `hooks/post-commit` + `hooks/post-merge` present, executable, always exit 0.
- [ ] Runbook documents `core.hooksPath` wiring, verification, and the FF-pull gap.
- [ ] Hook is non-fatal to `git` on publish failure (FR-002).

## Risks / Reviewer guidance

- **Risk**: a hook that can fail `git` would block commits to the legal corpus.
  Reviewer: confirm `exit 0` on every path and stderr is swallowed.
- **Risk**: `goldberg` not on PATH in the committing environment. Reviewer: confirm
  the runbook calls this out (absolute path or venv activation option).

## Activity Log

- 2026-07-23T12:48:37Z – claude:sonnet:python-pedro:implementer – shell_pid=44488 – Assigned agent via action command
- 2026-07-23T12:57:43Z – claude:sonnet:python-pedro:implementer – shell_pid=44488 – post-commit/post-merge hooks (always exit 0, FR-002) + wiring runbook (core.hooksPath, verify, FF-pull gap, 'goldberg ingest catchup' escape hatch, PATH/venv note); hook-shape test green (17 passed)
- 2026-07-23T13:09:00Z – claude:sonnet:reviewer-renata:reviewer – shell_pid=49870 – Started review via action command
