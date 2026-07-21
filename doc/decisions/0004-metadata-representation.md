# ADR 0004 — Metadata representation: markdown + YAML frontmatter

**Status:** Accepted · **Date:** 2026-07-21 · **Reshapes:** M1 (schema), M3 (enrich)

## Context

M1 ported the legacy **goldberg-meta** system: metadata in separate
`metadata.yaml` files with directory inheritance (locked / overridable /
non-inherited / irreversible semantics). Revisiting this before building M3: how
much do we actually benefit from that tool for the *extracted* collection?

Key facts:
- We already decided (M1) that **most metadata is machine-derived per document**
  during enrichment — which removes inheritance's main payoff (DRY hand-authored
  metadata shared across a directory).
- Enrichment (M3) already produces **markdown with a YAML frontmatter header**
  (see `doc/design.md`), and Mind of Steele does exactly this
  (`format_summary_with_yaml_header`).
- A RAG chunk needs its own metadata; it cannot "inherit" from a directory.

## Decision

**Represent each extracted document as a single markdown file: a YAML frontmatter
prelude + the extracted text as the body.** The frontmatter holds the metadata
(short summary, long summary, keywords, entities, classifications, matters,
author, provenance, handling flags, attributed claims); the body is the extracted
text. This is the conventional, self-contained, tool-standard representation
(Obsidian / Jekyll / RAG pipelines / MoS all use it), parsed with
`python-frontmatter` (already a dependency).

- **Keep** the M1 `DocumentMetadata` field schema — it becomes the *frontmatter
  schema* (extended with `long_summary` and `claims`).
- **Drop** goldberg-meta's heavyweight inheritance engine as the primary
  mechanism.
- **Keep a light folder-defaults merge** (simple parent-provides-defaults,
  child-overrides; lists union) for the few fields that are genuinely
  folder-uniform and human-set — the legal-handling flags (CPIA s.17, privilege,
  disclosure status, source channel) and often `matters`/`parties` — so those are
  set once per folder without hand-stamping every file, and **without** the
  locked/irreversible conflict semantics.

## Consequences

- **M3** emits `frontmatter + body` extracted documents (one `.md` per raw doc).
- **M1** `inheritance.py` is demoted; a lighter `metadata/defaults.py` folder-merge
  replaces it as the primary path (the schema is unchanged and reused).
- `DocumentMetadata` gains `long_summary` and `claims`.
- Simpler, standard, self-contained; each extracted doc carries its own metadata,
  which is what the RAG index needs anyway.

## Downstream

Shapes **M3** (enrichment output format) and **M4** (the extracted-repo writer
persists these frontmatter files; the ES indexer reads frontmatter fields).
