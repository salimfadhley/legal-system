# Workflow — what happens to a document when it is ingested

This is the pipeline framed from the **document author's** point of view: *you tell an
LLM (or yourself) to ingest some content — what happens to it, and what you are
responsible for.*

For the full technical description — component contracts, external dependencies, and the
reasoning behind each choice — see [`architecture.md`](./architecture.md).

## The contract: the LLM's job ends at "raw + push"

The single most important rule: **the LLM does not convert, summarise, or index anything by hand.** It only lands the original into `goldberg-raw` and pushes. Everything downstream is automated.

### Stage 0 — Ingestion (the only manual part)

When told to ingest content, the agent:

1. Takes the raw content (uploaded file, email, URL, pasted text).
2. Determines provenance — source, date, party, case number, and handling flags (e.g. **CPIA s.17**, sensitivity, obtained-via-unofficial-channel).
3. Writes the **original, unmodified** file into `goldberg-raw/…` at the conventional path, plus a folder-level `metadata.yaml` capturing that provenance.
4. `git add` → commit (descriptive message).
5. **Done.** The agent walks away; it does not wait for or perform indexing.

The file must land in a tree named in [`config/evidence-allowlist.yaml`](../config/evidence-allowlist.yaml) — anything outside those trees is deliberately ignored.

## What then happens automatically

```
git commit in goldberg-raw
      │
      ▼
[1] TRIGGER  ── post-commit / post-merge hook → `goldberg publish-commit <sha>`
      │        → NATS JetStream, subject  goldberg.raw.commit  (payload: {sha, ts, source})
      │        The hook NEVER fails git: a broker outage costs a trigger, not a commit.
      ▼
[2] CONSUME  ── durable pull consumer `ingest-processor` (survives service downtime)
      │
      ▼
[3] PROVENANCE FIRST ── register sha256 → {raw_path, raw_commit, matters} in the manifest
      │                 BEFORE anything is indexed. No provenance, no index entry.
      ▼
[4] RESOLVE  ── `git` names exactly which allowlisted files this commit changed
      ▼
[5] EXTRACT → markdown   (self-hosted Docling, async submit/poll; text & .csv/.json pass through)
      ▼
[6] ENRICH   (OpenAI gpt-4o-mini → summary, entities, author, attributed CLAIMS)
      ▼
[7] SINKS    ├──▶ Elasticsearch `goldberg_documents`  (deterministic doc-id; claims nested)
             ├──▶ goldberg-extracted  (markdown + YAML frontmatter, mirroring the raw path)
             └──▶ concept wiki  (renderer built; automatic synthesis not yet wired)
      ▼
[8] DONE → the document is searchable, quotable and attributed.
           Every stage emitted an event to `goldberg_pipeline_events`.
           Ack only when every file is terminal; otherwise retry, then dead-letter.
```

## Properties baked in

- **Idempotent / reprocessable** — deterministic doc-ids keyed on the raw file, so re-ingesting updates (not duplicates); a redelivered or replayed commit re-ingests nothing new.
- **Provenance end-to-end** — every extracted doc and ES record links back to the exact raw path + commit sha + content hash, and provenance is recorded *before* indexing.
- **Raw is sacred** — the pipeline never writes to `goldberg-raw`; extraction failures dead-letter and the original is always preserved.
- **One-way** — `goldberg-extracted` is written *by* the pipeline and is **not** itself a trigger (no two-hop, no loops).
- **Nothing is silently dropped** — a commit that cannot be resolved is retried, never acked as "nothing to do"; a backlog the startup catch-up could not reach is reported as degraded health; `goldberg audit` proves expected-vs-actual.
- **Degrades, doesn't die** — if Docling is down, text files still ingest and OCR files retry; if the enricher is down, one document dead-letters, not the service.

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

## Where to read further

- [architecture.md](./architecture.md) — the canonical technical description: components,
  contracts, external dependencies, and the reasoning behind every significant choice.
- [runbooks/wiring-the-ingest-trigger.md](./runbooks/wiring-the-ingest-trigger.md) — how a
  `goldberg-raw` clone is wired to fire the trigger (`core.hooksPath`).
- [runbooks/verifying-the-system-is-up.md](./runbooks/verifying-the-system-is-up.md) — the
  operator's acceptance procedure.
