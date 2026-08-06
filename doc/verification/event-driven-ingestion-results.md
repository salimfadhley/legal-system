# Verification results — Event-Driven Trigger-Based Ingestion (live cutover)

**Mission:** event-driven-ingestion-01KY79WK
**Date:** 2026-07-23
**Environment:** Halob (192.168.86.31), live `goldberg_documents` index + NATS + Docling.

The polling reconciler was retired and replaced with the event-driven ingest service,
deployed live and verified end-to-end. This records the evidence.

## Deployment

- Container **`legal-ingest`** (image `legal-ingest:local`, built from the mission
  code) runs `legal_system ingest-serve` on **host network**, mounts: `goldberg-raw:ro`
  (incl. `.git`), the live writable `config/` (manifest) → `/app/config`,
  `deploy/projects.container.yaml` → `/etc/goldberg/projects.yaml:ro`. Env: `GOLDBERG_ES_URL`,
  `GOLDBERG_ES_INDEX=goldberg_documents`, `GOLDBERG_DOCLING_URL`, `NATS_URL`, `OPENAI_API_KEY`,
  and `GIT_CONFIG_*` (safe.directory — see follow-ups). `/health` on 8098.
- The old **`goldberg-reconciler`** container is **stopped** (Exited) and removed from the
  deploy topology (WP05). `legal_system watch` no longer exists.
- On startup the service runs ONE bounded catch-up, then consumes `goldberg.raw.commit`.

## SC-004 / FR-012 — oversized-document backfill (the storm files)

The two OCR files that caused the original `context_length_exceeded` DLQ storm are now
**indexed and enriched** (token-safe enrich on bounded text), with `raw_sha256` matching
the current file:

| File | Result |
|------|--------|
| `evidence/example_party/mortgage_dossier/ocr_output/combined.tsv` | ✅ indexed 2026-07-23T14:18:55, real summary + keywords, sha match |
| `evidence/example_party/complaint_pack/ocr_output/combined.tsv` | ✅ indexed 2026-07-23T14:19:30, real summary |

## SC-001 / FR-001..FR-006 — end-to-end trigger flow

Verified by trivially mutating `reports/` markdown files, committing them in
`goldberg-raw`, publishing the commit trigger (exactly what the `post-commit` hook does),
and confirming the running consumer flowed each through to a searchable, provenanced index:

| Commit | File | raw_commit on indexed doc | Marker searchable |
|--------|------|---------------------------|-------------------|
| `e570eb83` | `reports/INDEX.md` | ✅ `e570eb83…` | ✅ |
| `f4b6d068` | `reports/case_contacts_directory.md` | ✅ `f4b6d068…` | ✅ (restart-safe deploy) |

Flow proven: **commit → publish trigger → NATS JetStream → durable consumer →
git-resolve changed files → provenance-first (raw_commit) → extract → enrich → index →
searchable.** `dead_lettered=0` on the fixed path.

## Issues found during the live cutover (and their disposition)

1. **git "dubious ownership"** — the container (root) could not run `git` on the
   bind-mounted `goldberg-raw` (host-owned), so commit resolution raised
   `GitResolutionError` and the provenance `git log` was blocked. **Notably, the
   Codex-review FIX-1 worked exactly as intended**: the processor NAK'd (never
   silently acked an empty result), so no document was dropped — the old behaviour
   would have silently dropped it. Resolved by `safe.directory` (set via the
   container `GIT_CONFIG_*` env for the live container; **baked into
   `deploy/Dockerfile.ingest`** so the compose path is correct too).
2. **Catch-up vs healthcheck** — the health endpoint opens only AFTER the ~170s
   startup catch-up, so a short healthcheck start-period marked the container
   unhealthy mid-catch-up and `autoheal` (configured `LABEL=all`) restarted it into a
   loop. Resolved by a generous `--health-start-period=900s` (and baked into the
   Dockerfile). **Follow-up:** start the health server BEFORE the catch-up so the
   tolerance is unnecessary.

## Follow-ups (non-blocking)

- **Health-before-catch-up** (code): open `/health` before running the startup
  catch-up so a long catch-up never trips a supervisor.
- **Wire `core.hooksPath`** on the `goldberg-raw` clone(s) so real commits auto-fire
  the trigger (per `doc/runbooks/wiring-the-ingest-trigger.md`); requires `goldberg`
  resolvable in the committing environment.
- **Doc versioning**: a content change to a file produces a NEW `doc_id` (content-based)
  rather than superseding the prior version; the old version lingers in ES. Pre-existing
  doc_id behaviour, not introduced here — flag for a future dedup/versioning decision.
- **Pre-fix DLQ artifact**: the first e2e message (`4bc5faca`) dead-lettered during the
  pre-`safe.directory` window (its file was still indexed via catch-up, so nothing lost).
- **E2e marker commits** (`4bc5faca`, `e570eb83`, `f4b6d068`) appended harmless HTML-comment
  markers to three `reports/` files; revert if a clean corpus is preferred.

## Verdict

The event-driven pipeline is **live, healthy, and verified**: backfill complete, the
commit→index flow works with provenance, the DLQ is clean on the fixed path, and the
reconciler is decommissioned. Independent Codex live-check: see the session record.
