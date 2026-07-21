# ADR 0006 — Ingestion & provenance architecture (reconciling git-raw with Papra)

**Status:** Accepted (pending spike validation) · **Date:** 2026-07-21 · **Enables:** M8, and closes the §2 pipeline gaps

## Context

To get the pipeline working we went **Papra-first** (drop a file straight into
Papra → webhook → enrich → index). That works, but it left `goldberg-raw` (the
intended immutable git system-of-record, ADR 0003) unwired, so indexed documents
have **no real provenance** (`raw_path` is Papra's filename, no `raw_commit`) and
**no `matters`**. Before M8 migrates the whole corpus we must settle how the
git-raw model and the Papra-centric pipeline fit together — getting it wrong means
re-migrating.

Five questions (from the §2 review), with the research findings:

### Q1 — Entry point / system-of-record

**Decision: `goldberg-raw` (git) is the system-of-record and the source of
provenance; Papra is the extraction engine.** A document's authoritative identity
is its path + commit in `goldberg-raw`; Papra holds a working copy purely to run
Docling. The archive is already organised this way (folders per party/matter with
`metadata.yaml`), so `goldberg-raw` mirrors it.

### Q2 — Correlating a Papra document back to its git raw_path/raw_commit

**Decision: join on content SHA-256.** Papra content-addresses documents by
`original_sha256_hash`; the same hash is computable from the raw file in
`goldberg-raw`. A **manifest** built by walking `goldberg-raw`
(`sha256 → {raw_path, raw_commit, matters, party_role, document_type}`) is the
lookup: when the pipeline processes a Papra document it reads `original_sha256_hash`
and resolves real provenance + matters from the manifest. No dependence on Papra
custom-properties (SSRF-style friction avoided); stamping them back remains
optional for a human-visible cross-link.

### Q3 — Assigning `matters` (and party_role / document_type)

**Decision: from the folder-level `metadata.yaml` chain**, using the light
**folder-defaults merge** already built in M1 (`merge_folder_defaults`). The
archive already sets `case_number`/`party_role`/`document_type` per folder,
inherited down the tree (e.g. `evidence/` → `422500059892`,
`r_v_fanthom_and_deacon/` → `T20240030`). Migration reads each document's folder
chain; `case_number(s)` become `matters`. No LLM guessing for the structural
fields; the machine still fills summary/keywords/entities/author/claims.

### Q4 — Populating `goldberg-extracted`

**Decision: write the enriched frontmatter `.md` mirroring the raw path** (wire the
existing `ExtractedRepoWriter` into the pipeline sinks). `goldberg-extracted` is a
**regenerable** artifact store, so commit it in **batches** (or on demand), not
per-document; Elasticsearch remains the primary queryable store. This also gives a
human-browsable, provenance-stamped mirror.

### Q5 — Large-document enrichment

**Decision: full-context enrichment** — remove the 12k-char truncation and pass
the whole document (models are 128k-context; the 69k MG6C fits comfortably). MoS's
chunker is transcript-specific (SRT-by-time) and is **not** reused. Chunking for
retrieval granularity + embeddings is deferred to the vector-RAG stretch (M10).

## Decision (summary)

Migration and live ingestion both key off `goldberg-raw`:

```
goldberg-raw file  ──(sha256, raw_path, raw_commit, metadata.yaml→matters)──▶ manifest
       │  (push a working copy into Papra for extraction)
       ▼
     Papra (Docling extract)  ──webhook / batch──▶  pipeline
                                                      │  join manifest by original_sha256_hash
                                                      ▼  real provenance + matters
                             enrich (full-context) → sinks: ES indexer + extracted writer
```

Plus two implementation items that fold in:
- **`.eml` handling:** the pipeline fetches the original bytes from Papra
  (`GET …/documents/:id/file`) and runs our own `eml_to_markdown` when Papra
  returns no content (mime `message/rfc822`).
- **Wire the `ExtractedRepoWriter`** into `Processor`/backfill sinks.

## Consequences

- M8 becomes: (1) migrate the archive into `goldberg-raw` (git, folder structure +
  `metadata.yaml` preserved; git-LFS per ADR 0002); (2) build the manifest;
  (3) push/reconcile into Papra; (4) run the pipeline with real provenance/matters;
  (5) reindex, verifying coverage vs the legacy 589.
- Needs: a manifest builder (`goldberg-raw` walk → sha256/provenance/matters),
  a `PapraClient.get_file` (original bytes), the mime-dispatch for `.eml`, the
  full-context enricher change, and the extracted-writer wiring.
- Idempotency preserved (deterministic doc-id keyed on raw file).

## Spike (validate before M8)

On ~5 real archive documents (spanning a couple of matters, incl. one `.eml` and
one large PDF): stage them in a `goldberg-raw`-shaped tree with their
`metadata.yaml`; build the manifest; run the flow; assert the indexed docs carry
**correct `raw_path`, `raw_commit`, and `matters`**, the `.eml` produces content,
and the large doc enriches without truncation. If the SHA-256 join or the
metadata-chain resolution misbehaves, revise here — not during the full migration.
