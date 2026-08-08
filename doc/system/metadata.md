# Corpus metadata: folder inheritance + per-file sidecars

**Status:** design, out for casework review (2026-08-08). Structured/`no_index`/`claim_source`
parts already shipped; per-file **sidecars** and the **`notes` → enrichment** wiring are the
new work this doc proposes.

## Why this exists

Every document is enriched by an LLM that sees **one file in isolation**. That makes its
inferred metadata detailed but *context-blind* — it cannot know that a PDF is Exhibit
SM/01, that a folder is all one speaker's work-product, or that a scanned figure is OCR
noise rather than the witness's number. This mechanism lets a human record that context,
**authoritatively, in the repo, next to the file**, and have it override inference.

Two principles throughout:

1. **Human metadata beats inference.** A value a person wrote always wins over the model's guess.
2. **Never fail silently.** A typo'd key, an orphan sidecar, or a dropped value must be *loud*.
   A metadata layer that silently does nothing is worse than none — you think it's set and it isn't.

## Two carriers, one resolution chain

| Carrier | File | Scope |
|---|---|---|
| **Folder defaults** *(shipped)* | `metadata.yaml` in a folder | everything at/below that folder |
| **Per-file sidecar** *(proposed)* | `<filename.ext>.metadata.yaml` next to the file | that one file |

They are resolved as **one ordered chain, least-specific first**:

```
repo-root metadata.yaml  →  …  →  leaf-folder metadata.yaml  →  <file>.metadata.yaml
```

The last layer that sets a field wins. This is the existing `_resolve_chain` /
`merge_folder_defaults` machinery (ADR 0004) with the sidecar appended as the final,
most-specific layer — so **every field already works at file level**, including a per-file
`no_index` (restrict *one* document without hiding its folder) and `claim_source`.

**Merge semantics** (unchanged from folder defaults):
- **Scalars** (`author`, `document_type`, `claim_source`, `no_index`, …): most-specific non-null wins → *override*.
- **Lists** (`matters`, `keywords`, `parties`): order-preserving union → *augment*.

## Structured keys

Any field of the document schema can be set. The high-value ones:

- `author` — who is speaking (overrides the inferred author).
- `claim_source` — authoritative speaker for **every claim** in the file (overrides per-claim inference).
- `matters`, `primary_matter`, `document_type`, `parties`.
- `no_index` + `no_index_reason` — exclude from the search index (legal/contractual restriction).

## Prose: the `notes` field

The part a person most wants and the schema didn't have. Free English that does **two** jobs:

1. **Fed to the enricher as ground-truth context** *before* it extracts — so summary, claims and
   attribution reflect what the human knows, not just what one file says in isolation.
2. **Stored and indexed** so the note itself is searchable and visible on the document.

```yaml
# ETP_Overview_Of_Third_Party_Tax_Specialist_Services.pdf.metadata.yaml
author: Paul Keitch
document_type: witness statement
claim_source: Paul Keitch
matters: ["422500059892"]
notes: |
  Exhibit SM/01 — the disclosure officer's own statement. Dates in section 3 are
  the police timeline, not ours. Scanned: treat garbled figures as OCR noise.
```

## Safety rules (enforced, not advisory)

1. **Unknown/typo'd key → loud error**, naming the file and the key. Never silently ignored.
2. **Orphan sidecar** (`foo.pdf.metadata.yaml` with no `foo.pdf`) → warning at ingest, surfaced in status.
3. **Re-applied on every (re)ingest / re-enrich**, so editing a sidecar takes effect next pass.
4. **`metadata.yaml` and `*.metadata.yaml` are never themselves ingested** as documents.
5. **Originals are never mutated** — the sidecar carries the metadata; the evidence file is untouched.

## Open questions for casework

1. **Should `notes` also be appended to the indexed `content`** (so a full-text search hits the
   note), or kept as enricher-context + a separate stored field only? (Lean: both.)
2. **Unknown keys** — hard-error and refuse the file, or accept and stash under an `annotations`
   map so nothing is lost? (Lean: hard-error, because a silently-ignored typo is the exact
   false-comfort failure we keep hitting.)
3. **A `method` / provenance field?** You noted metadata should be weighted by *what was actually
   opened*, never by a `verified: true` boolean or a title. Should a sidecar be able to record
   `method: "checked against legislation.gov.uk 2026-08-07"` as a first-class, machine-visible field?
4. **Do you want a `legal_system metadata lint` command** that validates every sidecar/folder file
   (unknown keys, orphans, malformed YAML) and refuses to pass if any fail — the self-test discipline
   you asked for on the grounding checker, applied to metadata?
5. Any field you need that the schema doesn't have yet?
