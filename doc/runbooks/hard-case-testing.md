# Runbook — hard-case extraction testing

An isolated regression suite for the extraction/ingestion pipeline, run against the
*worst* documents so we never re-break on them. Born from the M8 migration, where a
20-doc sample exposed a ~20% failure rate (large scans hitting Docling's 120s sync
timeout; `.json` that Docling couldn't parse).

## The workflow (the rule)

**Whenever a document fails or misbehaves in the pipeline, add it to
`config/hard-cases.yaml`.** That turns every incident into a permanent regression
guard. Two kinds of case:

- **`real`** — an actual corpus document (by `raw_path` in goldberg-raw) that broke.
- **`synthetic`** — a document we *generate* to probe a theoretical limit before a
  real one exposes it (huge text, empty PDF, odd encoding). Generators live in
  `goldberg_system.testing.synthetic`.

Each case declares an `expect`:

| key | meaning |
|---|---|
| `min_chars` | extraction must yield at least N characters (default 1) |
| `allow_empty` | empty extraction is acceptable (e.g. a decorative image) |
| `allow_error` | an extraction error is acceptable (a known unsupported format) |
| `contains` | the extraction must contain this (case-insensitive) substring |

## Running it

```bash
# extraction suite — no live index, no OpenAI cost, exits non-zero on any failure
uv run legal_system test-hard-cases [--only <case>]
```

Needs Docling reachable (`GOLDBERG_DOCLING_URL`, default `http://localhost:5001` — run
`ssh -f -N -L 5001:localhost:5001 sal@halob` from the Mac). It extracts each case via
Docling and checks the expectation.

## Full-pipeline isolation (a separate index)

To test extract → enrich → **index** without touching the live corpus, point the
re-ingest at a throwaway index:

```bash
uv run legal_system migrate reingest --index goldberg_documents_test --only <raw_path>
```

Everything (indexer, `search`, `audit`, the MCP server) reads its index from
`GOLDBERG_ES_INDEX`, so the live `goldberg_documents` is never touched.

## When the suite catches a regression

A failing case means a real capability broke. Fix the pipeline (not the expectation),
re-run until green, and only then run the bulk re-ingest. The suite is the gate.
