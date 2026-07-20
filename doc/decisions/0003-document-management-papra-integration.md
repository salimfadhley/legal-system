# ADR 0003 — Document management: integrate the existing Papra (Papra + Docling)

**Status:** Accepted · **Date:** 2026-07-20 · **Mission:** research spike · **Reshapes:** M2, M3, M6, M1

## Context

The pipeline's "opening stages" (ingest raw → OCR/extract to markdown → track) were
scoped as bespoke work in **M2 (extractors)**. Before building six extractors, we
asked whether existing document-management software already automates this.

It turns out **one is already deployed**: **Papra** (papra.app, AGPL, single
container: Node + Tesseract + SQLite FTS5) runs on Halob via the Mind of Steele
project, **with a dedicated `legal` org** created for this case. See
`~/workspace/mind_of_steele/doc/applications/papra/`.

A research spike (2026-07-20) verified Papra against its own docs + source; other
tools (Paperless-ngx, Docspell, Docling, Marker, unstructured, Tika, LlamaParse,
managed cloud APIs) were surveyed but **not** re-verified against current primary
sources — treated as directional only. Full report retained in the mission notes.

### What Papra's REST API actually exposes (verified)

- **Extracted text + metadata:** `GET /organizations/:orgId/documents/:id` returns
  the document JSON **including the OCR'd `content` field**, plus `originalSha256Hash`,
  name, mime, dates, tags. (The serializer strips only encryption/storage-key
  fields — not `content`.)
- **List / full-text search:** `GET …/documents?searchQuery=…` (SQLite FTS5).
  *Caveat: the list projection may not inline full `content`; use the single-doc
  GET or the webhook payload for reliable text.*
- **Webhook on new document:** per-org **`document:created`** (Standard Webhooks,
  HMAC-SHA256), payload carries the full document incl. `content`.
- **Upload:** `POST …/documents` (multipart) + batch; **original retrievable
  byte-for-byte** (`…/documents/:id/file`; content-addressed, immutable store).
- **Tags + custom-properties:** per-document CRUD — where we stamp provenance.
- **Auth:** API keys, `Authorization: Bearer`, scoped. Official TS SDK.
- **Pluggable extraction:** `CONTENT_EXTRACTION_STRATEGY` can call an **external
  Docling** (or Azure/Mistral) backend instead of the bundled flat-text Tesseract.

### Gaps (Papra can't do these)

- **`.eml` + attachments** — not parsed to text by Papra's bundled engine.
- **docx / layout / tables** — bundled engine is flat text; needs external Docling.
- **audio/video** — unsupported (expected).

## Options considered

- **(a) Papra as sole system-of-record.** Rejected: Papra has **no git-commit
  concept** (it content-addresses by SHA-256), so it cannot carry our provenance
  triple (`raw_path` + `raw_commit`). Letting it own the originals severs the
  commit linkage the court output depends on.
- **(b) Papra purely parallel** to a hand-built extractor set. Rejected: wastes the
  OCR we already run and still forces us to build the extractors M2 was scoped for.
- **(c) Hybrid — git-raw system-of-record + Papra as ingest/OCR/extraction
  front-end (backed by Docling), pipeline pulls extracted markdown via the API.**
  **Chosen.**
- **Bundled Tesseract vs Docling:** Papra's flat-text Tesseract is inadequate for
  legal PDFs (no tables/reading order/docx). **Use a self-hosted Docling backend**
  behind Papra (MIT, on-network, layout/table-aware).
- **Full-DMS alternatives** (Paperless-ngx et al.): not adopted — MoS already chose
  Papra for this homelab; Paperless would only win on ML classification/scanner
  workflow, which we don't need.

## Decision

**Adopt option (c).** Keep **`goldberg-raw` (git)** as the immutable
system-of-record and provenance anchor. Use the **already-deployed Papra `legal`
org** as the ingest + OCR/extraction front-end, configured to extract via a
**self-hosted Docling** server on Halob. The pipeline pulls extracted markdown via
Papra's API, then runs the **unchanged** claim-aware enrich → `goldberg-extracted`
→ Elasticsearch → RAG stages.

### Flow

1. Original lands in **`goldberg-raw`** (git) → authoritative; commit yields
   `raw_path` + `raw_commit`.
2. The Halob watcher (M6) also pushes the file into Papra's `legal` org (`POST
   …/documents` or the `ingest/<org-id>/` drop folder — note the org-id subfolder
   mapping is required).
3. Papra extracts **via Docling** → layout/table-aware markdown in `content`.
4. Pipeline consumes the **`document:created` webhook** (bridged to
   `goldberg.raw.ingested` on NATS) and `GET …/documents/:id` for `content` +
   metadata.
5. Pipeline **stamps `raw_path` + `raw_commit` back onto the Papra doc as
   custom-properties**, cross-linking the two stores.
6. Continue unchanged: enrich (MoS `llm_support`) → extracted → ES → RAG.

### Provenance

git-raw is never delegated; the canonical provenance triple lives in our metadata
layer. Papra holds a *second* immutable copy — reconciled via (a) custom-properties
`raw_path`/`raw_commit`, and (b) matching the git blob hash to Papra's
`originalSha256Hash` to detect drift. Idempotency/staleness keys off the git raw
file, not Papra.

## Consequences

- **M2 shrinks** — see roadmap. Docling-via-Papra replaces pdftotext / Tesseract /
  pandoc; we keep only **eml-to-md (+attachments)**, readability, passthrough, and
  add a **Papra API client** + a **`document:created` → NATS bridge**.
- **M3 unchanged** — enrichment input now arrives from Papra's `content` field.
- **M6** gains a second trigger source (Papra webhook) alongside the filesystem
  watcher; the watcher additionally pushes into Papra.
- **M1** adds a Papra `documentId` ↔ `raw_path`/`raw_commit` mapping so the
  cross-store link is first-class in the schema.
- **Audio/video** stays with Mind of Steele's Whisper, feeding transcripts in (the
  existing MoS `wiki-ingest` pattern).
- **Residual tension (logged):** two immutable copies (git-raw + Papra) double
  storage for large media. Mitigate: only push OCR/search-useful docs into Papra;
  keep large A/V in git-raw + MoS transcripts.
- **Before committing M2, verify:** Docling's current MIT license + Papra-backend
  integration steps; and Marker/unstructured licensing if considered as fallbacks.

## Downstream

Reshapes **M2** (extractors → "wire Papra + Docling"), and touches **M3**, **M6**,
and **M1**. Ragie remains only an optional downstream RAG sink (ADR 0001), not an
extractor.
