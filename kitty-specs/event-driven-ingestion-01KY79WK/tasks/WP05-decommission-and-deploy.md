---
work_package_id: WP05
title: Decommission reconciler + deployment + docs
dependencies:
- WP03
- WP04
requirement_refs:
- FR-011
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: Planning artifacts for this mission were generated on feat/goldberg-nats-es-archive. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/goldberg-nats-es-archive unless the human explicitly redirects the landing branch.
subtasks:
- T019
- T020
- T021
agent: claude
history:
- created by /spec-kitty.tasks
agent_profile: curator-carla
authoritative_surface: deploy/
create_intent:
- deploy/Dockerfile.ingest
- doc/decisions/0013-event-driven-ingestion.md
execution_mode: code_change
owned_files:
- src/goldberg_system/reconcile/**
- deploy/**
- doc/decisions/0013-event-driven-ingestion.md
- doc/decisions/0011-auto-ingestion-reconciler.md
- doc/decisions/0012-deployment-topology.md
- doc/runbooks/auto-ingestion-reconciler.md
role: implementer
tags: []
---

## ⚡ Do This First: Load Agent Profile

`/ad-hoc-profile-load curator-carla` (role: implementer/curator). This WP is
removal + deployment + doctrine documentation — keep the ADR trail coherent.

## Objective

Retire the polling reconciler now that the event-driven path (WP03/WP04) works:
remove the daemon + `goldberg watch` CLI, swap the deployment service, and record
the decision (ADR 0013 supersedes 0011). **Sequencing**: only after WP03+WP04 are
merged and verified — the new path must work before the old one is deleted.

## Context

- The `reconcile/reconciler.py` diff logic was extracted into `ingest/catchup.py`
  in WP03 — so removing `reconcile/` here loses nothing.
- `cli.py` is owned by WP03; removing the `watch` command is a small, declared
  out-of-map edit here (note it in the WP history with a one-line rationale).
- The live `goldberg-reconciler` container is already stopped; this WP removes it
  from the compose topology so it is not recreated.

## Subtasks

### T019 — Remove the reconciler daemon + CLI
- Delete `src/goldberg_system/reconcile/` (daemon path) — confirm nothing but the
  removed `watch` command imports it (catch-up now lives in `ingest/`).
- Remove the `goldberg watch` command from `cli.py` (out-of-map edit; rationale in
  history). Remove now-dead tests referencing the reconciler daemon, or repoint
  them at `ingest/catchup.py`.

### T020 — Deployment swap
- `deploy/docker-compose.yml`: replace the `reconciler` service with an `ingest`
  service running `goldberg ingest-serve` (same volumes: `goldberg-raw:ro` incl.
  `.git`, writable `config/`; env `${GOLDBERG_ES_URL}`, `${NATS_URL}`, `docling:5001`;
  `/health` on 8098; `restart: unless-stopped`).
- Add `deploy/Dockerfile.ingest` (or rename `Dockerfile.reconciler`) — same base,
  CMD `goldberg ingest-serve`.

### T021 — ADRs + runbooks (DIR-002)
- New `doc/decisions/0013-event-driven-ingestion.md`: context (reconciler was
  wasteful + DIR-004), decision (git-hook → NATS → processor + one-shot catch-up),
  consequences; **Supersedes ADR 0011**.
- Update `doc/decisions/0011-*` status → "Superseded by 0013"; update ADR 0012
  deployment note (`reconciler`→`ingest`).
- Update the ingestion runbook (`doc/runbooks/auto-ingestion-reconciler.md` →
  rename/replace with the new event-driven flow, or clearly mark superseded and
  point to the WP04 wiring runbook).

## Branch Strategy

Planning base + merge target `feat/goldberg-nats-es-archive`; per-lane worktrees
from `lanes.json`.

## Definition of Done

- [ ] `reconcile/` daemon removed; `goldberg watch` gone; no dangling imports.
- [ ] Compose runs `ingest` (not `reconciler`); Dockerfile.ingest present.
- [ ] ADR 0013 added and supersedes 0011; 0011/0012 + runbook updated.
- [ ] Test suite green after removal (no references to deleted code).

## Risks / Reviewer guidance

- **Risk**: deleting `reconcile/` before catch-up is proven elsewhere. Reviewer:
  confirm `ingest/catchup.py` fully covers the extracted diff and WP03 is merged.
- **Risk**: stale doc references to `goldberg watch`. Reviewer: grep docs for
  `watch`/`reconciler` and confirm they're updated.
