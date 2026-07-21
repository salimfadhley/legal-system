# ADR 0007 — Concept-wiki output: SilverBullet LLM-wiki generated from the enriched corpus

**Status:** Proposed (spec for mission M11) · **Date:** 2026-07-21

## Context

The corpus is queryable (BM25 + attributed claims over Elasticsearch), which answers
*targeted* questions well. What it does **not** give is a **"by concept" index** —
a browsable, human-curated map of the people, organisations, legal concepts, and
contradictions across the whole case. That is the missing *output* the user wants:
a knowledge base you read and navigate, not just query.

This is exactly the **"LLM wiki"** pattern (Karpathy; formalised in Nous Research's
`llm-wiki` skill): *compile knowledge once and keep it current* — the opposite of
stateless RAG. The human curates sources and directs; the agent summarises,
cross-references, files, and lints.

Crucially, **the machinery already exists** in Mind of Steele and a Goldberg space
is already deployed:

- **`silverbullet-goldberg`** — a live SilverBullet space for `the_goldberg_files`
  (http://192.168.86.31:3100/, data at `/Volumes/Home/silverbullet/the_goldberg_files/`),
  already scaffolded: `SCHEMA.md` (closed tag taxonomy: party-role / phase /
  doc-type / claim-status), `index.md`, and the Layer-2 folders `entities/`,
  `concepts/`, `comparisons/`, `queries/`. Current fill: 5 entities, 1 concept, 0
  comparisons, 97 filed queries — i.e. the **synthesised concept/entity layer is
  the gap**.
- **The SilverBullet ES indexer/linter** (`mind_of_steele/doc/applications/silverbullet/indexer/`)
  — watchdog live-indexes each space into a `silverbullet-<space>` ES index and runs
  a daily lint (broken links, orphans, stale pages, tag drift).
- **The auto-ingest author** (`src/mind_of_steele/wiki_ingest/`) — orient → LLM
  authors strict-JSON page operations → local hard-gate validation → apply
  (DRY_RUN proposals by default, live after review).

## Decisions

1. **Adopt the SilverBullet LLM-wiki as the concept-index output; reuse, don't
   rebuild.** Use the existing `silverbullet-goldberg` space and the MoS
   indexer/linter/author machinery. Per [[shared-infra-docs]], any changes to that
   shared machinery are documented in Mind of Steele, not here.

2. **The wiki is strictly downstream of enrichment — a peer sink of the pipeline
   (user, 2026-07-21).** It is built from the *enhanced, extracted data*, never from
   raw and never re-extracting. Architecturally the **wiki author is another `Sink`**
   (alongside `ElasticsearchIndexer` and `ExtractedRepoWriter`): it consumes the same
   `EnrichedDocument` — `entities`, attributed `claims`
   (subject/predicate/object/**asserted_by**), `author`, `summary`, `matters`,
   `raw_path` — and authors/updates Layer-2 pages from it. This is the key difference
   from the MoS listener (whose seed is raw Pinchflat transcripts): **our pipeline has
   already done the extraction**, so the wiki consumes structured signal, not text to
   re-analyse.

   ```
   raw → Papra/Docling → enrich (entities/claims/summary) ─┬─▶ ElasticsearchIndexer (query)
                                                            ├─▶ ExtractedRepoWriter  (markdown mirror)
                                                            └─▶ WikiAuthorSink       (concept index)  ← NEW
   ```

3. **Attributed claims → `comparison` pages for contradictions.** The legal payoff.
   Where two documents assert conflicting objects for the same subject/predicate
   (e.g. the identity of the prosecuting entity), author a `comparison` page citing
   both, `asserted_by` each source, with `contradictions:` frontmatter. This turns
   the claims layer into navigable argument material.

4. **Trigger: batch first, then event-driven.** Phase 1 is a batch author over the
   already-indexed corpus (bounded, reviewable). Phase 2 hooks the live pipeline
   (the M5 service) to author/update pages as new documents are indexed — reusing
   the MoS listener's orient→author→validate→apply loop.

5. **Guardrails (inherited from the MoS pattern, non-negotiable for legal work):**
   DRY_RUN proposals by default (written to `queries/_auto_ingest/`), reviewed before
   going live; tags ⊆ `SCHEMA.md`; ≥2 outbound `[[wikilinks]]`; frontmatter required;
   never silently overwrite a claim (record both + flag). The human curates.

6. **The wiki is a derived OUTPUT, regenerable — not evidence.** Pages cite
   `sources: [raw_path]` + `doc_id` for every assertion (mandatory, per the casework
   discipline). It is not fed back into `goldberg-raw`. It *is* indexed
   (`silverbullet-goldberg` ES index) so it is itself searchable.

7. **Cross-link the `mind_of_steele` wiki.** Goldberg appears in both (litigation
   party here; conspiracy figure there); link via external markdown/`cross_wiki`
   frontmatter, per the existing `SCHEMA.md`.

## Consequences / open questions for M11 to resolve

- **Author component location.** The MoS `wiki_ingest` seeds from transcripts; we
  need a Goldberg variant seeding from ES documents (entities/claims). Decide:
  extend `wiki_ingest` (shared, in MoS) vs a thin new author in goldberg-system that
  reuses its validation/apply core. Leaning: reuse the validation/apply core, new
  corpus-specific "orient + author" front.
- **SCHEMA taxonomy fit.** The existing tag vocabulary is generic-litigation; extend
  it for this case's matters (422500059892 / 422500059914 / 648MC011 / L00SS179) and
  domain (PHA 1997 harassment, private prosecution, disclosure/CPIA).
- **De-dup vs the corpus.** Entities already exist as ES `entities`; the wiki page is
  the *synthesised* view. Page-creation threshold (2+ sources) still applies.
- **Update cadence & idempotency.** Re-authoring on every re-index must converge
  (update-in-place, content-hash skip) — mirror the indexer's `content_hash` gate.

## Mission M11 — phases

1. **Verify infra + fit taxonomy** — confirm the `silverbullet-goldberg` indexer is
   running; extend `SCHEMA.md` for this case; orient on the 97 existing queries.
2. **Batch author (DRY_RUN)** — author `entities/` + `concepts/` proposals from the
   indexed corpus; review; apply the good ones. Build `index.md`.
3. **Contradiction pass** — author `comparison/` pages from conflicting attributed
   claims; the argument-grade output.
4. **Event-driven auto-update** — hook the live pipeline; DRY_RUN then live, with the
   MoS guardrails. Keep the wiki current as the corpus grows (esp. after full M8).
