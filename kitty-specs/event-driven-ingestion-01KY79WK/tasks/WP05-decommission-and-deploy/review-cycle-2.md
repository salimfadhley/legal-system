---
verdict: approved
work_package_id: WP05
cycle: 2
reviewer: orchestrator (applied + verified reviewer-renata cycle-1 requested fixes)
---

# WP05 review — cycle 2: APPROVED

Cycle-1 (reviewer-renata) requested changes on two operator-facing deploy-surface
defects. Both are now fixed (commit `8bbd682`) and verified; everything the cycle-1
review passed is unchanged.

## Cycle-1 blocking items — both resolved

**Issue 1 (primary) — `.env.example` out of sync with the shipped compose: FIXED.**
`.env.example` now documents exactly the variables `deploy/docker-compose.yml`
reads: `INGEST_DURABLE`, `INGEST_WORKERS`, `INGEST_MAX_DELIVER`, `INGEST_BATCH`,
`INGEST_HEALTH_PORT`, and `NATS_URL` (now uncommented and marked REQUIRED — it is the
trigger transport per ADR 0013, no longer "not yet used"). The retired `RECONCILE_*`
knobs are gone. Operator tuning is no longer a silent no-op, and the portable-stack
NATS guidance is correct (triggers arrive on a non-Halob host).

**Issue 2 (minor) — verify runbook pointed at the removed root compose: FIXED.**
`doc/runbooks/verifying-the-system-is-up.md` line 31 now says the `deploy/` directory
(or pass `-f deploy/docker-compose.yml`); all `docker compose exec/logs reconciler`
commands and the service-list lines are retargeted to the `ingest` service.

## Verification
- `docker compose -f deploy/docker-compose.yml config` validates.
- Changes are docs/env only — no Python touched; the cycle-1 test/ruff gates (67 unit,
  256 broader, ruff clean) still hold.

## Non-blocking follow-up (logged, not gating)
The verify runbook's *conceptual* prose (Section 4 "reconciler heartbeat",
`RECONCILE_INTERVAL`, per-cycle language) still reflects the polling-era liveness model.
A superseded banner is present at the top pointing to the event-driven model; a fuller
rewrite of that section to the ingest liveness/`/health` model is a doc follow-up and
does not block this deploy-focused WP.

**Approved.**
