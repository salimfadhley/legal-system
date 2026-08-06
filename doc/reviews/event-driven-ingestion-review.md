# Review brief: Event-Driven Trigger-Based Ingestion

Independent, adversarial review. Find defects; do not bless. **Do not modify code** —
produce a written report only.

## Why this mission exists
The pipeline used to auto-ingest via a POLLING reconciler (`legal_system watch`) that
re-hashed the whole `goldberg-raw` corpus every 2 minutes and retried failures
forever — wasteful, and against charter DIR-004 ("trigger, don't poll"). It also had
a bug: oversized OCR `.tsv` files exceeded the LLM's 128k-token context and hard-failed
(`400 context_length_exceeded`), then retried endlessly — a DLQ storm. This mission
replaces the reconciler with an event-driven flow and fixes the enrich bug.
Storm files: `evidence/example_party/{mortgage_dossier,complaint_pack}/ocr_output/combined.tsv`.

## What was built
1. **Enrich token-safety** — `src/goldberg_system/enrichment/openai_enricher.py`:
   tiktoken token-budget truncation + bounded shrink-and-retry (was a wrong-unit char cap).
2. **Messaging boundary** — `src/goldberg_system/messaging/`: only place importing `nats-py`.
3. **Ingest service** — `src/goldberg_system/ingest/`: durable JetStream consumer that per
   `goldberg-raw` commit resolves changed files and runs the EXISTING
   `migrate/reingest.py::process_one` (ack/nak/term + DLQ); bounded ONE-SHOT startup
   catch-up; CLI `ingest-serve` / `publish-commit` / `ingest catchup`.
4. **Trigger** — `hooks/`: git `post-commit`/`post-merge` → `legal_system publish-commit`;
   must NEVER fail `git`.
5. **Decommission** — remove `src/goldberg_system/reconcile/` + `legal_system watch`;
   `deploy/docker-compose.yml` `reconciler`→`ingest`; ADR `doc/decisions/0013-*` supersedes `0011`.

## Read first
`kitty-specs/event-driven-ingestion-01KY79WK/{spec.md,plan.md,research.md,contracts/interfaces.md}`,
`.kittify/charter/charter.md` (DIR-001 provenance, DIR-004 trigger-not-poll), then
`git diff main...feat/goldberg-nats-es-archive -- src/ hooks/ deploy/ doc/decisions/`.

## Checklist — verify each, cite file:line, try to break it
- **A. Storm can't recur.** `enrich()` bounded for ANY input size? Retry catch NARROW
  (only context-length 400s; other 400s propagate)? Impossible doc terminates (DLQ), not loops?
- **B. No dropped legal doc (DIR-001).** Provenance written BEFORE indexing (reuse of
  `process_one`, not a fork)? Startup catch-up covers the processor-was-down window?
  Fast-forward-`git pull` gap documented/acceptable?
- **C. No polling (DIR-004).** Catch-up strictly ONE-SHOT (no loop)? `reconcile/` + `watch`
  fully removed, no dangling imports? Grep for any residual periodic scan.
- **D. Message semantics.** Commit ACKed ONLY after ALL its files terminal (else a
  mid-commit crash loses docs)? nak transient vs term after max_deliver? `ack_wait`
  long enough for Docling+enrich (default 30s — flag if slow OCR exceeds it)? Idempotent
  on redelivery (Nats-Msg-Id dedup + deterministic doc_id + skip already-indexed)?
- **E. Hook can't break git.** Every hook path `exit 0`, even on publish failure?
- **F. Boundary + reuse.** Nothing outside `messaging/` imports `nats`? extract/enrich/index
  reused (not reimplemented)? Same pipeline events (status/dlq/trace still work)?
- **G. Dead code.** New modules actually invoked from the live CLI/service path?
- **H. Tests.** Unit tests use injected fakes (no live broker) and would FAIL if the impl
  were reverted (not synthetic)? Run `uv run pytest -q`.
- **I. Deploy safety.** compose `ingest` mounts `goldberg-raw:ro` (incl. `.git`) + writable
  manifest volume; connects to external ES/NATS (never recreates)?

## Output
Markdown report: overall verdict (SHIP / SHIP-WITH-FIXES / BLOCK), then findings ranked
CRITICAL/HIGH/MEDIUM/LOW — each with file:line, a concrete failure scenario, and a fix.
State explicitly whether checks A, B, C hold. Run the test suite and report the result.
