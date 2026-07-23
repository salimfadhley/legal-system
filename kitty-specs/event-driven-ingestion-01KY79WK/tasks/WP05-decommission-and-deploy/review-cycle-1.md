# WP05 review — cycle 1: CHANGES REQUESTED

The core of this WP is correct and well executed. The removal is clean, the ADR
trail is coherent, the compose/Dockerfile are internally valid, and the test
suite is green. **Two deploy-surface consistency defects** block approval — both
are in the operator-facing deployment surface, which matters because this WP
deploys to live Halob immediately after merge and its stated purpose is
"deployment + docs".

## What passed (verified)

- `src/goldberg_system/reconcile/` deleted; `git grep "goldberg_system.reconcile\b"`
  and `from goldberg_system import reconcile` in `src/`/`tests/` are empty (only hit
  is the decommission guard test asserting the module is gone).
- `src/goldberg_system/observability/reconcile.py` **intact**; its tests survive in
  the trimmed `tests/test_reconcile.py` (90 lines, completeness API only) —
  `tests/test_reconcile.py` + `tests/unit/test_reconcile_decommissioned.py` = 9 passed.
- `goldberg watch` gone from the CLI; `--help` lists `ingest-serve`, `publish-commit`,
  `ingest`; `import goldberg_system.cli` clean. `ingest/catchup.py` preserved.
- `deploy/Dockerfile.ingest` present, `CMD ["goldberg","ingest-serve"]`.
- `deploy/docker-compose.yml`: `ingest` service, external ES/NATS via
  `${GOLDBERG_ES_URL}`/`${NATS_URL}` (never bundled), `goldberg-raw:ro` incl `.git`
  + writable config + `projects.container.yaml:ro`, health 8098, `restart:
  unless-stopped`. `docker compose config` validates.
- ADR 0013 Accepted, Supersedes 0011; ADR 0011 Superseded-by-0013; ADR 0012 updated
  reconciler→ingest + notes compose moved to `deploy/`; `auto-ingestion-reconciler.md`
  banner-marked retired → `wiring-the-ingest-trigger.md`.
- `tests/unit` 67 passed; `tests/` (ex `test_mcp_server.py`) 256 passed, 2 skipped.
  The `test_mcp_server.py` collection error is the pre-existing optional-`mcp`-extra
  issue, not from this WP. Diff-scoped ruff clean.

## Issue 1 (PRIMARY, blocking) — `.env.example` is out of sync with the shipped compose

The new `deploy/docker-compose.yml` consumes these env vars, none of which the
repo-root `.env.example` (the template the compose header tells operators to copy
to `.env`) defines:

    INGEST_DURABLE  INGEST_WORKERS  INGEST_MAX_DELIVER  INGEST_BATCH  INGEST_HEALTH_PORT  NATS_URL

`.env.example` instead still documents the retired reconciler knobs the compose
never reads — `RECONCILE_INTERVAL`, `RECONCILE_WORKERS`, `RECONCILE_BATCH`,
`RECONCILE_HEALTH_PORT` (comment: "the reconciler's :8080 /health") — and has
`NATS_URL` commented out with the note "not yet actively used by the direct-index
path".

Why this blocks (not cosmetic):
- The stack boots because every `INGEST_*` var in the compose has a `:-default`, so
  the **defaults path works on Halob** — but an operator who tunes
  `RECONCILE_WORKERS=4` / `RECONCILE_BATCH=…` per the template gets a **silent
  no-op** (the compose reads `INGEST_WORKERS`/`INGEST_BATCH`).
- `NATS_URL` is now **required** by the event-driven ingest service; the template
  still tells operators it is unused. On any non-Halob host the compose default
  points at `nats://192.168.86.31:4222`, and because the template says NATS is
  unused the operator won't override it — triggers silently never arrive (only the
  startup catch-up runs). That defeats the portable-stack goal of ADR 0012 / NFR-004
  (the `.env` is meant to be the single per-host config surface).
- `RECONCILE_INTERVAL` is obsolete under event-driven ingest (there is no interval).

Fix: update `.env.example` to match the shipped compose — replace the reconciler
tuning block with the ingest knobs (`INGEST_WORKERS`, `INGEST_BATCH`,
`INGEST_MAX_DELIVER`, `INGEST_DURABLE`, `INGEST_HEALTH_PORT=8098`), drop
`RECONCILE_INTERVAL`, uncomment `NATS_URL` and note it is now required by the ingest
service, and fix the "reconciler" / ":8080" wording in the surrounding comments
(the health port is 8098). (`.env.example` is outside `owned_files` but is the
direct companion to the `deploy/**` compose this WP rewrote; two other out-of-map
edits — cli.py and the root compose/Dockerfile removals — were already made with
rationale, so keeping the template consistent is in the same lane. Note it in the WP
history.)

## Issue 2 (MINOR, blocking) — stale root-compose reference in the verify runbook

`doc/runbooks/verifying-the-system-is-up.md` line 31 still reads "All commands
assume you are in the repo root (where `docker-compose.yml` lives)", and its
`docker compose ps` / `docker compose up -d` commands (≈ lines 20, 36) omit
`-f deploy/docker-compose.yml`. The root `docker-compose.yml` was removed by this WP
and now lives at `deploy/docker-compose.yml` (ADR 0012 says so). The runbook's top
banner updated reconciler→ingest but not the compose file path, so an operator
following this "is the system up?" runbook right after the live deploy runs
`docker compose ...` from repo root and gets "no configuration file provided".

Fix: point the runbook at `deploy/docker-compose.yml` (either `cd deploy` or add
`-f deploy/docker-compose.yml` to the commands) and correct line 31.

## Not blocking

- `cli.py:744` and the retired `auto-ingestion-reconciler.md` / superseded ADR 0011
  mention `watch`/`Dockerfile.reconciler` — these are correct historical references,
  leave them.
