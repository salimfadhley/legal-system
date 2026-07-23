# Phase 0 Research — Event-Driven Trigger-Based Ingestion

Resolved design decisions. Each: Decision · Rationale · Alternatives rejected.

## R1 — How commits reach Halob, and how the trigger fires

- **Decision**: Ship a git `post-commit` **and** `post-merge` hook for
  `goldberg-raw`, installed via a repo-committed `core.hooksPath` (e.g.
  `hooks/`), each calling `goldberg publish-commit "$(git rev-parse HEAD)"`.
  The hook fires wherever `git` runs (on-box on Halob, or from a client that
  mounts the tree over SMB) because it executes in the committing process; NATS is
  reachable from both at `nats://192.168.86.31:4222`.
- **Rationale**: `goldberg-raw`'s working tree lives on Halob's filesystem; commits
  are authored there (directly or over the mount) or arrive by pull. `post-commit`
  covers authored commits; `post-merge` covers non-fast-forward pull-merges.
- **Alternatives rejected**: (a) inotify/FSEvents watcher — rejected by ADR 0011
  (unreliable over SMB). (b) GitHub webhook → Halob receiver — needs an
  externally-reachable endpoint through NAT; deferred as a future increment
  (spec Out of Scope).
- **Residual gap** (accepted): a *fast-forward* `git pull` fires neither hook, so a
  doc arriving only via FF-pull between processor restarts waits for the next
  startup catch-up (R6). Given a low, human-driven commit rate this is acceptable;
  a periodic backstop is explicitly **not** added (would reintroduce the polling
  DIR-004 forbids). A manual `goldberg ingest catchup` command is provided as the
  escape hatch. Revisit only if FF-pull ingestion proves common.

## R2 — Publish mechanism from the hook

- **Decision**: The hook calls the in-repo `goldberg publish-commit <sha>` CLI
  (uses `nats-py` + existing config), **not** a raw `nats` CLI binary.
- **Rationale**: reuses config/secrets resolution, is unit-testable with a fake
  publisher, and adds no host dependency beyond the Python package already deployed.
- **Alternatives rejected**: shelling to a `nats` CLI (extra host install, no test
  seam); embedding a Python one-liner in the hook (unversioned, untestable).

## R3 — JetStream stream / subject / consumer topology

- **Decision**:
  - **Stream** `GOLDBERG`, subjects `goldberg.>`, `FileStorage`, retention
    `limits`, dedup window on `Nats-Msg-Id`. (Same stream the downstream
    `nats-es-archive` mission will read — aligns with its `goldberg.>` design.)
  - **Trigger subject** `goldberg.raw.commit`; payload = JSON `{sha, ts, source}`.
    Publish with `Nats-Msg-Id = sha` for idempotent dedup.
  - **Consumer**: durable **pull** consumer `ingest-processor`, explicit ack,
    `max_deliver = 5`, `ack_wait` ≈ 5 min (covers Docling OCR + enrich), backoff.
    On terminal failure (deliveries exhausted) the processor `term()`s the message
    and emits a `failed`/DLQ pipeline event (FR-009).
- **Rationale**: JetStream durability makes the bus the event system-of-record
  (ADR 0012); `Nats-Msg-Id` dedup + deterministic `doc_id` give idempotency; a pull
  consumer bounds concurrency on the petite host (NFR-005).
- **Alternatives rejected**: core NATS (no durability — a processor-down window
  loses events); push consumer (harder to bound concurrency); per-file subjects
  (commit-level is coarser, fewer messages, and the processor expands to files).

## R4 — Resolving a commit's changed files

- **Decision**: `git -C <raw_root> diff-tree --no-commit-id --name-status -r <sha>`
  (added/modified only; deletions ignored for ingest), filtered through the existing
  `Allowlist` and `_SKIP_EXT`. For a root/first commit fall back to
  `git show --name-status`. The processor maps each surviving path → its manifest
  SHA and calls `process_one`.
- **Rationale**: precise per-commit deltas keep work minimal and event-scoped;
  reuses the same allowlist/skip rules as the reconciler for parity.
- **Alternatives rejected**: re-scan whole tree per event (that's polling again);
  trusting the hook to enumerate files (loses the commit as the idempotent unit).

## R5 — Enrichment token-safety (FR-008)

- **Decision**: In `openai_enricher.py`, budget the body by **tokens** using
  `tiktoken` (encoding for the model), truncating to a safe budget (≈ 100k tokens,
  leaving headroom under 128k for system prompt + instructions + completion).
  Wrap the call in a **defensive retry**: on an OpenAI `context_length_exceeded`
  `BadRequestError`, halve the body and retry (bounded attempts) so any tokenizer
  drift still converges. Replace the miscalibrated `_MAX_BODY_CHARS = 200_000`
  char cap (it assumed ~4 chars/token; token-dense OCR hit ~1.5, so 200k chars →
  ~128.6k tokens).
- **Rationale**: token budgeting is the correct unit; the retry is a
  tokenizer-agnostic backstop. Mirrors Mind of Steele `llm_support` token handling.
- **Alternatives rejected**: lower the char cap only (still unreliable at pathological
  density); chunk-and-summarize map-reduce (larger change; unnecessary for the
  observed cases — truncation is sufficient and preserves the leading, most-salient
  text).

## R6 — Startup catch-up (FR-007, one-shot)

- **Decision**: Extract the reconciler's raw-vs-indexed **diff** (provenance refresh
  + `already_indexed` resume set + bounded `_select_pending`) into
  `ingest/catchup.py` as a **single bounded pass** invoked once at `ingest-serve`
  startup, before the consumer begins pulling. It ingests the difference via the
  same `process_one`, emits events, then returns.
- **Rationale**: preserves the reconciler's only genuinely valuable property (no
  silently-dropped document) while discarding the wasteful forever-loop; satisfies
  NFR-002 (zero idle scans) because it runs exactly once per boot.
- **Alternatives rejected**: keep `run_forever` at a longer interval (still polling);
  no catch-up at all (violates NFR-003 for the processor-was-down window).

## R7 — Idempotency & ordering

- **Decision**: Idempotent by content SHA-256 end-to-end: `Nats-Msg-Id = commit sha`
  (stream dedup), `skip_shas = already_indexed()` in the processor, and deterministic
  `doc_id`. Ordering is not required (each file is independent; re-index is a no-op).
- **Rationale**: at-least-once + idempotency is simpler and safer than exactly-once;
  redelivery or overlap between catch-up and live events cannot duplicate.

## R8 — Deployment shape

- **Decision**: Replace the `reconciler` compose service with an `ingest` service
  running `goldberg ingest-serve` (reusing the reconciler's Dockerfile/volumes:
  read-only `goldberg-raw` incl. `.git`, writable `config/` for the manifest,
  `${GOLDBERG_ES_URL}`, `${NATS_URL}`, `docling:5001`). Health endpoint retained.
  The `goldberg-reconciler` container is already stopped; cutover removes it.
- **Rationale**: minimal topology delta (ADR 0012 already models stateless,
  disposable processing services against external ES/NATS).
- **Alternatives rejected**: a brand-new stack (unnecessary; reuse the tuned volumes).

## Open follow-ups (not blocking; noted for plan→tasks)

- Whether `post-merge` alone suffices for pull-based delivery or a `reference-transaction`
  hook is warranted — decide during IC-03 implementation with a small delivery test.
- `tiktoken` availability offline on Halob (it downloads encodings on first use);
  vendor the encoding or pin a cache dir in the image if network-restricted.
