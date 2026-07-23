# ADR 0012 — Deployment topology: a portable processing stack against external ES

**Status:** Accepted · **Date:** 2026-07-22 · **Builds on:** [ADR 0006](./0006-ingestion-provenance-architecture.md), [ADR 0008](./0008-observability-architecture.md), [ADR 0010](./0010-mcp-server.md), [ADR 0011](./0011-auto-ingestion-reconciler.md) · **Retires:** Papra from the deploy ([ADR 0003](./0003-document-management-papra-integration.md))

> **Revised (2026-07-23) by [ADR 0013](./0013-event-driven-ingestion.md).** The stack's
> `reconciler` service (`goldberg watch`, `/health` on 8080) is replaced by an `ingest`
> service (`goldberg ingest-serve`, `/health` on 8098), and the compose file now lives at
> `deploy/docker-compose.yml`. The topology below (three stateless processing services
> against external ES + NATS, source-of-truth = goldberg-raw + manifest) is unchanged;
> only the ingest service's name, trigger, and health port moved — read "reconciler"
> below as "the ingest service".

## Context

By M15 the system had all the runnable pieces — a Docling OCR service, the
`goldberg watch` reconciler ([ADR 0011](./0011-auto-ingestion-reconciler.md)), and the
hosted MCP server ([ADR 0010](./0010-mcp-server.md)) — but no single, portable way to
**deploy them together**. They were documented as separate `docker run` recipes tied to
Halob-specific paths and IPs. The operator needs one artifact they can paste into
Portainer today on Halob and lift to a more powerful host later, changing only
configuration.

Two facts shape the topology:

- **The shared infrastructure (Elasticsearch and NATS) is stateful and already
  running.** ES holds the indexed corpus and the pipeline-event stream; NATS is the
  shared message bus ([ADR 0005](./0005-live-index-pipeline.md); durability/eventing,
  not yet used by the direct-index path). These are the "everything uses them" services:
  every current and future component depends on them, so they are a shared layer that
  should **outlive any redeploy** and stay put (on Halob) while the processing stack is
  the portable piece that can move to a faster host. Re-creating ES inside the stack
  would risk the existing data ([C-002](../../kitty-specs/goldberg-stack-01KY5FCM/spec.md))
  and wrongly couple the stack's lifecycle to a datastore. The stack connects *up* to
  ES and NATS over TCP; moving the stack never means moving the data or the bus.
- **The source of truth is `goldberg-raw` + the provenance manifest, not ES.** Per
  [ADR 0006](./0006-ingestion-provenance-architecture.md), the corpus in ES is a
  *derived* artifact: given `goldberg-raw` (git) and `config/provenance-manifest.json`,
  the ingest service can rebuild/refresh the ES corpus from scratch. That is what makes the
  stack portable — a new host needs the code, the raw tree, and an ES endpoint, not a
  data migration.

## Options considered

1. **All-in-one stack including Elasticsearch.** Rejected: bundles a stateful,
   memory-hungry datastore with stateless processing services, risks the live corpus on
   every redeploy (C-002), and makes "lift to a bigger host" a data-migration exercise
   rather than a config change.
2. **Keep the separate per-service `docker run` recipes.** Rejected: not a single
   deployable unit, not Portainer-friendly, and every recipe re-encodes Halob paths/IPs.
3. **Processing-only compose stack against an external ES over TCP.** **Chosen.** The
   stack is exactly the three stateless processing services; ES is referenced by
   `${GOLDBERG_ES_URL}` and never defined. Host-specific values are entirely env-driven.

## Decision

Ship a **`deploy/docker-compose.yml`** defining three services and nothing else:

- **docling** — `ghcr.io/docling-project/docling-serve-cpu:latest`, port 5001, a
  stdlib `GET /health` healthcheck, memory-capped so a large scan OOMs the container
  (dead-lettered + retried by the ingest service) rather than the petite host.
- **ingest** — built from `deploy/Dockerfile.ingest`, runs `goldberg ingest-serve`
  (startup catch-up, then consumes `goldberg.raw.commit` from NATS — [ADR 0013](./0013-event-driven-ingestion.md))
  with conservative defaults (workers 2 / batch 50 / max-deliver 5), exposes `/health`
  on 8098. (Was the `reconciler` service running `goldberg watch` on 8080.)
- **mcp** — built from the existing `Dockerfile.mcp`, runs `goldberg mcp-serve` on 8765.

**The shared infrastructure is not in the stack.** Elasticsearch is reached over TCP via
`${GOLDBERG_ES_URL}` (e.g. `http://192.168.86.31:9200` on Halob today, elsewhere on a new
host); the existing corpus is only connected to, never re-created (FR-002 / C-002). NATS,
when a component starts using it, is referenced the same way via `${NATS_URL}` — external
and shared, never bundled into this stack.

**Inter-service addressing is by compose service name, never IP** (FR-004). The
ingest service and mcp reach Docling at `http://docling:5001`; only the external ES URL is a
host value in `.env`.

**All host-specific / secret configuration is a single `.env`** (templated by
`.env.example`, gitignored). Moving hosts = editing `.env` (NFR-002 / NFR-004). No IPs,
paths, ports, or secrets are hard-coded in the compose file.

### Volumes (the load-bearing decisions)

- **`goldberg-raw`, read-only, including `.git`.** Mounted `${GOLDBERG_RAW_PATH}` →
  `/data/goldberg-raw:ro`. `GOLDBERG_RAW_PATH` must be the **git working-tree root** so
  the `.git` directory travels with the mount: the ingest service runs `git log` / `git diff`
  over the tree to stamp `raw_commit` provenance and compute the startup catch-up diff ([ADR 0006](./0006-ingestion-provenance-architecture.md)),
  and a mount without `.git` would break provenance.
- **The provenance manifest on a writable, persistent volume.** The ingest service
  *writes* `config/provenance-manifest.json` provenance-first on every commit / catch-up
  pass (it registers new content before indexing, ADR 0013). If that lived inside the image layer it would be lost on every
  restart/redeploy — and with it the record that makes the corpus rebuildable. So the
  config directory is mounted **read-write** (`${GOLDBERG_MANIFEST_PATH}` → `/app/config`)
  and holds both the persistent manifest and the container-tuned `projects.yaml`
  (`projects.raw.path=/data/goldberg-raw`, `projects.system.path=/app`), which the ingest
  service reads (`GOLDBERG_PROJECTS_CONFIG`) so `config/provenance-manifest.json` resolves
  inside the container without path inference.

### Doctor as an MCP tool

The `goldberg doctor` component-health board ([ADR 0008](./0008-observability-architecture.md))
is now also an **MCP tool**, `component_health`, so an MCP-capable agent can ask "is
every component up?" without a shell. It reuses `observability/health.run_doctor`
verbatim — no reimplemented probes (C-003) — and returns the `DoctorReport` as a dict,
matching the other structured MCP tools. Its probes reach the sibling stack services by
compose name and ES via `${GOLDBERG_ES_URL}` (FR-004).

### Portability model

`goldberg-raw` + the manifest are the source of truth; ES is derived. To lift-and-shift:
copy the repo + `.env`, set `GOLDBERG_ES_URL` and `GOLDBERG_RAW_PATH`, and
`docker compose -f deploy/docker-compose.yml up -d`. The ingest service rebuilds/refreshes
the ES corpus on the new host from the raw tree and manifest (startup catch-up) — no ES
data migration (FR-008).

## Consequences

- **Stateless, disposable services (FR-001):** the stack can be torn down and
  redeployed without data loss; all state lives in external ES and the mounted raw tree
  + manifest. `restart: unless-stopped` + per-service healthchecks give Portainer true
  liveness (NFR-001).
- **No second Elasticsearch (FR-002 / C-002):** the existing corpus is untouched; the
  stack only connects.
- **Portable by construction (FR-007 / FR-008 / NFR-004):** standard compose, pinned
  image + relative build contexts, deployable as a Portainer stack; a new host is a new
  `.env`.
- **Petite-host-safe (NFR-003):** conservative ingest concurrency/batch and a
  Docling memory cap; a large scan degrades (dead-letter + retry) instead of taking the
  host down.
- **Papra retired from the deployment (FR-009):** consistent with ADR 0011 retiring the
  Papra *trigger*, Papra is not a service in this stack. [ADR 0003](./0003-document-management-papra-integration.md)
  is superseded as a deployment component; Papra may remain a human drop-target/viewer
  out-of-band, but it is off the ingest path and out of the stack.
- **Deployment carries a config obligation:** because `projects.yaml` is not baked into
  the images and the app resolves the raw path from it, the mounted config dir must ship
  a container-tuned `projects.yaml`. This is documented in `.env.example` and the verify
  runbook; it is the one manual step per host.
- **Not deployed by this mission (C-004):** this mission ships the compose file, env
  template, ADR, and the verify runbook. Actually bringing the stack up on Halob is a
  separate operational step (gated on how each host reaches Docling and ES).

## Downstream

Unblocks the M-deployment goal (one portable stack). The verify runbook
([`doc/runbooks/verifying-the-system-is-up.md`](../runbooks/verifying-the-system-is-up.md))
is the operator's acceptance procedure. A future increment could split the stack into
logically-grouped Portainer stacks or offload Docling to a bigger host by changing only
`${GOLDBERG_DOCLING_URL}`.
