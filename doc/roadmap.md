# Mission roadmap

The build order for taking the pipeline in [design.md](./design.md) /
[workflow.md](./workflow.md) live. Each mission below is scoped to become one
**Spec Kitty mission** (`/spec-kitty.specify` → plan → tasks → implement → review
→ merge). TDD is mandatory throughout; nothing here is implemented yet.

**Build order (reviewed + locked 2026-07-20): foundations-first.** M1–M7 build the
complete, robust pipeline before M8 migrates the corpus in — chosen over a
time-to-value "walking skeleton" for rigour. Migration (M8) stays last.

## Sequencing

```
M0 ─▶ M1 ─┬─▶ M2 ─┐
          ├─▶ M3 ─┤
          ├─▶ M4 ─┼─▶ M5 ─▶ M7 ─▶ M8
          └─▶ M6 ─┘
```

M2, M3, M4, M6 are largely parallel once M1 lands. M5 integrates them; M7 deploys;
M8 migrates the corpus in.

---

## M0 — Research spike: resolve open decisions

- **Goal:** settle the remaining open decisions with written recommendations
  (ADRs) so downstream missions aren't blocked.
- **Scope:** (1) **wiki sink** — Ragie (managed RAG, cloud permitted) vs Obsidian
  vault vs RAG-on-Elasticsearch; (2) **large-binary handling** in goldberg-raw —
  plain git vs git-LFS.
- **Already resolved (record, don't re-litigate):** trigger locus = Halob-local
  filesystem watcher; data boundary = cloud LLM/Ragie permitted (logged exception).
- **Deliverable:** `doc/decisions/` ADRs with a recommendation per decision —
  [ADR 0001 wiki/RAG sink](./decisions/0001-wiki-rag-sink-backend.md) →
  RAG-on-Elasticsearch (Ragie pluggable); [ADR 0002 large binaries](./decisions/0002-large-binary-handling.md)
  → git-LFS (selective).
- **Process:** **lightweight** — a short research spike producing the two ADRs,
  *not* the full spec→plan→tasks→implement→review ceremony.
- **Acceptance:** each open decision has a chosen option + rationale; no code.

## M1 — Foundations & contracts

**Status: ✅ Delivered (2026-07-20)** — `src/goldberg_system/{metadata,events,sinks,identity,enrichment}`; 47 tests; ruff/black/mypy clean. MoS resolution recorded in [`doc/reuse/mind_of_steele.md`](./reuse/mind_of_steele.md).

- **Goal:** the shared library everything else builds on. Pure code, tested
  against mocks — no external services required.
- **Deliverables:**
  - **Metadata schema** (pydantic) — port goldberg-meta (`document_type`,
    `party_role`, `parties`, `keywords`, `date`, `topic`, `summary`, `skip`,
    `files`, directory-inheritance semantics) **plus** `author`/`source_party`,
    `matters` (list) + `primary_matter`, `origin`/`role`, `entities`,
    `raw_path`/`raw_commit`, `source_channel`, `disclosure_status`, `cpia_s17`,
    `privileged`, `sensitivity`, `relates_to`. Two-tier population (machine vs
    human) with **safe defaults** for legal-handling flags.
  - **NATS event contracts** — `goldberg.raw.ingested` and `goldberg.indexed`
    payloads (path, commit sha, mime, metadata).
  - **Sink interface** — the abstraction M4's writers implement.
  - **Deterministic doc-id** scheme + **content-hash staleness** check.
  - **Mind of Steele reuse resolution** — resolve MoS as a git source / vendored
    package (it lives on the Mac, not Halob).
  - **Papra cross-store mapping** ([ADR 0003](./decisions/0003-document-management-papra-integration.md)):
    schema carries a Papra `documentId` ↔ `raw_path`/`raw_commit` mapping so the
    git-raw ↔ Papra link is first-class.
- **Depends on:** M0 (for interface choices).
- **Acceptance:** schema round-trips + inheritance rules covered by tests;
  event/id/staleness logic tested; MoS importable.

## M2 — Extraction (wire Papra + Docling; build only the gaps)

**Status: ✅ Mostly delivered (2026-07-20).** Docling deployed on Halob and Papra
pointed at it (validated: scanned PDFs that were OCR-garbage now extract cleanly);
`src/goldberg_system/{extract,papra}` built (eml→markdown, passthrough, Papra
ingest-folder writer, Papra REST client) — 64 tests, gates clean. **Remaining:**
provision a Papra API key for the live REST path, and the `document:created`
webhook→NATS bridge (belongs with M6/M5).

*Rescoped by [ADR 0003](./decisions/0003-document-management-papra-integration.md):
the existing Papra deployment (backed by self-hosted Docling) handles OCR / PDF /
docx / layout+tables, so we no longer hand-build those extractors.*

- **Goal:** get raw files to layout-aware markdown, reusing Papra + Docling for the
  heavy lifting; build only what Papra can't do.
- **Offloaded to Papra + Docling:** `pdftotext`, `tesseract` OCR, `pandoc` (docx),
  and table/layout extraction.
- **Still build (Papra gaps):** eml-to-md (**recurse into `attachments/`**),
  readability (url), passthrough (txt/md).
- **New adapters:** a **Papra API client** (push original into the `legal` org;
  `GET …/documents/:id` for `content` + metadata; stamp `raw_path`/`raw_commit` as
  custom-properties); a **`document:created` webhook → NATS bridge**.
- **Depends on:** M0 (ADR 0003), M1. **Pre-req:** verify Docling's MIT license +
  Papra external-extraction integration before starting.
- **Acceptance:** for a sample leaf, the original lands in git-raw and Papra, Papra
  returns Docling-extracted markdown via the API, and provenance is cross-stamped;
  eml/passthrough covered by fixture tests; failures dead-letter without touching
  raw.

## M3 — Enrich (claim-aware)

**Status: ✅ Delivered (2026-07-21).** Metadata representation pivoted to
**markdown + YAML frontmatter** ([ADR 0004](./decisions/0004-metadata-representation.md)):
`metadata/frontmatter.py` (serialise/parse), `metadata/defaults.py` (light
folder-defaults, replacing the demoted goldberg-meta inheritance), schema extended
with `long_summary`/`claims`, `enrichment/assemble.py` (merge enrichment →
frontmatter doc). Concrete `OpenAIEnricher` + `secrets.py` (reuses the MoS OpenAI
key) produce attributed summary/keywords/entities/author/claims. **Verified live
end-to-end** (Papra content → enrich → frontmatter doc, real attributed claims).
80 tests, gates clean.

- **Goal:** turn extracted markdown into enriched, attributed markdown.
- **Scope:** reuse MoS `llm_support` (OpenAI) to produce summary, keywords,
  entities, **`author`/`source_party`**, and **attributed assertions** in a
  comparable form — powering both attributed Q&A *and* contradiction detection.
  Assemble markdown with YAML frontmatter matching the M1 schema.
  *(Per [ADR 0003](./decisions/0003-document-management-papra-integration.md), the
  extracted-markdown input now arrives from Papra's `content` field; enrichment
  itself is unchanged.)*
- **Depends on:** M1, MoS resolved.
- **Acceptance:** enrichment output validates against the schema; claim extraction
  covered by fixture tests (mocked LLM).

## M4 — Sinks

**Status: 🟡 Mostly delivered (2026-07-21).** `ElasticsearchIndexer` (new
`goldberg_documents` index; mapping matches the frontmatter schema — claims
**nested**, matters/author/entities keyword, content/summary text, provenance +
handling flags; idempotent by deterministic doc-id) and `ExtractedRepoWriter`
built behind the M1 `Sink` interface. **Verified live** on Halob ES: full pipeline
(Papra → enrich → index) then a nested claims query (`asserted_by`) returns the
doc. 91 tests, gates clean. **Remaining:** the RAG sink / query layer (RAG-on-ES +
optional dense-vector) and reindexing the 589 legacy `goldberg_files` docs (M8).

- **Goal:** persist + index the enriched document, behind the M1 sink interface.
- **Scope:** (1) **goldberg-extracted writer** — mirror the raw path, link the raw
  commit; (2) **Elasticsearch indexer** (MoS chunker/indexer) — index content +
  attribution/claim/matter fields; (3) **wiki/RAG sink** — backend per M0.
- **Depends on:** M1 (interface), M0 (wiki backend).
- **Acceptance:** each sink tested against a local/mocked backend; idempotent
  writes (deterministic doc-id) verified.

## M5 — live-index service

**Status: ✅ Delivered (2026-07-21), webhook-driven per [ADR 0005](./decisions/0005-live-service-webhook-driven.md).**
`service/` = `Processor` (poll Papra for content → enrich → index) + a stdlib HTTP
app (`POST /webhooks/papra`, `GET /health`). NATS deferred; the Papra webhook is
the trigger. Live-verified end to end.

- **Goal:** the orchestration loop that wires it all together.
- **Scope:** consume `goldberg.raw.ingested`; run the correct ingest path —
  **evidence pipeline** (extract→enrich→persist→index→wiki) or **input passthrough**
  (enrich→index for reports/analysis); enforce idempotency + staleness; dead-letter
  on failure; emit `goldberg.indexed`. Modelled on MoS `live_summarize/main.py`.
- **Depends on:** M1–M4.
- **Acceptance:** end-to-end test over a sample leaf through both paths;
  re-processing updates (not duplicates); failures dead-letter without touching raw.

## M6 — Trigger (Papra webhook)

**Status: ✅ Delivered (2026-07-21), per [ADR 0005](./decisions/0005-live-service-webhook-driven.md).**
Registered a Papra **`document:created` webhook** → the live-index service
(Papra's `WEBHOOK_URL_ALLOWED_HOSTNAMES` extended to permit the private-IP target).
Papra fires it *before* Docling finishes, so the service polls for content. (The
Halob filesystem-watcher framing below is superseded by the Papra webhook.)

- **Goal:** detect raw changes on Halob and publish `goldberg.raw.ingested`.
- **Scope:** filesystem watcher on the goldberg-raw (and input) tree; **coalesce to
  leaf-directory granularity + debounce** so a multi-file document save fires one
  job after writes settle. Also pushes the original into Papra's `legal` org.
  *(Per [ADR 0003](./decisions/0003-document-management-papra-integration.md), a
  second trigger source exists — Papra's `document:created` webhook bridged to
  `goldberg.raw.ingested` — which may be preferred for the Papra-mediated path.)*
- **Depends on:** M1 (event contract).
- **Acceptance:** simulated multi-file save produces exactly one event per leaf.

## M7 — Deploy

**Status: ✅ Delivered (2026-07-21).** `Dockerfile` (python:3.12-slim) → container
`goldberg-live-index` on Halob (port 8099, `--restart unless-stopped`, env from
`/share/Docker/goldberg-live-index/.env`). **Verified restart-safe + fully
automatic**: dropped a file → indexed in ~20s, zero manual steps. Runbook:
[runbooks/live-index-service.md](./runbooks/live-index-service.md).

- **Goal:** run `live-index` + watcher on Halob.
- **Scope:** docker-compose service (à la MoS); wire Elasticsearch, NATS, and
  OpenAI/Ragie config (MoS `env.yaml` pattern).
- **Depends on:** M5, M6.
- **Acceptance:** service comes up on Halob, processes a dropped test document end
  to end, and is restart-safe.

## M8 — Migration & backfill

- **Goal:** move off the frozen archive and populate the new repos.
- **Scope:** from `the_goldberg_files` — originals → **goldberg-raw**
  (evidence/exhibits); inputs (reports/analysis) via the passthrough path;
  authored outputs (briefings/filings) → **goldberg-casework**; regenerate
  extractions → **goldberg-extracted**. `--force` backfill over all raw; verify ES
  coverage against the legacy ~589 docs. Apply the M0 large-binary decision.
- **Depends on:** M7.
- **Acceptance:** archive content landed in the right repos with provenance intact;
  ES coverage meets/exceeds the legacy baseline; the archive is left untouched.

## Future / deferred missions (to analyse later)

### M9 — Composite raw-data decomposition (deferred)

- **Problem:** a single raw file often contains **multiple logical documents** —
  an `.eml` with many attachments (each attachment is arguably its own exhibit), a
  PDF that is a print-out of several separate email messages, a scanned bundle of
  distinct documents. One raw file → several extracted documents.
- **Scope (to be analysed):** detect document boundaries within a composite;
  split into constituent documents; give each its own doc-id + frontmatter while
  preserving provenance back to the parent raw file (and the position within it);
  decide how attachments relate to their carrier email.
- **Status:** deferred — analyse and spec later. Note: the M2 eml extractor already
  produces an *attachment manifest* (a foundation this can build on).

### M10 — Dense-vector semantic RAG (stretch goal)

- **Idea:** add a `dense_vector` field to the ES index and generate embeddings at
  index time (cloud OpenAI per ADR 0001) to enable **semantic** retrieval (kNN)
  alongside the current BM25 + structured/claims search — i.e. find relevant
  passages by *meaning*, not just keyword overlap, and hybrid-rank the two.
- **Status:** **stretch / uncertain value.** The current BM25 + attributed-claims
  querying already answers the core "who said what" questions well; the added
  benefit of embeddings (and their cost/complexity — chunking, an embedding model,
  vector storage) is not yet clear. Analyse whether it materially improves answers
  before committing.
