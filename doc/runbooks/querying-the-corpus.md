# Runbook — querying the corpus

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
- Run tools from the repo root: `uv run legal_system <command>` (or
  `PYTHONPATH=src .venv/bin/python -m goldberg_system.cli <command>`).

## The tools

### `legal_system claims` — who asserted what about whom

The attributed-claims query (a nested ES query over each document's `claims`).
This is the tool for *"who did X say was Y"* and for spotting contradictions
across documents.

```
legal_system claims [--by <speaker>] [--subject <text>] [--object <text>] \
                [--text <free text>] [--matter <id>] [--size N]
```

- `--by` — filter by the speaker (`asserted_by`), exact match.
- `--subject` / `--object` — full-text match on the claim's subject / object.
- `--text` — free text across subject/predicate/object.
- `--matter` — restrict to a matter (repeatable).

Output: one line per matched claim — `speaker asserts: subject — predicate —
object` — plus the source `doc_id` and `raw_path` for citation.

Example — *"What did the sender of the cease-and-desist assert about the recipient?"*:

```
legal_system claims --by "A Sender"
# → A Sender asserts: The Recipient — has unlawfully possessed and used —
#   Confidential Data …  (source: gb_… / evidence/exhibits/letter-…pdf)
```

Contradiction-hunting — the same subject/object across speakers or time:

```
legal_system claims --subject "responsible party"
```

### `legal_system search` — full-text search (BM25)

```
legal_system search "<question or keywords>" [--matter <id>] [--author <name>] \
                [--type <document_type>] [--size N]
```

Searches `content` + `summary` + `long_summary` + `keywords` + `entities`, with
optional filters. Output per hit: `doc_id`, `document_type`, score, `raw_path`,
`matters`, `author`, `summary`, and highlighted snippets.

> **Note (ADR 0014):** the `legal_system wiki` command and the SilverBullet concept wiki
> are **retired** — nobody used them and the synthesised layer was never authored. There
> is now one substrate: the document index (plus the `goldberg-extracted` git store for
> greppable, versioned markdown+frontmatter). Ignore any older "query both
> representations" instruction.

### `legal_system get` — read a document

```
legal_system get <doc_id> [--no-content]
```

Returns the document's full source (metadata + extracted `content`) as JSON — use
it to read and quote the exact text once search/claims has found the right doc.

### `legal_system facets` — orient

```
legal_system facets
```

Terms counts by `matters`, `author`, `document_type`, `parties` — a map of what's
in the index.

### `legal_system audit` — completeness check (did anything not ingest?)

```
legal_system audit --manifest <provenance-manifest.json> [--missing] [--extra]
```

Reconciles the **expected** set (the goldberg-raw provenance manifest) against the
**actual** set (the index), joining on `raw_path` (M12 / [ADR 0008](../decisions/0008-observability-architecture.md)).
Reports `matched`, **`missing`** (expected but never ingested — the drops that would
silently skew answers), and `extra` (indexed under a path the manifest doesn't know,
e.g. a doc indexed without provenance). Exits non-zero when gaps exist, so it can gate
a migration. `--missing` lists every un-ingested `raw_path`; run this after a bulk
migration to prove completeness.

### `legal_system status` — system health (human + LLM-readable)

```
legal_system status [--yaml]
```

The canonical system state (M12/M13, [ADR 0009](../decisions/0009-operations-dashboard.md)):
health checks, corpus counts by matter/type, per-stage/status
pipeline counts, and DLQ depth. Default is a human table; **`--yaml`** emits the same
`SystemState` as YAML so an LLM can read the whole system in one call.

### `legal_system trace` / `legal_system dlq` — why did X (not) ingest

```
legal_system trace <raw_path|sha256|doc_id>   # one document's full stage timeline
legal_system dlq [--status failed] [--status skipped]   # what failed/skipped, and why
```

`trace` resolves any identifier to the document's `raw_sha256` correlation ID and
shows every stage event in order — the stop point is the answer. `dlq` lists the
failed/skipped documents from the event projection.

### `legal_system alert` — proactive gap/failure check (scheduled)

```
legal_system alert [--manifest <provenance-manifest.json>] [--max-failures N] [--json]
```

The reduced M12 phase 4 (ADR 0008): evaluates health + pipeline failures + (with a
manifest) completeness, prints the alerts, and **exits non-zero** — `2` critical,
`1` warning, `0` clear. Drive it from a scheduler so silent drops surface without
anyone running `legal_system audit`. Example cron on Halob (notify on non-zero):

```cron
*/30 * * * * cd /share/home/sal/.../goldberg-system && \
  uv run legal_system alert --manifest config/provenance-manifest.json \
  || mail -s "goldberg: corpus gap/failure" sal@halob < /dev/null
```

Or as a Jenkins job on the existing server — the build fails on non-zero exit and
Jenkins emails. Deliberately *not* built (overkill for a single-user LAN tool):
OpenTelemetry/Grafana and the durable NATS JetStream DLQ (the ES projection +
idempotent `reindex` cover reprocessing).

## How to answer a question

1. **Pick the tool.** Attribution / "who said" / contradiction-hunting → `claims`.
   Topic / keyword → `search` (or `grep` the `goldberg-extracted` store). Then `get`
   the most relevant document(s) to read the exact text.
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
