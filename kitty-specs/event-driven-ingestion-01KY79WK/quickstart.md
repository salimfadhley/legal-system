# Quickstart — Event-Driven Trigger-Based Ingestion

How to run, deploy, and verify the new ingest path.

## Prerequisites

- Shared infra already on Halob: Elasticsearch (`${GOLDBERG_ES_URL}`), NATS
  JetStream (`${NATS_URL}` = `nats://192.168.86.31:4222`), Docling (`docling:5001`).
- `uv sync` (adds `nats-py`, `tiktoken`).

## Local dev / test

```bash
# Unit tests (TDD — these are written first and must fail before implementation)
uv run pytest tests/unit -k "enrich_token or commit_files or processor or catchup"

# Opt-in end-to-end (real NATS/ES/Docling into isolated *_test stream+index)
GOLDBERG_INTEGRATION=1 uv run pytest tests/integration -k ingest
```

## Wire the trigger (once, on the goldberg-raw clone)

```bash
# Point goldberg-raw at the repo-committed hooks
git -C <goldberg-raw> config core.hooksPath hooks
# hooks/post-commit and hooks/post-merge call: goldberg publish-commit "$(git rev-parse HEAD)"
```

Verify a commit publishes an event:

```bash
git -C <goldberg-raw> commit --allow-empty -m "trigger test"
# -> a goldberg.raw.commit message appears on the GOLDBERG stream
```

## Run the processor

```bash
uv run goldberg ingest-serve --workers 2 --max-deliver 5 --health-port 8098
# startup: one bounded catch-up pass, then live event consumption
curl -s localhost:8098/health
```

## Deploy on Halob

1. Build/replace the compose `ingest` service (was `reconciler`) →
   `goldberg ingest-serve`, same volumes (`goldberg-raw:ro` incl. `.git`,
   writable `config/`), env `${GOLDBERG_ES_URL}` / `${NATS_URL}` / `docling:5001`.
2. `docker compose up -d ingest` (the `goldberg-reconciler` container is already
   stopped; remove it at cutover).

## Verify (acceptance)

```bash
# Backfill the two oversized OCR files left by the retired reconciler
uv run goldberg ingest catchup

# Completeness + health
uv run goldberg audit --manifest config/provenance-manifest.json   # expect 100% coverage
uv run goldberg status --yaml                                       # expect health: ok
uv run goldberg trace evidence/simon_goldberg/fsa_mortgage_dossier/ocr_output/combined.tsv
#   -> received/ok ... indexed/ok  (no more enriched/failed storm)

# No idle polling: with no commits, no new events accrue (no reconcile-heartbeat)
```

## Rollback

The corpus is derived (ADR 0006/0012): if the new path misbehaves, re-run
`goldberg ingest catchup` to converge, or temporarily restart the (still-built)
reconciler image until fixed. `goldberg-raw` + manifest remain the system of record.
