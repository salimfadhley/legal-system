---
work_package_id: WP01
title: Enrichment token-safety (fold-in fix)
dependencies: []
requirement_refs:
- FR-008
tracker_refs: []
planning_base_branch: feat/goldberg-nats-es-archive
merge_target_branch: feat/goldberg-nats-es-archive
branch_strategy: Planning artifacts for this mission were generated on feat/goldberg-nats-es-archive. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into feat/goldberg-nats-es-archive unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
- T005
agent: claude
history:
- created by /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: src/goldberg_system/enrichment/
create_intent:
- tests/unit/test_enrichment_token_safety.py
execution_mode: code_change
owned_files:
- src/goldberg_system/enrichment/openai_enricher.py
- tests/unit/test_enrichment_token_safety.py
- pyproject.toml
role: implementer
tags: []
---

## ⚡ Do This First: Load Agent Profile

Before reading anything else, load your assigned profile:
`/ad-hoc-profile-load python-pedro` (role: implementer). Adopt its identity,
governance scope, and TDD discipline for this work package.

## Objective

Make enrichment safe for arbitrarily large documents. Today
`src/goldberg_system/enrichment/openai_enricher.py` caps the body at
`_MAX_BODY_CHARS = 200_000` **characters**, on the wrong assumption that ≈50k
tokens fits the 128k-token model. Token-dense OCR text hits ~1.5 chars/token, so
200k chars → ~128.6k tokens → a hard `400 context_length_exceeded`. Replace the
char cap with a **token budget** (via `tiktoken`) and add a **defensive retry**
that shrinks the body if the model still rejects it. This closes the observed
failure class (the two OCR `combined.tsv` files that stormed the DLQ).

## Context

- File: `src/goldberg_system/enrichment/openai_enricher.py`.
  - `_build_messages()` currently does `body = request.markdown[:_MAX_BODY_CHARS]`.
  - `enrich()` calls `self._client.chat.completions.create(...)` once.
- The client is injected (tests use a fake), so unit tests need no network.
- Keep the enrichment output contract unchanged (summary/long_summary/keywords/
  entities/author/document_type/claims).
- `tiktoken` downloads encodings on first use; note the offline-Halob caveat in a
  code comment (research R5) — pin `TIKTOKEN_CACHE_DIR` or fall back gracefully.

## Subtasks

### T001 — Failing test first (TDD)
Create `tests/unit/test_enrichment_token_safety.py`:
- A fake OpenAI-like client that records the messages it receives and can be
  configured to raise a `context_length_exceeded`-style error on the first call.
- **Test A**: given a `request.markdown` far larger than the budget, after
  `enrich()` the recorded user message token count (via the same tiktoken encoding,
  or a length proxy the code exposes) is ≤ the configured budget.
- **Test B**: given a fake client that raises a context-length 400 on first call
  then succeeds, `enrich()` returns a normal `EnrichmentResult` (retry shrank the
  body) and does **not** propagate the error.
- **Test C**: a normal small document is unchanged (no truncation, single call).
Run and confirm they fail.

### T002 — Token-budget truncation
In `_build_messages()`, replace the char slice with a token-budget truncation:
- Add a module helper `_truncate_to_tokens(text, budget, model)` using
  `tiktoken.encoding_for_model(model)` (fallback `get_encoding("cl100k_base")`).
- Budget default ≈ `100_000` tokens for the body (leaves headroom under 128k for
  the system prompt, instructions, and completion). Make it a module constant
  `_BODY_TOKEN_BUDGET`.
- Encode, slice to budget, decode back to text.

### T003 — Defensive context-length retry
Wrap the `create()` call in `enrich()` so that on an OpenAI `BadRequestError`
whose code/message indicates `context_length_exceeded`, the body is halved and the
call retried, up to a small bounded number of attempts (e.g. 3). If still failing,
re-raise (the processor will DLQ it — that's correct for a truly impossible doc).
Catch narrowly (inspect the exception's `code`/message), not blanket `except`.

### T004 — Remove the char cap + dependency
- Delete `_MAX_BODY_CHARS` and its use.
- Add `tiktoken` to `pyproject.toml` `dependencies`. Run `uv sync`.
- Add a brief comment referencing the offline-encoding caveat.

### T005 — Verify
`uv run pytest tests/unit/test_enrichment_token_safety.py` green. Run the broader
enrichment unit tests to ensure no regression. Note in the WP history that the
storm-file class is now handled (the live backfill/verify is WP06, not here).

## Branch Strategy

Planning base and final merge target are both `feat/goldberg-nats-es-archive`.
Execution worktrees are allocated per computed lane from `lanes.json` at implement
time — do not create branches manually.

## Definition of Done

- [ ] New tests written first, initially failing, now passing.
- [ ] `enrich()` cannot raise `context_length_exceeded` for any input size.
- [ ] Token budgeting via tiktoken with a documented budget + offline caveat.
- [ ] `_MAX_BODY_CHARS` removed; `tiktoken` added to deps; `uv sync` clean.
- [ ] No change to the `EnrichmentResult` output contract.

## Risks / Reviewer guidance

- **Risk**: tiktoken encoding mismatch vs the actual model. Mitigated by the
  shrink-and-retry backstop (T003) — reviewer should confirm the retry path is
  covered by Test B.
- **Reviewer**: verify truncation keeps the *leading* text (most salient), the
  retry catch is narrow (not blanket), and the budget leaves real headroom.
