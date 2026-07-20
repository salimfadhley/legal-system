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
- **Depends on:** M0 (for interface choices).
- **Acceptance:** schema round-trips + inheritance rules covered by tests;
  event/id/staleness logic tested; MoS importable.

## M2 — Extractors

- **Goal:** raw file → markdown, dispatched by mime type.
- **Scope:** `pdftotext` (text PDF) · `tesseract` OCR (scanned PDF/image) ·
  eml-to-md (**recurse into `attachments/`**) · `pandoc` (docx) · readability
  (url) · passthrough (txt/md). Leaf-directory aware.
- **Depends on:** M1.
- **Acceptance:** table-driven tests with fixtures per mime type; failures raise
  cleanly (feed the dead-letter path later).

## M3 — Enrich (claim-aware)

- **Goal:** turn extracted markdown into enriched, attributed markdown.
- **Scope:** reuse MoS `llm_support` (OpenAI) to produce summary, keywords,
  entities, **`author`/`source_party`**, and **attributed assertions** in a
  comparable form — powering both attributed Q&A *and* contradiction detection.
  Assemble markdown with YAML frontmatter matching the M1 schema.
- **Depends on:** M1, MoS resolved.
- **Acceptance:** enrichment output validates against the schema; claim extraction
  covered by fixture tests (mocked LLM).

## M4 — Sinks

- **Goal:** persist + index the enriched document, behind the M1 sink interface.
- **Scope:** (1) **goldberg-extracted writer** — mirror the raw path, link the raw
  commit; (2) **Elasticsearch indexer** (MoS chunker/indexer) — index content +
  attribution/claim/matter fields; (3) **wiki/RAG sink** — backend per M0.
- **Depends on:** M1 (interface), M0 (wiki backend).
- **Acceptance:** each sink tested against a local/mocked backend; idempotent
  writes (deterministic doc-id) verified.

## M5 — live-index service

- **Goal:** the orchestration loop that wires it all together.
- **Scope:** consume `goldberg.raw.ingested`; run the correct ingest path —
  **evidence pipeline** (extract→enrich→persist→index→wiki) or **input passthrough**
  (enrich→index for reports/analysis); enforce idempotency + staleness; dead-letter
  on failure; emit `goldberg.indexed`. Modelled on MoS `live_summarize/main.py`.
- **Depends on:** M1–M4.
- **Acceptance:** end-to-end test over a sample leaf through both paths;
  re-processing updates (not duplicates); failures dead-letter without touching raw.

## M6 — Trigger (Halob filesystem watcher)

- **Goal:** detect raw changes on Halob and publish `goldberg.raw.ingested`.
- **Scope:** filesystem watcher on the goldberg-raw (and input) tree; **coalesce to
  leaf-directory granularity + debounce** so a multi-file document save fires one
  job after writes settle.
- **Depends on:** M1 (event contract).
- **Acceptance:** simulated multi-file save produces exactly one event per leaf.

## M7 — Deploy

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
