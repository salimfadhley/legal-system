# Runbook — querying the Goldberg corpus

**Audience:** an agent (Claude Code) or a person answering questions about the case
from the indexed evidence. **Goal:** grounded, **attributed** answers with
citations — never answered from memory, never invented.

The query layer is deliberately thin: the CLI tools do **retrieval** against the
Elasticsearch index; the **agent synthesises** the answer from what comes back.
There is no separate answer-generating LLM in the loop — Claude Code *is* the
answerer.

## Prerequisites

- The document(s) you're asking about must be **indexed** (ran through the
  pipeline: Papra/Docling extract → enrich → index). Coverage grows as the corpus
  is backfilled (M8).
- Config is read from the gitignored `.env` (`GOLDBERG_ES_URL`,
  `GOLDBERG_ES_INDEX`, plus Papra/OpenAI keys). Defaults: ES
  `http://192.168.86.31:9200`, index `goldberg_documents`.
- Run tools from the repo root: `uv run goldberg <command>` (or
  `PYTHONPATH=src .venv/bin/python -m goldberg_system.cli <command>`).

## The tools

### `goldberg claims` — who asserted what about whom

The attributed-claims query (a nested ES query over each document's `claims`).
This is the tool for *"who did X say was Y"* and for spotting contradictions
across documents.

```
goldberg claims [--by <speaker>] [--subject <text>] [--object <text>] \
                [--text <free text>] [--matter <id>] [--size N]
```

- `--by` — filter by the speaker (`asserted_by`), exact match.
- `--subject` / `--object` — full-text match on the claim's subject / object.
- `--text` — free text across subject/predicate/object.
- `--matter` — restrict to a matter (repeatable).

Output: one line per matched claim — `speaker asserts: subject — predicate —
object` — plus the source `doc_id` and `raw_path` for citation.

Example — *"What did the sender of the cease-and-desist assert about Salim?"*:

```
goldberg claims --by "E Lowe"
# → E Lowe asserts: Mr. Salim Fadhley — has unlawfully possessed and used —
#   Confidential Data …  (source: gb_… / evidence/exhibits/letter-…pdf)
```

Contradiction-hunting — the same subject/object across speakers or time:

```
goldberg claims --subject "prosecuting entity"
```

### `goldberg search` — full-text search (BM25)

```
goldberg search "<question or keywords>" [--matter <id>] [--author <name>] \
                [--type <document_type>] [--size N]
```

Searches `content` + `summary` + `long_summary` + `keywords` + `entities`, with
optional filters. Output per hit: `doc_id`, `document_type`, score, `raw_path`,
`matters`, `author`, `summary`, and highlighted snippets.

### `goldberg get` — read a document

```
goldberg get <doc_id> [--no-content]
```

Returns the document's full source (metadata + extracted `content`) as JSON — use
it to read and quote the exact text once search/claims has found the right doc.

### `goldberg facets` — orient

```
goldberg facets
```

Terms counts by `matters`, `author`, `document_type`, `parties` — a map of what's
in the index.

## How to answer a question

1. **Pick the tool.** Attribution / "who said" → `claims`. Topic / keyword →
   `search`. Then `get` the most relevant document(s) to read the exact text.
2. **Synthesise** the answer only from what you retrieved. If nothing relevant is
   found, say so — do not guess.
3. **Cite every claim**: source `doc_id` + `raw_path`, the **speaker**
   (`asserted_by` / `author`), and the date where available. This is legal work
   product; provenance is mandatory.

## The index

Elasticsearch index `goldberg_documents` on Halob. One document per enriched
markdown-with-frontmatter file (ADR 0004). Key fields:

| Field | Type | Notes |
|---|---|---|
| `content` | text | extracted body (BM25) |
| `summary`, `long_summary` | text | machine summaries |
| `keywords`, `entities`, `parties`, `author` | keyword | facet/filter |
| `matters`, `primary_matter` | keyword | multi-valued case ids |
| `document_type`, `party_role`, `origin`, `role`, `topic` | keyword | classification |
| `claims` | **nested** | `{subject, predicate, object, asserted_by}` — attributed assertions |
| `handling` | object | `cpia_s17`, `privileged`, `sensitivity`, `disclosure_status`, `source_channel` |
| `raw_path`, `raw_commit`, `ingested_at`, `papra_document_id` | keyword/date | provenance |

The stale legacy `goldberg_files` index (589 docs, pre-frontmatter schema) is left
untouched for rollback; it is reindexed into `goldberg_documents` during M8.

## Related

- Code: `src/goldberg_system/query.py` (`CorpusQuery`), `src/goldberg_system/cli.py`.
- Design: [`../design.md`](../design.md), [`../workflow.md`](../workflow.md),
  ADR [0001](../decisions/0001-wiki-rag-sink-backend.md) (RAG-on-ES),
  [0004](../decisions/0004-metadata-representation.md) (frontmatter).
