# Portable Single-Stack Deployment

## Purpose

Package every *processing* service the system needs as one portable
`docker-compose` stack — the containers that monitor `goldberg-raw`, extract,
enrich, index, and serve queries — deployable as a Portainer stack on Halob and
liftable to a more powerful host later. Elasticsearch is **not** in the stack: it
is an already-running stateful datastore the services connect to over TCP. The
stack is host-agnostic: point it at an ES URL, mount `goldberg-raw`, and bring it
up anywhere.

## User Scenarios & Testing

### Primary scenario
The operator deploys the stack as a single Portainer stack on Halob (paste the
compose + set the `.env`). All processing services come up healthy and connect to
the existing Elasticsearch over TCP. `goldberg doctor` — via the CLI **or the MCP
tool** — reports every component UP. The operator drops a document into
`goldberg-raw`; within one reconcile interval it is auto-ingested and queryable.

### Lift-and-shift scenario
The operator copies the repo + compose + `.env` to a more powerful host, sets
`GOLDBERG_ES_URL` (and mounts `goldberg-raw`), and runs `docker compose up -d`.
Because `goldberg-raw` + the provenance manifest are the source of truth, the
reconciler rebuilds/refreshes the ES corpus on the new host without a data
migration.

### Exception / edge scenarios
- **ES unreachable at startup.** Services start but report DEGRADED/DOWN via
  `doctor` rather than crash-looping; they recover when ES returns.
- **Docling OOM on a large scan (petite host).** The reconciler dead-letters and
  retries; the stack stays up.
- **A service crashes.** `restart: unless-stopped` brings it back; its healthcheck
  reflects true state.

### Acceptance
- One `docker compose up -d` (or one Portainer stack deploy) brings up docling +
  reconciler + mcp, all healthy, all connected to the external ES.
- `doctor` (CLI and MCP tool) reports the stack's components.
- A document dropped in `goldberg-raw` is auto-ingested with full provenance.
- No second Elasticsearch is created; the existing corpus is untouched.

## Domain Language
- **Stack** — the docker-compose set of *processing* services (docling,
  reconciler, mcp), deployed together.
- **External ES** — the already-running Elasticsearch, referenced via
  `GOLDBERG_ES_URL`, never replicated by the stack.
- **doctor tool** — the component-health board exposed as an MCP tool
  (`component_health`) in addition to the `goldberg doctor` CLI.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-001 | Provide a `docker-compose.yml` defining the processing services — **docling**, **reconciler** (`goldberg watch`), **mcp** — with env-driven config, healthchecks, and `restart: unless-stopped`. | Draft |
| FR-002 | The stack connects to the **existing external Elasticsearch over TCP** via `GOLDBERG_ES_URL`; it does **not** define or replicate an Elasticsearch service. | Draft |
| FR-003 | Add a `component_health` (doctor) **MCP tool** to the MCP server that returns the same `DoctorReport` as the `goldberg doctor` CLI. | Draft |
| FR-004 | `doctor` probes reach ES via `GOLDBERG_ES_URL` and reach sibling stack services (docling, reconciler) by their **compose service names**, not hard-coded IPs. | Draft |
| FR-005 | The reconciler service mounts `goldberg-raw` (read-only for content), reaches docling and ES by URL/name, and runs `goldberg watch` as its entrypoint. | Draft |
| FR-006 | Ship a documented `.env.example` covering every variable (ES URL, Docling URL, OpenAI key, interval/workers/batch, MCP host/port) with **no secrets committed**. | Draft |
| FR-007 | The compose file deploys cleanly as a **Portainer stack** (standard compose, relative build contexts or pinned images, named where needed). | Draft |
| FR-008 | Be **host-agnostic**: a documented lift-and-shift procedure (set `GOLDBERG_ES_URL`, mount `goldberg-raw`, `up`) with the reconciler rebuilding the corpus on a fresh host. | Draft |
| FR-009 | **Retire Papra** from the deployment — it is not part of the stack; document it as superseded. | Draft |
| FR-010 | Provide a **"how to verify the system is up"** runbook: the exact commands/healthchecks to confirm each service is live (compose ps, service healthchecks, `doctor`, a `goldberg watch` heartbeat, and a small-test-document ingest). | Draft |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|----|-------------|-----------|--------|
| NFR-001 | Each service exposes a healthcheck usable by compose/Portainer. | All 3 services report healthy within 60s of a clean start (ES reachable). | Draft |
| NFR-002 | Secrets are injected via env, never baked into images or committed. | 0 secrets in the compose file or images. | Draft |
| NFR-003 | Resource-bounded for the petite host. | Conservative reconciler concurrency/batch defaults; the stack does not peg the host at idle. | Draft |
| NFR-004 | Portable — no host-specific values hard-coded in the compose file. | All host/URL/secret values come from env; changing host = changing `.env`. | Draft |

### Constraints

| ID | Constraint | Status |
|----|-----------|--------|
| C-001 | Reuse the existing images/build files (`Dockerfile.mcp`, `Dockerfile.reconciler`) and the existing `docling-serve` image; do not fork new service code. | Draft |
| C-002 | Do not create, migrate, or risk the existing ES data — connect only. | Draft |
| C-003 | The `component_health` MCP tool reuses `observability/health.run_doctor`; it does not reimplement probes. | Draft |
| C-004 | Ships docs (an ADR for the deployment topology + the verify runbook) per charter DIR-002. | Draft |

## Success Criteria
- **SC-001**: `docker compose up -d` brings docling + reconciler + mcp up healthy, all connected to the pre-existing ES, with no second ES created.
- **SC-002**: `goldberg doctor` (CLI) and the MCP `component_health` tool both return the component board.
- **SC-003**: A small test document dropped into `goldberg-raw` is auto-ingested (full provenance) and queryable, with no manual step.
- **SC-004**: The stack redeploys on a different host by changing only `.env` (ES URL + `goldberg-raw` mount), demonstrating portability.

## Key Entities
- **Compose service** — docling | reconciler | mcp, each with image/build, env, healthcheck, restart policy.
- **Stack env (`.env`)** — the single source of host-specific/secret configuration.

## Assumptions
- Elasticsearch already runs and is reachable at `GOLDBERG_ES_URL` (on Halob today; elsewhere on a new host).
- `goldberg-raw` is mountable into the reconciler container (NAS path on Halob; a bind mount elsewhere).
- Docling on the petite host handles text/passthrough and modest scans; large-scan OOM is tolerated via dead-letter/retry and can be offloaded to a bigger host later.
- The stack may be deployed as one Portainer stack or split into logically-grouped stacks; one compose file is the default deliverable.
