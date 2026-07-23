# Runbook — Auto-ingestion reconciler (`goldberg watch`)

> **RETIRED (2026-07-23) — superseded by event-driven ingestion.** The polling
> reconciler (`goldberg watch`) and its `goldberg_system.reconcile` daemon have been
> **removed**. The canonical automatic ingest path is now the event-driven ingest
> service (`goldberg ingest-serve`): a `goldberg-raw` commit publishes a
> `goldberg.raw.commit` event onto NATS, and a durable processor ingests that commit's
> changed files provenance-first (with a one-shot startup catch-up). See
> **[ADR 0013](../decisions/0013-event-driven-ingestion.md)** for the decision and
> **[Wiring the ingest trigger](./wiring-the-ingest-trigger.md)** for the operational
> recipe (git hooks → NATS → `goldberg ingest-serve`). This runbook is kept as the
> historical record of the reconciler; the reconcile *model* (provenance-first,
> manifest + direct Docling) is preserved unchanged in the new service, but the
> `goldberg watch` commands below no longer exist.

The reconciler was the **canonical automatic ingest path** ([ADR 0011](../decisions/0011-auto-ingestion-reconciler.md),
supersedes the retired Papra webhook of [ADR 0005](../decisions/0005-live-service-webhook-driven.md)).
Drop a file into `goldberg-raw`, and within one reconcile interval it is registered
with provenance, extracted, enriched, indexed, and queryable — no manual
`goldberg migrate reingest`.

## What it does — the reconcile cycle

Each cycle (`Reconciler.run_cycle`):

1. **Refresh provenance (before indexing).** Walks the allowlisted trees
   (`config/evidence-allowlist.yaml`); for every file whose content SHA-256 is not yet
   in `config/provenance-manifest.json`, it registers a manifest entry — `sha256`, git
   `raw_commit`, and `matters` / `document_type` / `origin` from the nearest tree
   `metadata.yaml` — and persists the manifest atomically. Reuses the same per-file
   derivation as `goldberg migrate manifest`. **No document is indexed without a
   manifest entry written first** (provenance-safe by construction).
2. **Compute the resume set.** Reads the `raw_sha256` values already in Elasticsearch.
3. **Ingest a bounded batch of the difference.** Extract (direct Docling) → enrich →
   index the not-yet-indexed, non-media files via `reingest_from_raw`, emitting
   pipeline events. Bounded by `--batch` per cycle.

The loop emits a **heartbeat** pipeline event every cycle (keeps the `doctor` watcher
probe UP), sleeps `--interval`, and **never dies on a per-file error** — a bad
document dead-letters to the DLQ and the daemon carries on.

### Docling reachability & graceful degradation

- Text / passthrough files (`.md` `.txt` `.json` `.csv` `.tsv`) are read **without
  Docling** — they always flow, even when Docling is down.
- OCR-needing files (PDFs, images) require Docling. If Docling is unreachable, they
  **dead-letter** (a `failed` event) and are **retried on the next cycle** — never
  indexed empty, never crashing the daemon.
- Docling currently runs on the **Mac**. For a Halob deployment, either tunnel the
  Mac's Docling to Halob or run a Halob-local `docling-serve`, and point
  `GOLDBERG_DOCLING_URL` at it (see below).

## Run it

### One cycle (testing / cron)

```bash
uv run goldberg watch --once --workers 2 --batch 50
# isolated test index:
uv run goldberg watch --once --index goldberg_documents_test --batch 5
```

Prints one summary line:
`[<ts>] scanned=.. new=.. indexed=.. skipped=.. dead_lettered=.. elapsed=..s`.

### Forever (daemon)

```bash
uv run goldberg watch --interval 300 --workers 2 --batch 50
```

Also serves `GET /health` (default port 8080) returning the last cycle.

### Config knobs

| Flag | Default | Meaning |
|------|---------|---------|
| `--interval` | 300 | Seconds between cycles (NFR-002: latency ≤ interval + 2 min). |
| `--workers` | 2 | Concurrent documents per cycle (conservative — Halob is 4-core). |
| `--batch` | 50 | Max documents ingested per cycle (bounds CPU per cycle). |
| `--once` | off | Run exactly one cycle and exit. |
| `--index` | env | Target index (use `goldberg_documents_test` for isolation). |
| `--health-port` | 8080 | `GET /health` port (`0` disables; ignored with `--once`). |
| `--manifest` | `config/provenance-manifest.json` | Manifest path. |

## Run on Halob (container)

Build and run the image (`Dockerfile.reconciler`):

```bash
docker build -f Dockerfile.reconciler -t goldberg-reconciler .

docker run -d --name goldberg-reconciler \
  -e GOLDBERG_ES_URL=http://elasticsearch:9200 \
  -e GOLDBERG_DOCLING_URL=http://docling:5001 \
  -e OPENAI_API_KEY=sk-... \
  -v /srv/goldberg-raw:/data/goldberg-raw:ro \
  -v /srv/goldberg-system/config:/app/config \
  -p 8080:8080 \
  goldberg-reconciler \
  goldberg watch --interval 300 --workers 2 --batch 50
```

Or as a compose service:

```yaml
services:
  reconciler:
    build:
      context: .
      dockerfile: Dockerfile.reconciler
    environment:
      GOLDBERG_ES_URL: http://elasticsearch:9200
      GOLDBERG_DOCLING_URL: http://docling:5001   # or the tunnelled Mac Docling
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    volumes:
      - /srv/goldberg-raw:/data/goldberg-raw:ro
      - /srv/goldberg-system/config:/app/config    # manifest is read+written here
    ports:
      - "8080:8080"
    command: ["goldberg", "watch", "--interval", "300", "--workers", "2", "--batch", "50"]
    restart: unless-stopped
```

### Environment

| Var | Purpose |
|-----|---------|
| `GOLDBERG_ES_URL` | Halob Elasticsearch (documents **and** `goldberg_pipeline_events`). |
| `GOLDBERG_DOCLING_URL` | How this container reaches `docling-serve`. Docling runs on the Mac today — tunnel it to Halob, or run a Halob-local Docling and use `http://docling:5001`. Text/passthrough files ingest regardless. |
| `OPENAI_API_KEY` | Enrichment (summaries, claims, entities). |
| `GOLDBERG_ES_INDEX` | Optional document index override (default `goldberg_documents`). |

The manifest volume must be **read-write** — the reconciler persists new provenance
entries to `config/provenance-manifest.json`. `goldberg-raw` can be mounted read-only.
The container installs `git` because `raw_commit` derivation shells out to git against
the `goldberg-raw` checkout (mount the working tree with its `.git`).

## Verify

1. **Drop a file** into an allowlisted tree under `goldberg-raw` (commit it so
   `raw_commit` resolves), then run one cycle:
   `uv run goldberg watch --once`.
2. **Search for it:** `uv run goldberg search "<some text from the file>"` — it
   should appear, with `raw_path` / `matters`.
3. **Watcher UP:** while the daemon runs, `goldberg doctor` reports the
   `live_index_watcher` probe **UP** (it infers liveness from recent
   `goldberg_pipeline_events` — the heartbeat guarantees fresh events even on idle
   cycles).
4. **Health:** `curl localhost:8080/health` → `{"status":"ok","last_cycle":{…}}`.

## DLQ / troubleshooting

- **A file didn't ingest.** `goldberg trace <raw_path|sha256|doc_id>` shows its
  pipeline timeline — where it stopped and why.
- **What's failing right now.** `goldberg dlq` (add `--status skipped`) lists
  failed/skipped documents. `goldberg status` shows per-stage/status counts and DLQ
  depth.
- **OCR files dead-lettering.** Docling is unreachable — check `GOLDBERG_DOCLING_URL`
  and that Docling is up (`curl $GOLDBERG_DOCLING_URL/health`). They retry each cycle
  once Docling returns; text files are unaffected.
- **Empty extraction (`skipped-empty`).** A blank/graphic-only document with no
  OCR-able text. It's recorded so it doesn't re-fill the batch every cycle; add
  genuinely-hard cases to `config/hard-cases.yaml`.
- **Nothing ingesting, `scanned=0`.** Check the allowlist and that files sit under an
  allowlisted tree with a resolvable `metadata.yaml` chain.
- **Backlog not clearing.** Raise `--batch` / `--workers` (mind the 4-core Halob) or
  shorten `--interval`. Each cycle ingests up to `--batch` pending docs.
- **Restart-safe.** State lives in Elasticsearch (resume set) and the manifest on
  disk; a restart re-derives both. After a restart the reconciler re-attempts
  previously-empty files once (the in-memory non-indexable set resets), which is
  harmless.
