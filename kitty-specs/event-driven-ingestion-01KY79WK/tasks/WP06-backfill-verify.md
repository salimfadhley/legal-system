---
work_package_id: WP06
title: Backfill + verification
dependencies:
- WP01
- WP03
requirement_refs:
- FR-012
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: feature-branch
subtasks:
- T022
- T023
- T024
agent: claude
history:
- created by /spec-kitty.tasks
agent_profile: reviewer-renata
authoritative_surface: kitty-specs/event-driven-ingestion-01KY79WK/verification/
create_intent: []
execution_mode: planning_artifact
owned_files:
- kitty-specs/event-driven-ingestion-01KY79WK/verification/**
role: reviewer
tags: []
---

## ⚡ Do This First: Load Agent Profile

`/ad-hoc-profile-load reviewer-renata` (role: reviewer). This is an operational
verification WP — run the new path against real infra and record evidence.

## Objective

Prove the pipeline is fully operational after cutover: backfill the two oversized
OCR documents the retired reconciler could never index, and verify completeness +
health with the observability tools. Record the evidence as a verification
artifact under the mission.

## Context

- WP01 made enrichment token-safe; WP03 provides `goldberg ingest catchup`.
- The two files the old reconciler stormed on:
  - `evidence/simon_goldberg/fsa_mortgage_dossier/ocr_output/combined.tsv`
  - `evidence/simon_goldberg/santander_complaint_pack/ocr_output/combined.tsv`
- This WP runs commands against live Halob infra (ES/NATS/Docling); it does not
  change source code — it produces an evidence artifact.

## Subtasks

### T022 — Backfill
- Run `uv run goldberg ingest catchup` (or `ingest-serve` startup) so the two
  oversized OCR files ingest via the token-safe path. Confirm each now reaches
  `indexed/ok` (they enrich on bounded text).

### T023 — Verify completeness + health
- `uv run goldberg audit --manifest config/provenance-manifest.json` → expect 100%
  coverage (every allowlisted committed file indexed or with a durable DLQ record).
- `uv run goldberg status --yaml` → expect `health: ok` (no runaway failed-event
  count).
- `uv run goldberg trace <each .tsv>` → `received/ok … indexed/ok`, no
  `enriched/failed` storm.
- Confirm **no** `reconcile-heartbeat` events accrue while idle (poll gone, NFR-002).

### T024 — Record evidence
- Write `kitty-specs/event-driven-ingestion-01KY79WK/verification/results.md`:
  the commands run, their output (audit %, status health, traces), timestamps, and
  a PASS/FAIL against SC-005 (100% coverage + healthy) and SC-002 (no idle polling).

## Branch Strategy

Planning base + merge target `feat/goldberg-nats-es-archive`; per-lane worktrees
from `lanes.json`.

## Definition of Done

- [ ] Both oversized OCR files trace to `indexed/ok`.
- [ ] `goldberg audit` reports 100% coverage; `status` healthy.
- [ ] No reconcile heartbeat while idle.
- [ ] `verification/results.md` records the evidence with a PASS/FAIL verdict.

## Risks / Reviewer guidance

- **Risk**: running against live infra. Reviewer: use the manual `ingest catchup`
  (bounded) and confirm no unintended re-index storm.
- **Risk**: audit gaps unrelated to this mission. Reviewer: distinguish pre-existing
  coverage holes from regressions and note them separately.
