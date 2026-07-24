# Design — corpus model, metadata schema, and ingestion

> **HISTORICAL — this is the record of the 2026-07-20 design session, not the current
> state of the system.** It is preserved because it explains *why* the corpus model and
> metadata schema are shaped as they are, and that reasoning still holds. But the
> pipeline described in §5–§6 (a filesystem watcher, a per-mime extractor set, the
> `goldberg_files` index) was superseded during implementation, and the "open decisions"
> in §10 were all decided.
>
> **For the current system, read [`architecture.md`](./architecture.md)** — the canonical
> technical description. Sections still accurate here: §1 (purpose), §2 (two-axis
> taxonomy), §3 (the document unit), §4 (metadata schema), §7 (claim-aware enrichment),
> §11 (data boundary). Corrections are marked inline below.

This is the technical backbone the pipeline missions were sliced from. It built on
[architecture.md](./architecture.md) (four-repo topology + Halob) and
[workflow.md](./workflow.md) (the per-document pipeline), and records the design
decisions taken in the 2026-07-20 design session.

## 1. Purpose (what the system is actually for)

goldberg-system is a **defence research + drafting tool** for a live UK private
prosecution in which the user is one of the defendants. It has two concrete jobs:

1. **Answer attributed questions across the evidence corpus** — e.g. *"When Simon
   Goldberg described Goldberg v Fadhley & Others, who did he say was the
   prosecuting entity?"* Answers must be **grounded and attributed**: cite the
   source document, its raw commit, the speaker, and the date.
2. **Draft responses to the court and other parties**, grounded in the corpus and
   in the user's own prior research.

Two capabilities are **both first-class**: attributed Q&A, and **cross-corpus
contradiction detection** (surfacing where a party's account shifts over time).

## 2. The two-axis taxonomy

Documents are classified on **two independent axes**, not one flat category list.
This is what stops categories from sliding between buckets.

- **`origin`** — `received` vs `authored`.
- **`role`** — `input` (indexed knowledge the system reasons over) vs `output`
  (a deliverable we produce to share). Optional `shared` flag.

| Category | origin | role | Notes |
|---|---|---|---|
| `evidence/` | received | **input** | source facts, organised by party/matter |
| `exhibits/` | received | **input** | documentary exhibits |
| `reports/` | authored | **input** (+shared) | reusable legal-research memos, by topic |
| `analysis/` | authored (AI) | **input** | cached LLM research — a memoised answer, reused |
| `briefings/` | authored | **output** | short emails to legal support |
| `filings/` | authored | **output** | major court documents / applications |

**Rule:** anything with **`role = input` is enriched + indexed into the RAG**
(evidence, exhibits, reports, analysis). **`role = output`** documents (briefings,
filings) are deliverables and are **not** indexed as knowledge. "Authored
knowledge we share" = reports + filings.

### `analysis/` as a research cache

An analysis document is a **memoised answer**: the user asks the LLM to investigate
a specific question and the result is persisted so the work is reusable. Because
enrichment is claim-aware, cached research becomes queryable and claim-comparable
alongside the evidence — the system **accumulates its own reusable doctrine**.
This is a deliberate *knowledge-layer* loop (produce → cache → index → reuse),
distinct from the extraction pipeline's strict one-way rule; it must be designed
so persisting an analysis doc does **not** trigger uncontrolled reprocessing.

## 3. The document unit

The unit of a document is a **leaf directory**, not a single file:

```
evidence/<party-or-matter>/<doc>/
  <original>.pdf|.eml|.md   ← raw original
  attachments/              ← raw (e.g. email attachments)
  markdown/                 ← extracted (regenerable)
  metadata.yaml             ← provenance (goldberg-meta, directory-inherited)
```

Split across repos by mirrored path:

- `original + attachments/ + metadata.yaml` → **goldberg-raw**
- `markdown/` (the extraction) → **goldberg-extracted** (pipeline-regenerated)
- authored outputs (`briefings/`, `filings/`) → **goldberg-casework**

## 4. Metadata schema

The existing **goldberg-meta** system is ported: `metadata.yaml` files with
directory inheritance (`LOCKED` / `OVERRIDABLE` / `NON_INHERITED` semantics). But
it **was not used reliably** historically, so the new schema is populated in **two
tiers** — most fields machine-derived from the raw document, with `metadata.yaml`
as an *optional authoritative override*.

| Field | Populated by | Notes |
|---|---|---|
| `summary`, `keywords`, `parties`, `topic`, `date` | machine (enrichment) | metadata.yaml can override/lock |
| `document_type`, `party_role` | machine, human-lockable | inferred, correctable |
| `author` / `source_party` | machine | **who is speaking** (distinct from `parties` = who it is *about*) — powers attributed queries |
| `entities` | machine | people/orgs/refs, for retrieval |
| `matters` | machine-suggested, human-confirmed | **list** (corpus spans several matters); optional `primary_matter` |
| `origin`, `role` | machine, human-lockable | the two-axis tags above |
| `raw_path`, `raw_commit` | auto (pipeline) | provenance link extracted → raw |
| `source_channel` | **human** | official-disclosure / own-records / third-party / unofficial; default `unknown` |
| `disclosure_status` | **human** | served / unused / undisclosed / own; default `unknown` |
| `cpia_s17`, `privileged`, `sensitivity` | **human** | legal judgments; default to the **safe** value (treat as sensitive until cleared) |
| `relates_to` | human / machine-assisted | "responds to", "cited in" — for drafting |
| `skip`, `skip_patterns`, `files` | human | ported as-is |

**Legal-handling flags are human-authored and the LLM must not invent them.** When
absent they default safe. Everything else the machine fills so the corpus is useful
from raw even where the old collection had no metadata.

### Matters (multi-matter corpus)

The corpus spans several related matters; `case_number` is replaced by a
multi-valued `matters` list. Known matters:

- `422500059892` — main prosecution, *R v Fadhley*
- `422500059914` — *Goldberg v Mannino & Ors* (co-defendants)
- `648MC011` — *Goldberg v Afshar* / ETP
- `L00SS179` — *Deacon v Goldberg*

## 5. Two ingest paths

> **SUPERSEDED.** The stage *shape* below (extract → enrich → persist → index) survived,
> but the extractor set did not: extraction is a single call to a self-hosted **Docling**
> server, not a per-mime dispatch to pdftotext/tesseract/pandoc, and the index is
> `goldberg_documents`, not `goldberg_files`. See
> [architecture.md §5](./architecture.md#5-the-ingestion-path-write-side) and
> [§7](./architecture.md#7-why-docling--and-not-the-alternatives).

Both paths converge on the same enrich + index stages and the same RAG.

```
A. Evidence pipeline (received inputs: evidence, exhibits)
   raw file saved on Halob
        │  (file-watch trigger, per-leaf, debounced)
        ▼
   EXTRACT → markdown        (dispatch by mime: pdftotext · OCR · eml-to-md · pandoc · readability · passthrough)
        ▼
   ENRICH  (claim-aware)     (Mind of Steele llm_support: summary + keywords + entities + attributed claims)
        ▼
   PERSIST → goldberg-extracted (mirrors raw path; links to raw commit)
        ├──▶ INDEX → Elasticsearch (goldberg_files)
        └──▶ WIKI  → RAG sink
        ▼
   DONE (searchable + queryable, attributed)

B. Input passthrough (authored inputs already in markdown: reports, analysis)
   markdown saved
        │  (file-watch trigger)
        ▼
   ENRICH (claim-aware) ──▶ INDEX + WIKI     (no EXTRACT, no PERSIST-to-extracted)
```

Authored outputs (`briefings`, `filings`) ride neither path — they are deliverables.

## 6. Trigger behaviour

> **SUPERSEDED — the trigger is a git commit hook publishing to NATS, not a filesystem
> watcher.** A filesystem watcher was rejected on implementation: the corpus is on an
> SMB-mounted NAS where change notifications are unreliably delivered, and a silently
> missed event is an invisible hole in a legal corpus. The properties listed below
> (per-document scope, idempotency, safe/non-looping, raw is never written) all survived
> and are still true. Full reasoning, including the intermediate polling design and why
> it was retired: [architecture.md §6](./architecture.md#6-why-trigger-not-poll) and
> [ADR 0013](./decisions/0013-event-driven-ingestion.md).

The corpus lives on Halob **specifically so file changes can trigger the
pipeline** — the trigger is a **Halob-local filesystem watcher**, not a GitHub
webhook. Saving a file into `goldberg-raw`:

- triggers the **full per-document workflow** (`extract → enrich → persist → index
  → wiki`), **scoped to that one document** — not a corpus-wide re-run;
- is coalesced at **leaf-directory granularity** and **debounced** — a document is
  several files (original + attachments + metadata.yaml), which must fire **one**
  job after the writes settle, not one per file;
- is **idempotent**: a deterministic doc-id keyed on the raw file means re-saving
  **updates**, never duplicates. A content-hash **staleness check** skips
  re-extraction when only `metadata.yaml` changed (re-enrich + re-index only);
- is **safe + non-looping**: extraction failures dead-letter and never touch raw;
  writes to `goldberg-extracted` do not re-trigger.

## 7. Enrichment — claim-aware from day one

Enrichment (reusing Mind of Steele's OpenAI-backed `llm_support`) extracts, per
document:

- summary, keywords, entities (people/orgs/refs);
- `author` / `source_party` (who is speaking);
- **attributed assertions** — claims in a comparable form (e.g. *"Goldberg asserts
  prosecuting-entity = X"*), so both attributed Q&A **and** cross-document
  contradiction detection work without a later re-architecture.

## 8. Index scope and RAG

Only `role = input` documents are indexed (evidence, exhibits, reports, analysis).
Answers from the RAG/wiki layer are **grounded and attributed** — every answer
cites source document + raw commit + speaker + date, because the output is destined
for the court.

## 9. Reuse — Mind of Steele

The enrichment engine is reused, not reinvented, from Mind of Steele
(`~/workspace/mind_of_steele`, on the Mac): `env.yaml` holds the OpenAI key
linkage; MoS already does summarise + claim-extract + Elasticsearch chunk/index +
Ragie upload. Exact module names are mapped when wiring the enrich mission (M3).

## 10. Open decisions (for the M0 spike) — **all now closed**

- **Wiki sink** → **RAG on Elasticsearch** ([ADR 0001](./decisions/0001-wiki-rag-sink-backend.md)).
  Ragie was rejected: managed chunking weakens attribution fidelity and cannot express
  cross-document claim comparison. Obsidian is a browse surface, not a retrieval engine.
- **Large binaries** → **selective git-LFS** via `.gitattributes`
  ([ADR 0002](./decisions/0002-large-binary-handling.md)); media excluded from the raw
  repo entirely.
- **Trigger locus** → **git commit hook → NATS**, not a filesystem watcher
  ([ADR 0013](./decisions/0013-event-driven-ingestion.md)) — see §6 above.
- **Data boundary** → cloud LLM permitted as a **logged charter exception** (still true;
  Ragie is not used).

## 11. Data boundary

Legally-sensitive material (CPIA s.17, personal data, unofficially-obtained) **may**
be sent to cloud OpenAI/Anthropic + Ragie for enrichment/RAG. This is a
**deliberate, logged exception**, recorded in the charter — not a silent default.
