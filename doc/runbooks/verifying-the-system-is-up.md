# Runbook — Verifying the system is up

The five checks that confirm the Goldberg processing stack ([ADR 0012](../decisions/0012-deployment-topology.md))
is genuinely live: all three services healthy, each reachable, the doctor board green,
the reconciler heartbeating, and a real document making it end-to-end from
`goldberg-raw` to a provenance-carrying search hit.

Run these after a `docker compose up -d` (or a Portainer stack deploy). The stack is the
**processing services only** — docling, reconciler, mcp; Elasticsearch is external and
reached via `${GOLDBERG_ES_URL}`, so "is ES up?" is answered by the doctor board, not by
a stack container.

> Prerequisite: you copied `.env.example` → `.env`, set `GOLDBERG_ES_URL`,
> `GOLDBERG_RAW_PATH` (the git root of `goldberg-raw`, so `.git` is included), and
> `GOLDBERG_MANIFEST_PATH` (the writable config dir holding a container-tuned
> `projects.yaml` with `projects.raw.path: /data/goldberg-raw`, `projects.system.path:
> /app`, and the persistent `provenance-manifest.json`).

All commands assume you are in the repo root (where `docker-compose.yml` lives).

## 1. All services report healthy

```bash
docker compose ps
```

Expected: three services — `docling`, `reconciler`, `mcp` — each `running (healthy)`.
`(health: starting)` is normal for up to ~40s after boot (Docling loads first); re-run
until all three read `healthy`. Anything `restarting` or `exited` → jump to
Troubleshooting.

## 2. Per-service healthchecks

Hit each service directly. Ports below are the `.env` defaults
(`DOCLING_PORT=5001`, `RECONCILE_HEALTH_PORT=8098`, `GOLDBERG_MCP_PORT=8765`).

```bash
# Docling — expect HTTP 200 and an ok/status JSON body
curl -fsS http://localhost:5001/health && echo

# Reconciler — expect HTTP 200 and the last-cycle JSON
curl -fsS http://localhost:8098/health && echo

# MCP — the /mcp endpoint rejects non-MCP requests by design, so confirm the port is
# listening rather than expecting a 200:
nc -z localhost 8765 && echo "mcp port 8765 open"
```

A real MCP `initialize` handshake (optional, stronger than the port check):

```bash
curl -isS http://localhost:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}' \
  | grep -i 'HTTP/\|Mcp-Session-Id'
```

Expected: `HTTP/1.1 200` with an `Mcp-Session-Id` header.

## 3. The doctor board — CLI and MCP tool

The doctor board is the single source of truth for "is every component up?" (ADR 0008).
It probes ES, Docling, the enricher, the MCP server, the live-index watcher, and wiki
synthesis. Run it two ways; both must agree.

**CLI** (from inside the reconciler or mcp container, which have `goldberg` + env):

```bash
docker compose exec reconciler goldberg doctor
# machine-readable:
docker compose exec reconciler goldberg doctor --yaml
```

**MCP tool** `component_health` — the same board for MCP-capable agents, no shell needed
([ADR 0012](../decisions/0012-deployment-topology.md), reuses `run_doctor`, so it can
never drift from the CLI). Call it from any MCP client pointed at
`http://<host>:8765/mcp`; it returns the `DoctorReport` as JSON.

Expected board (healthy stack, ES reachable):

| Component | Expected | Notes |
|---|---|---|
| `elasticsearch` | UP | cluster responds; required indices present |
| `docling` | UP | `/health` ok at `http://docling:5001` |
| `enricher` | UP or DEGRADED | DEGRADED if `OPENAI_API_KEY` is blank — acceptable |
| `mcp_server` | UP | `initialize` handshake ok |
| `live_index_watcher` | UP | inferred from a recent pipeline event (see step 4) |
| `wiki_synthesis` | DEGRADED | honest default — no synthesis pipeline wired yet |

`overall` is the worst status. `UP` or `DEGRADED` (from the enricher/wiki legs) is a
healthy stack; any component `DOWN` → Troubleshooting. CLI exit code: `0` only when
`overall` is UP.

## 4. Confirm the reconciler heartbeat

The `live_index_watcher` probe is *inferred* from the freshness of
`goldberg_pipeline_events`: the reconciler emits a heartbeat event every cycle, so a
recent event means the watcher is alive. Confirm a heartbeat landed within the last
interval:

```bash
# reconciler logs — look for cycle output roughly every RECONCILE_INTERVAL (default 300s)
docker compose logs --tail=20 reconciler

# or straight from ES — most recent pipeline event timestamp:
curl -fsS "${GOLDBERG_ES_URL}/goldberg_pipeline_events/_search?size=1&sort=ts:desc&_source=ts,stage,status" && echo
```

Expected: a `ts` within the last few minutes. If the newest event is older than the
freshness window (default 15 min), the doctor will read `live_index_watcher` DOWN even
though the container is up — check the reconciler logs.

## 5. The live smoke test — drop a document, watch it become queryable

The real acceptance test (SC-003): a file dropped into `goldberg-raw` is auto-ingested
with full provenance and queryable, no manual step. Use a tiny, unmistakable marker
document, wait **one reconcile interval**, then confirm the hit — and clean up.

```bash
# 1. Drop a marker doc into goldberg-raw (must be inside an allowlisted tree; adjust the
#    subdir to a real matter dir). GOLDBERG_RAW_PATH is the host path from your .env.
MARKER="verify-$(date +%s)"
DROP="${GOLDBERG_RAW_PATH}/verify-smoketest"
mkdir -p "${DROP}"
printf '# Verify smoke test\n\nUnique marker: %s\n' "${MARKER}" > "${DROP}/${MARKER}.md"

# 2. Wait one reconcile interval (default 300s) plus a little slack.
sleep 330

# 3. Confirm it is queryable WITH provenance (doc_id + raw_path shown by search).
docker compose exec reconciler goldberg search "${MARKER}"
```

Expected: one hit whose `raw_path` points at `verify-smoketest/<marker>.md`. That the hit
exists at all proves the full chain ran — provenance registered (manifest entry with
`raw_commit` + `raw_sha256`), extracted, enriched, indexed — because the reconciler
never indexes a document without writing its manifest entry first (ADR 0011). For the
full provenance record:

```bash
# grab the doc_id from the search output, then:
docker compose exec reconciler goldberg get <doc_id>
```

**Clean up** — remove the marker so it does not pollute the corpus:

```bash
rm -rf "${DROP}"
# The document remains in ES until re-derived; delete the test doc from the index if your
# environment requires a clean corpus (optional — it is clearly marked and harmless):
#   curl -fsS -X POST "${GOLDBERG_ES_URL}/goldberg_documents/_delete_by_query" \
#     -H 'Content-Type: application/json' \
#     -d '{"query":{"match_phrase":{"content":"'"${MARKER}"'"}}}'
```

If the marker never appears after a full interval, check the reconciler logs and the DLQ
(`docker compose exec reconciler goldberg dlq`) — the file may be outside an allowlisted
tree, or its extraction dead-lettered.

## Troubleshooting

**ES unreachable (doctor `elasticsearch` DOWN, or step 4 curl fails).**
The stack does not host ES — confirm the external instance is up and that
`GOLDBERG_ES_URL` in `.env` is correct and reachable *from inside the containers*
(`docker compose exec reconciler python3 -c "import urllib.request as u,os;print(u.urlopen(os.environ['GOLDBERG_ES_URL'],timeout=4).status)"`).
By design the services start and report DEGRADED/DOWN rather than crash-loop, and recover
when ES returns.

**Docling OOM on a large scan (docling restarts, OCR files dead-letter).**
Expected on a petite host — the container is memory-capped (`DOCLING_MEM_LIMIT`, default
4g) so a huge scan OOMs the container, not the host; the reconciler dead-letters that one
document and retries next cycle. Text/passthrough files keep flowing regardless. To
process the offending file, raise `DOCLING_MEM_LIMIT` or offload Docling to a bigger host
by pointing `GOLDBERG_DOCLING_URL` at it.

**Manifest not writable / registrations lost after restart.**
The reconciler writes `config/provenance-manifest.json`; it must be on the writable,
persistent `${GOLDBERG_MANIFEST_PATH}` mount (`/app/config`). Symptoms: reconciler logs a
write/permission error, or every restart re-registers the same files. Check the host dir
exists and is writable by the container UID
(`docker compose exec reconciler sh -c 'touch /app/config/.wtest && rm /app/config/.wtest && echo writable'`).
Do **not** let the manifest live inside the image layer.

**Reconciler up but `live_index_watcher` DOWN.**
No recent pipeline event within the freshness window. Check `docker compose logs
reconciler` for a stuck cycle; confirm ES is writable (the heartbeat is an ES write).

**`git log` / `raw_commit` errors ("detected dubious ownership").**
The reconciler runs `git` over the bind-mounted `goldberg-raw`, which may be owned by a
different UID than the container user. If provenance stamping fails with a dubious-
ownership error, mark the mount safe inside the container
(`docker compose exec reconciler git config --global --add safe.directory /data/goldberg-raw`)
or bake it into the image; ensure `GOLDBERG_RAW_PATH` is the working-tree root so `.git`
is present at all.
