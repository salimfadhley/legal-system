# ADR 0001 — Wiki / RAG sink backend

**Status:** Accepted · **Date:** 2026-07-20 · **Mission:** M0 · **Unblocks:** M4 (sinks)

## Context

The pipeline's final stage indexes enriched documents into a searchable,
**attributed** knowledge layer that answers questions across the corpus and
grounds drafting. Two capabilities are first-class (per `doc/design.md`):

1. **Attributed, grounded Q&A** — every answer cites the source document, its raw
   commit, the speaker (`author`/`source_party`), and the date.
2. **Cross-corpus contradiction detection** — surfacing where a party's account
   shifts over time, which requires comparing **claims/assertions** across
   documents.

Decision drivers, in priority order: fidelity of attribution + provenance
(citations must map to the exact raw commit and speaker); ability to query at the
**claim** level across the corpus; reuse of Mind of Steele (MoS); what already
runs on Halob; on-network vs cloud egress; build effort; vendor lock-in.

Relevant facts: Halob already runs Elasticsearch with the legacy `goldberg_files`
index (~589 docs). MoS already ships an Elasticsearch chunker+indexer **and** a
Ragie uploader. Cloud LLM/Ragie is permitted (logged charter exception).

## Options considered

### A. RAG-on-Elasticsearch (build retrieval on the ES Halob already runs)
- **+** Full control of the document schema, so citations map exactly to our
  metadata (`raw_commit`, `author`/`source_party`, `matters`, `date`).
- **+** Claim-level fields + entities are queryable and **aggregatable across the
  corpus** — the mechanism contradiction detection needs.
- **+** Reuses MoS's ES chunker/indexer; the index already exists on Halob.
- **+** On-network: the retrieval index stays on Halob (simpler provenance; no
  need to export the whole corpus to a third party for the core path).
- **+** Hybrid BM25 + dense-vector (kNN) retrieval; embeddings via cloud OpenAI.
- **−** We build the retrieval + prompt-assembly + citation-formatting layer
  (more than plugging in a managed API).

### B. Ragie (managed RAG; MoS has an uploader)
- **+** Lowest build effort for natural-language Q&A; MoS uploader exists.
- **−** Managed chunking is a black box → weaker control over attribution and
  **provenance-to-commit** fidelity.
- **−** No native **cross-document claim comparison** — contradiction detection
  isn't expressible as queries.
- **−** Exports the entire corpus to a third party (permitted, but avoidable for
  the system of record); ongoing cost; vendor lock-in.

### C. Obsidian vault
- **+** Human-browsable local markdown; already a Halob candidate.
- **−** It is a **browse surface, not a retrieval/RAG engine** — it cannot serve
  attributed programmatic Q&A or claim/contradiction queries on its own.

## Decision

**Adopt RAG-on-Elasticsearch as the canonical wiki/RAG backend.** Retrieval,
attribution, and claim-level querying run against the Halob Elasticsearch index
(reusing MoS's indexer); answer generation uses cloud OpenAI/Anthropic over the
retrieved, attributed chunks.

- **Ragie remains pluggable behind the M4 sink interface** as an optional managed
  RAG alternative/augmentation — not the system of record.
- **Obsidian** is retained only as an optional human-browse export, not the
  retrieval backend.

This decision is **reversible**: M4 builds sinks behind an interface, so a later
switch or addition (e.g. Ragie) does not require rework of the pipeline core.

## Consequences

- M4 implements the Elasticsearch indexer (reuse MoS) **plus** a retrieval/query +
  citation layer as the canonical sink; the sink interface keeps Ragie pluggable.
- A sub-decision for M4: choose the embedding model (cloud OpenAI) for kNN.
- Halob Elasticsearch must be sized for the corpus plus vector fields.
- Slightly more build effort than a managed API — accepted in exchange for
  attribution/provenance fidelity and claim-level querying, which are first-class.

## Downstream

Unblocks **M4 (sinks)**: the Elasticsearch indexer + RAG retrieval is the
canonical sink; the wiki-sink interface keeps alternative backends pluggable.
