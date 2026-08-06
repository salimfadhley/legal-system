# ADR 0015 — goldberg-extracted as the derived store; retire Papra

**Status:** Accepted · **Date:** 2026-08-06 ·
**Relates to:** [ADR 0003](0003-document-management-papra-integration.md) (Papra),
[ADR 0004](0004-metadata-representation.md) (frontmatter),
[ADR 0013](0013-event-driven-ingestion.md) (direct-Docling ingest)

## Context

Extraction no longer runs through Papra. Since [ADR 0013](0013-event-driven-ingestion.md)
the live ingest path is **event-driven with direct Docling** extraction; the
Papra-webhook processor is retired (M15). Papra therefore survives only as a *store* of
extracted content that nothing reads — the query substrate is Elasticsearch
(`goldberg_documents`, ~1,640 documents) and the MCP tools over it.

Meanwhile the derived, human-readable representation was homeless. [ADR 0007](0007-concept-wiki-output.md)
§2 designed two downstream sinks besides the ES indexer — an `ExtractedRepoWriter`
(git-markdown mirror → `goldberg-extracted`) and a `WikiAuthorSink` — but neither was
wired into a running path: `goldberg-extracted` sat empty, and the extracted markdown
ended up co-located inside `goldberg-raw` (`**/markdown/*.md`), mixing derived content
into the immutable-originals repo.

`ExtractedRepoWriter` itself **was** built (`sinks/extracted_writer.py`) — it writes an
`EnrichedDocument` as a markdown+frontmatter file mirroring `raw_path`. It was only ever
reachable behind the retired Papra-backfill's `--extracted-root` flag, so it never ran.

## Decision

1. **Adopt `goldberg-extracted` as the derived store** — the middle tier of a clean
   three-tier model:

   | Tier | Where | Role |
   |---|---|---|
   | Originals | `goldberg-raw` (private) | immutable source files + binaries |
   | **Derived** | **`goldberg-extracted` (private)** | git source-of-truth: extracted markdown + enriched frontmatter |
   | Index | ES `goldberg_documents` | query projection — rebuildable from the derived store |

   It is git-hosted (browsable, grep-able, diff-able history), private (identical
   privilege posture to `goldberg-raw`, which is already a private remote), and
   regenerable — never hand-edited.

2. **Populate it cheaply from Elasticsearch, not by re-enriching.** New command
   `goldberg backfill-extracted --extracted-root <repo>` scrolls the ES index and writes
   each already-enriched document out as a frontmatter `.md`. **No Docling
   re-extraction, no LLM call, and the live index is never modified.** Idempotent.
   (Full 1,640-doc backfill: seconds-to-minutes, $0.) A re-enrich is only for changing
   *what* is extracted (e.g. the claim-schema work), not for populating this store.

3. **Keep it live via the ingest service.** `goldberg ingest-serve` gains
   `--extracted-root` (default `$GOLDBERG_EXTRACTED_ROOT`); when set, `ExtractedRepoWriter`
   joins the sink list alongside the ES indexer, so every newly-ingested document is
   mirrored to the derived store as it is indexed.

4. **Retire Papra.** With extraction on direct Docling and derived content in a git
   store, Papra has no remaining role. Stand it down as a store; this supersedes the
   store half of [ADR 0003](0003-document-management-papra-integration.md). (Teardown of
   the running Papra service is shared-infra — documented in Mind of Steele per
   [[shared-infra-docs]].)

## Consequences

- **ES becomes disposable/reproducible.** The index is now a projection of a durable
  git store. A future "rebuild ES from `goldberg-extracted`" path (parse frontmatter
  `.md` → index, no Docling/LLM) is the natural follow-up — deferred, not built here.
- **The retired Papra-backfill (`goldberg reindex`, `backfill_from_papra`) is now dead
  code** to remove or clearly mark; it reads from Papra, which is going away.
- **Duplicate derived markdown in `goldberg-raw`.** The `**/markdown/*.md` files inside
  `goldberg-raw` are now redundant with the derived store. Cleaning them out of raw
  (so raw holds only originals) is deliberate — raw is the frozen archive — and is left
  as a **separate, explicit step**, not done here.
- **Naming quirk:** the extracted path is `raw_path + ".md"`, so a source already ending
  `.md` becomes `.md.md`. Cosmetic, deterministic, reversible; a possible later polish.
- **Consistency with [ADR 0014](0014-retire-silverbullet-wiki.md).** Both retire a
  running service (SilverBullet, Papra) in favour of git-hosted markdown + ES, and align
  with the Mr Lawyer rebuild direction.

## Status of the first backfill

Run 2026-08-06: **1,640 documents written, 0 failures**, committed to `goldberg-extracted`
(private). The live-path `--extracted-root` wiring is in the code; enabling it on the
running Halob ingest service is a redeploy (set `$GOLDBERG_EXTRACTED_ROOT` or pass the
flag) — a follow-up deployment step.
