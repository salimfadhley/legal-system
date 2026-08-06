# Runbook — component health (`legal_system doctor`)

**Audience:** an operator (or an agent acting for one) who needs a fast answer to
*"is the pipeline actually up?"* — not "did document X ingest?" (that's
[`legal_system trace`/`audit`](../decisions/0008-observability-architecture.md)), but "is
every major component reachable and doing its job right now?".

`legal_system doctor` probes the six major components concurrently and prints a
per-component liveness board with an overall verdict. Every probe is **read-only**,
individually **time-bounded** (≤5s), and never crashes the board — a hung or dead
component shows as **DOWN** while the rest report normally. The whole board returns in
under ~10s even if everything is down.

## Usage

```bash
uv run legal_system doctor                     # human board (colourised)
uv run legal_system doctor --yaml              # structured YAML for agents/CI
uv run legal_system doctor --freshness-window 300   # tighten the watcher freshness window (s)
```

A compact version of the same board is also folded into `legal_system status` (and
`legal_system status --yaml`) under a `components:` section, so the single system-overview
command shows control-plane health alongside the corpus/pipeline/DLQ data plane.

## What each status means

Three values — **DOWN is deliberately distinct from DEGRADED**: DOWN means "can't
reach it / it's erroring", DEGRADED means "reachable but not fully correct".

| Status | Meaning |
|--------|---------|
| **UP** | Reachable and functioning as intended. |
| **DEGRADED** | Reachable but not fully correct (e.g. a missing index, or synthesis not wired). The system runs, with reduced integrity. |
| **DOWN** | Unreachable, erroring, or timed out. |

The **overall** verdict is the worst component (DOWN > DEGRADED > UP).

## The components

| Component | UP means | DEGRADED means | DOWN means |
|-----------|----------|----------------|------------|
| **elasticsearch** | Cluster responds and all three required indices (`goldberg_documents`, `silverbullet-goldberg`, `goldberg_pipeline_events`) exist. | Cluster responds but a required index is **missing** (named in the detail). | Cluster unreachable / timed out. |
| **docling** | `GET /health` returns `{"status":"ok"}`. | — | Health check failed, unreachable, or timed out. Start the SSH tunnel or check the Docling service. |
| **enricher** (OpenAI) | `GET /v1/models` returned HTTP 200. Metadata call only — **no tokens billed.** | **No `OPENAI_API_KEY`** configured — the enricher can't run, but nothing is on fire. | API returned a non-200 (e.g. 401 bad key) or was unreachable. |
| **mcp_server** | A streamable-http `initialize` handshake (`POST /mcp`) returned HTTP 200 with an `Mcp-Session-Id`. | — | Port closed / connection refused / timed out / non-200. Start it with `uv run legal_system mcp-serve`. |
| **live_index_watcher** *(inferred)* | The newest pipeline event is within the freshness window (default 15 min) — events are flowing. | — | No recent (or no) pipeline events — the watcher is likely paused/stopped. |
| **wiki_synthesis** | Wiki index present **and** synthesis is wired to ingest. | Wiki index present but **not refreshed from ingest** (the current state — no synthesis pipeline is wired yet). | Wiki index missing entirely. |

### Two results are *inferred* — read them carefully

- **live_index_watcher** runs on a remote host (Halob) that the command can't reach
  directly, so its status is **inferred** from pipeline-event freshness. UP here means
  "events are flowing", not "the process answered a ping". Flagged `(inferred)` /
  `inferred: true`. Widen or tighten the window with `--freshness-window` if your
  ingest cadence is bursty.
- **wiki_synthesis** reports **DEGRADED** by design until a synthesis refresh
  mechanism is wired — the index exists but isn't being rebuilt from ingest. This is
  intentionally not painted green.

## Exit codes

`legal_system doctor` sets its exit code from the overall (worst) status, so it drops
straight into a script, cron job, or CI gate:

| Overall | Exit code |
|---------|-----------|
| UP | `0` |
| DEGRADED | non-zero (`1`) |
| DOWN | non-zero (`1`) |

```bash
uv run legal_system doctor --yaml || echo "pipeline not fully healthy — investigate"
```

## Related

- `legal_system status` — full system overview (health checks + corpus + pipeline + DLQ),
  now with the compact `components:` board.
- `legal_system trace <raw_path|sha256|doc_id>` — why one *document* did (not) ingest.
- `legal_system audit --manifest <m.json>` — completeness (did anything not ingest, or get
  deleted from raw and left behind). Full runbook:
  [Auditing completeness](./auditing-completeness.md).
- [ADR 0008 §6b](../decisions/0008-observability-architecture.md) — the design and
  probe semantics.
