# Workflow — what happens to a document when it is ingested

This is the end-to-end pipeline, framed as: *you tell an LLM (or yourself) to ingest some content — what happens to it.*

## The contract: the LLM's job ends at "raw + push"

The single most important rule: **the LLM does not convert, summarise, or index anything by hand.** It only lands the original into `goldberg-raw` and pushes. Everything downstream is automated.

### Stage 0 — Ingestion (the only manual part)

When told to ingest content, the agent:

1. Takes the raw content (uploaded file, email, URL, pasted text).
2. Determines provenance — source, date, party, case number, and handling flags (e.g. **CPIA s.17**, sensitivity, obtained-via-unofficial-channel).
3. Writes the **original, unmodified** file into `goldberg-raw/…` at the conventional path, plus a `metadata.yaml` capturing that provenance.
4. `git add` → commit (descriptive message) → **push**.
5. **Done.** The agent walks away; it does not wait for or perform indexing.

## What then happens automatically

```
goldberg-raw push
      │
      ▼
[1] TRIGGER  ── hook/watcher on the Halob clone → NATS event  goldberg.raw.ingested
      │        (payload: path, commit sha, mime type, metadata)
      ▼
[2] EXTRACT → markdown   (live-index service dispatches by type)
      │   PDF-text→pdftotext · PDF-scan→OCR(tesseract) · .eml→eml-to-md · docx→pandoc · url→readability · txt→passthrough
      ▼
[3] ENRICH   (reuse Mind of Steele llm_support)
      │   summary + keywords + entities → assemble markdown with YAML frontmatter
      ▼
[4] PERSIST → goldberg-extracted   (bot commit; mirrors the raw path; links back to source commit)
      │
      ├──▶ [5] INDEX → Elasticsearch (goldberg_files)   (MoS chunker + indexer; deterministic doc-id)
      │
      └──▶ [6] WIKI  → RAG sink (Ragie / Obsidian / vector store)
      ▼
[7] DONE → emit goldberg.indexed event / log; the document is now searchable + queryable
```

## Properties baked in

- **Idempotent / reprocessable** — deterministic doc-ids keyed on the raw file, so re-ingesting updates (not duplicates); a `--force` backfill re-runs stages 2–6 over all raw when the model/prompt changes.
- **Provenance end-to-end** — every extracted doc and ES record links back to the exact raw path + commit sha.
- **Raw is sacred** — extraction failures never touch `goldberg-raw`; the original is always preserved, and failures dead-letter to a log/NATS subject.
- **One-way** — `goldberg-extracted` is written *by* the pipeline and is **not** itself a trigger (no two-hop, no loops).

## Metadata

Each extracted document is a **markdown file with a YAML frontmatter header**
([ADR 0004](decisions/0004-metadata-representation.md)) — metadata in the prelude
(summary, keywords, entities, author, `matters`, attributed `claims`, provenance,
handling flags), extracted text in the body. Most fields are machine-derived at
enrichment; a light **folder-defaults** merge supplies the few genuinely
folder-uniform, human-set fields (the legal-handling flags, sometimes
`matters`/`parties`). Those same fields flow into the Elasticsearch document.

## Answering questions (the read side)

The pipeline above is the *write* side (raw → indexed). The *read* side is the
**query layer**: an agent (Claude Code) answers questions about the corpus by
running retrieval tools against the Elasticsearch index and synthesising an
**attributed, cited** answer — the tools retrieve, the agent answers.

```
question ──▶ goldberg claims / search / get / facets ──▶ Elasticsearch (goldberg_documents)
                                                              │
                                             grounded hits (with provenance)
                                                              ▼
                              agent synthesises an answer, citing doc_id + raw_path + speaker + date
```

`goldberg claims` (the nested attributed-claims query) answers "who said what about
whom" and surfaces contradictions; `goldberg search` is full-text; `goldberg get`
reads a document; `goldberg facets` orients. Full runbook:
[runbooks/querying-the-corpus.md](runbooks/querying-the-corpus.md).

## Open decisions

See [architecture.md](architecture.md#open-decisions) — wiki sink, trigger locus, large-binary handling.
