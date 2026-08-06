# Runbook — the live-index service (M5–M7, ADR 0005)

> **RETIRED (M15).** This Papra-webhook live-index service is **superseded** by the
> event-driven ingest service ([ADR 0013](../decisions/0013-event-driven-ingestion.md),
> [runbook](./wiring-the-ingest-trigger.md), `legal_system ingest-serve`) — which itself
> superseded the interim `legal_system watch` reconciler ([ADR 0011](../decisions/0011-auto-ingestion-reconciler.md)).
> The ingest service is provenance-first (registers `goldberg-raw` provenance before
> indexing) and extracts via direct Docling, closing the provenance gap this webhook
> path had. This runbook is kept for historical reference only — do not deploy the
> live-index service.

The automatic pipeline: **drop a file into Papra → Docling extracts it → Papra
fires a `document:created` webhook → the `live-index` service polls for the
content, enriches it (OpenAI, incl. attributed claims), and indexes it into
Elasticsearch** — no manual steps.

## Where it runs

- **Container** `goldberg-live-index` on Halob (from this repo's `Dockerfile`).
  Published on host port **8099** → container 8080. `--restart unless-stopped`.
- **Env** from `/share/Docker/goldberg-live-index/.env` (secrets — Papra/OpenAI
  keys, `GOLDBERG_ES_URL`, `PAPRA_BASE_URL`, `CONTENT_POLL_*`). Not in git.
- **Endpoints:** `POST /webhooks/papra` (the webhook), `GET /health`.

## The Papra webhook (the trigger, M6)

- Registered in Papra: org **Legal** → Settings → Webhooks →
  `goldberg-live-index`, URL `http://192.168.86.31:8099/webhooks/papra`, event
  **`document:created`**.
- Papra blocks private-IP webhook targets by default (SSRF). We allowlisted the
  host via the Papra stack env `WEBHOOK_URL_ALLOWED_HOSTNAMES=192.168.86.31`
  (Portainer stack `papra`, id 85). Alternative: `WEBHOOK_SSRF_PROTECTION_ENABLED=false`.

## Key behaviour — polling for content

Papra fires `document:created` **immediately on ingest, before Docling finishes
extracting**, so the document's `content` is usually empty on first fetch. The
service therefore **polls** Papra for content (`CONTENT_POLL_ATTEMPTS`×
`CONTENT_POLL_INTERVAL`, default 24×5s = 2min) before enriching. Processing is
idempotent (deterministic doc-id), so a re-delivered webhook just updates.

## Operate

```bash
# rebuild + redeploy after a code change (run on Halob, repo at
# /share/home/sal/work/project_goldberg/goldberg-system)
docker build -t goldberg-live-index .
docker rm -f goldberg-live-index
docker run -d --name goldberg-live-index --restart unless-stopped -p 8099:8080 \
  --env-file /share/Docker/goldberg-live-index/.env goldberg-live-index

docker logs -f goldberg-live-index          # watch processing
curl -s http://192.168.86.31:8099/health    # liveness
```

## Verify / troubleshoot

- **Test end to end:** drop a *new* file into `/Volumes/Home/papra/ingest/<legal-org-id>/`;
  within ~30–60s it should appear via `legal_system search`/`legal_system facets`.
- **`skip (no content after polling)` in the logs:** Docling took longer than the
  poll budget, or the format has no extractable text (e.g. `.eml` — Papra doesn't
  extract those). Increase `CONTENT_POLL_ATTEMPTS`, or handle the format via our
  own extractor.
- **Webhook "Last triggered: Never":** Papra couldn't reach the service — check
  the allowlist env and that the container is up/reachable from the papra container.
- **Backstop:** `uv run legal_system reindex` re-processes everything in Papra
  (idempotent), covering any missed webhooks.

## Not yet

Durability (NATS/JetStream), HMAC signature verification (LAN-internal for now),
and routing `.eml`/attachments through our own extractors (M8). See ADR 0005.
