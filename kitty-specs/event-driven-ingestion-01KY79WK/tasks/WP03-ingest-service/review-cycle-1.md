# WP03 review feedback — cycle 1 (from independent Codex review, verdict BLOCK)

An independent adversarial review (Codex, gpt-5.5) found correctness gaps that
violate the mission's central invariant (DIR-001 / FR-002 / NFR-003: **no silently
dropped legal document**). Fix all three below (TDD — add failing tests first).
Stay in the lane-c worktree. The messaging changes (item 2) are a coordinated
cross-WP edit to `src/goldberg_system/messaging/` — make them here and note the
rationale in the WP history (WP02 is already approved and won't re-touch these).

## FIX 1 (HIGH) — commit resolution silently drops files by ACKing an empty result

**Problem:** `ingest/commit_files.py::changed_files()` returns `[]` for THREE
different situations that the processor cannot distinguish:
- a commit that genuinely touched no allowlisted files (correct to ack),
- a **merge commit** — `git diff-tree`/`git show --name-status` emit NO rows for a
  normal merge without `-m` (empirically confirmed), and
- a **git failure** — `_run_git()` ignores a non-zero git exit code and returns "".

`processor.py` treats `results=[]` as success and **acks** the message. So a
`post-merge` event for a merge that introduced `evidence/new.pdf`, or a transient
`.git` read error, silently loses the document until a later restart/manual
catch-up. On a legal corpus this is unacceptable.

**Required fix:**
- `_run_git()` MUST check the return code and distinguish success from failure.
  A git failure is a **transient** error → the commit must **NAK** (redeliver),
  never ack.
- Distinguish "resolved successfully, zero allowlisted files" (ack is correct)
  from "could not resolve" (nak). Consider returning a typed result
  (e.g. `(resolved: bool, files: list[str])`) or raising a typed exception the
  processor maps to nak.
- Handle **merge commits** explicitly: use `git diff-tree -m --name-status` (or a
  documented first-parent `-r <sha>^!` strategy) so a merge's introduced files are
  resolved. Pick one, document it, and make sure post-merge events (WP04) actually
  ingest the files they bring in.
- In `processor.py`, only ack on a genuine "resolved, all files terminal-ok"
  outcome; an unresolved/failed resolution must nak (subject to max_deliver → term
  + DLQ, so it still can't loop forever).

**Tests (add, failing first):** invalid/unknown SHA → nak (not ack); simulated git
failure → nak; a real merge commit that introduced an allowlisted file → that file
is resolved and ingested; a genuinely empty commit → ack.

## FIX 2 (HIGH) — retry timing exhausts deliveries too fast (vs the design)

**Problem:** `research.md` R3 specified `ack_wait ≈ 5 min` (covers Docling OCR +
enrich) with backoff. The code shipped `messaging/config.py` default
`_DEFAULT_ACK_WAIT_SECONDS = 30.0` and `client.py::nak()` with no delay/backoff. A
~1-minute Docling outage can burn all 5 deliveries and `term()` a commit into the
DLQ before recovery; slow OCR (>30s) triggers premature duplicate redelivery.

**Required fix:**
- Raise the default `ack_wait` to a production-safe ~**300s** in
  `messaging/config.py` (keep it env-overridable via `GOLDBERG_NATS_ACK_WAIT`).
- Add backoff to redelivery: either a delayed NAK (`msg.nak(delay=...)`) or a
  JetStream `backoff` schedule on the consumer config in `messaging/client.py`.
- Expose/confirm `--ack-wait` (or the env var) is documented for `ingest-serve`.
- **Tests:** assert the default ack_wait is the new value; assert nak is issued with
  a non-zero delay / that a backoff schedule is configured.

## FIX 3 (MEDIUM) — bounded catch-up can leave a silent backlog

**Problem:** `ingest/catchup.py` selects only `batch` (default 50) pending entries,
processes them, then `ingest-serve` starts live consumption. If more than `batch`
files were missed while down (hook failures, a big FF-pull), the remainder have a
manifest entry but no index/DLQ record and are NOT surfaced — the health summary
reports only the selected `pending`, not the total remaining backlog. This is a
hole in the "no silently dropped document" guarantee.

**Required fix (pick one, document it):**
- Drain ALL pending in bounded chunks before handing over to live consumption, OR
- Compute and report `remaining_pending` (total, not just this batch) in the
  catch-up report / `/health`, and mark health **degraded** when a backlog remains
  so ops know to run another `goldberg ingest catchup`.
- **Test:** manifest with more than `batch` pending → either all are processed, or
  the report/health surfaces the true remaining count (not zero).

## Out of scope for this cycle (logged as follow-ups, do NOT fix here)
- `uv run pytest -q` fails collecting `tests/test_mcp_server.py` (`No module named
  'mcp'`) — pre-existing/environmental (optional `--extra mcp`); note only.
- Enrichment tokenizes the whole doc before truncating (memory, not token, bound) —
  LOW; fine for current corpus.

## Re-run before moving back to for_review
- `uv run --extra dev pytest tests/unit/test_ingest_*.py tests/unit/test_messaging_*.py` green (incl. the new tests).
- Diff-scoped ruff exit 0 on all changed `.py`.
- Confirm `goldberg ingest-serve --help` still works.
