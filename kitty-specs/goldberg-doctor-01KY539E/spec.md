# Pipeline Component Health Doctor

## Purpose

Give an operator (or an agent acting for one) an at-a-glance answer to "is the
pipeline actually up?" — a per-component liveness board, so a paused watcher or
an unreachable service is *reported by the system* rather than discovered by
hand-probing with curl.

## User Scenarios & Testing

### Primary scenario
An operator suspects something is wrong with ingestion. They run `goldberg
doctor`. Within a few seconds they see a board listing every major component —
Elasticsearch, Docling OCR, the enricher, the MCP server, the live-index
watcher, and wiki synthesis — each marked **UP**, **DOWN**, or **DEGRADED**,
with a one-line detail (latency, version, reason) and an overall verdict. They
immediately see which component is the problem.

### Exception / edge scenarios
- **A component hangs.** One probe (e.g. Docling) does not respond. The board
  still returns within its overall budget, showing that component as DOWN with a
  "timeout" reason; the other components are unaffected.
- **A component is reachable but not the one expected.** ES answers but a
  required index is missing → that shows as DEGRADED, distinct from DOWN.
- **Nothing is wrong with the data plane but a watcher is off.** The board shows
  every service UP except the live-index watcher, which is DOWN — the exact gap
  the manual experiment surfaced.
- **Machine consumption.** An agent runs `goldberg doctor --yaml` (or via the
  MCP `system_status` tool) and parses the same board as structured data.

### Acceptance
- `goldberg doctor` lists all six components with a status and a detail line.
- The overall exit status reflects the worst component (UP → 0, DEGRADED and
  DOWN → non-zero) so it is usable in scripts/CI.
- `goldberg status` shows a compact component-health section derived from the
  same probes.
- `no_recent_failures` reflects only failures within a recent window.

## Domain Language
- **Component** — an independently-runnable part of the pipeline whose liveness
  can be probed: Elasticsearch, Docling OCR, enricher, MCP server, live-index
  watcher, wiki synthesis.
- **UP** — reachable and functioning as intended.
- **DEGRADED** — reachable but not fully correct (e.g. missing index, stale
  heartbeat, data-plane failures) — the system runs but with reduced integrity.
- **DOWN** — unreachable, erroring, or timed out.
- **Liveness probe** — a fast, read-only check of one component that returns a
  status, a detail string, and a latency.

## Requirements

### Functional Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-001 | Provide a `goldberg doctor` command that probes every major component and prints a per-component board of UP/DOWN/DEGRADED plus a one-line detail (latency/version/reason) and an overall verdict. | Draft |
| FR-002 | Probe **Elasticsearch** liveness: reachable, cluster responds, and the required indices (`goldberg_documents`, `silverbullet-goldberg`, `goldberg_pipeline_events`) exist; missing index → DEGRADED, unreachable → DOWN. | Draft |
| FR-003 | Probe **Docling OCR** liveness via its health endpoint; ok → UP, error/unreachable/timeout → DOWN. | Draft |
| FR-004 | Probe the **enricher** (OpenAI): configuration present and endpoint reachable; missing key → DEGRADED, unreachable → DOWN. The probe must not incur meaningful cost. | Draft |
| FR-005 | Probe the **MCP server** liveness at its configured host/port; serving → UP, port closed/unreachable → DOWN. | Draft |
| FR-006 | Probe the **live-index watcher** liveness and report DOWN when it is not running/paused (using a heartbeat or event-freshness signal, clearly labelled as inferred when it is inferred). | Draft |
| FR-007 | Probe **wiki synthesis** health: report whether the wiki index is present and whether it is stale relative to the document corpus. | Draft |
| FR-008 | Surface a **compact component-health section** in `goldberg status`, derived from the same probes, without changing the existing corpus/pipeline/DLQ sections. | Draft |
| FR-009 | Fix the `no_recent_failures` health check to count only failures within a recent, configurable time window rather than all historical failed events. | Draft |
| FR-010 | Emit the board as structured output (`--yaml`/machine-readable) so agents and the MCP layer can consume the same component statuses. | Draft |
| FR-011 | Set the command's exit code from the worst component status (UP → 0; DEGRADED/DOWN → non-zero) for scripting and CI use. | Draft |

### Non-Functional Requirements

| ID | Requirement | Threshold | Status |
|----|-------------|-----------|--------|
| NFR-001 | Each component probe is individually time-bounded so a hung component cannot hang the report. | Per-probe timeout ≤ 5s; whole board returns ≤ 10s even with every component down. | Draft |
| NFR-002 | Probes are non-destructive and read-only — they never write, delete, or mutate any component or index. | Zero writes/mutations issued by any probe. | Draft |
| NFR-003 | The enricher probe incurs no material API spend. | ≤ 1 metadata/list call, no completions/tokens billed. | Draft |
| NFR-004 | A component being DOWN is visually and structurally distinct from data-plane DEGRADED in both human and machine output. | Distinct status enum value + distinct rendering for every case. | Draft |

### Constraints

| ID | Constraint | Status |
|----|-----------|--------|
| C-001 | Reuse the existing observability module and its Elasticsearch/health abstractions; do not fork a parallel health system. | Draft |
| C-002 | Ships with tests (unit probes with fakes + an integration guard) and updated docs (ADR 0008 observability + a runbook), per charter DIR-002 and the E2E-testing rule. | Draft |
| C-003 | Probes must degrade gracefully when a component's address is unconfigured/unreachable from the host running the command (e.g. the watcher on a remote host) — reporting "unknown/unreachable", never crashing. | Draft |

## Success Criteria
- **SC-001**: An operator can determine which specific component is down from a single `goldberg doctor` run, without any manual curl/docker probing.
- **SC-002**: With every component healthy, the board returns in under 10 seconds and reports all six UP.
- **SC-003**: When a component is stopped, the board reports exactly that component as DOWN within the time budget while the rest report correctly.
- **SC-004**: `goldberg status` no longer reports a permanently-red failure check driven by historical events; the failure signal reflects only recent activity.

## Key Entities
- **ComponentHealth** — { name, status (UP/DEGRADED/DOWN), detail, latency_ms, inferred? }.
- **DoctorReport** — an ordered set of ComponentHealth plus an overall verdict and a generated-at timestamp.

## Assumptions
- The six components listed are the complete set of "major components" for this iteration; additional probes (e.g. Papra, NATS) can be added later behind the same abstraction.
- The command runs from a host that can reach ES and the enricher; components on other hosts (watcher) may only be probeable indirectly (heartbeat/event freshness), which is acceptable and must be labelled as inferred.
- "Recent" for the failure window defaults to a sensible span (e.g. 24h) and is configurable.
