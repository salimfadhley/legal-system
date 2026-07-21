<!-- spec-kitty:orientation -->
**Spec Kitty v3.2.5** — project: unknown (healthy)

Two usage patterns:
- **Full mission** (spec → plan → tasks → implement → review → merge):
  trigger: "spec out", "create a mission", "write a spec", "plan this"
  → run `/spec-kitty.specify`
- **Lightweight dispatch** (ad-hoc fix, question, or advice — no mission created):
  trigger: "hey spec kitty", "use spec kitty to", "spec kitty <anything>"
  → **ALWAYS run `spec-kitty dispatch "<request verbatim>"` — do NOT answer directly.**
  If you know the right profile, pass it to skip routing:
  `spec-kitty dispatch "<request verbatim>" --profile <profile-id>`
  Reason: `spec-kitty dispatch` loads governance context, routes the request,
  and opens the Op. Skipping it produces ungoverned, untracked responses.
  After finishing the work, close the Op with the command printed in the capsule
  (`spec-kitty profile-invocation complete --invocation-id <id> --outcome <done|failed|abandoned>`).
<!-- /spec-kitty:orientation -->

## Skill: answering questions about the Goldberg corpus

When the user asks about the **case / evidence / documents** (e.g. "who did
Goldberg say was the prosecuting entity?"), **do not answer from memory** — run the
`goldberg` query tools to retrieve grounded evidence, then answer **with citations**
(`doc_id` + `raw_path` + speaker + date). Never invent; if nothing relevant is
indexed, say so.

The corpus has **two representations — query both**: the *document index*
(primary evidence) and the *concept wiki* (synthesised, cross-linked pages). They're
complementary — the wiki gives you the map (who's who, how they connect); the
documents give you the quotable primary source.

- `uv run goldberg claims [--by <speaker>] [--subject X] [--object X]` — who
  asserted what (attributed; use for "who said" + contradiction-hunting).
- `uv run goldberg search "<keywords>" [--matter M] [--author A]` — full-text over
  the evidence documents.
- `uv run goldberg wiki "<keywords>" [--layer entity|concept|comparison] [--tag T]`
  — search the **concept wiki** (synthesised entity/concept/contradiction pages).
- `uv run goldberg get <doc_id>` — read a document's full text to quote it.
- `uv run goldberg facets` — orient (counts by matter/author/type/party).

**Full instructions: [`doc/runbooks/querying-the-corpus.md`](doc/runbooks/querying-the-corpus.md).**

## Skill: reporting on the system itself (observability)

When asked "how's indexing going / any bottlenecks / has anything failed?", use the
observability commands (M12, [ADR 0008](doc/decisions/0008-observability-architecture.md)):

- `uv run goldberg status [--yaml]` — health, corpus size, per-stage/status counts,
  DLQ depth (`--yaml` is the compact LLM-readable form).
- `uv run goldberg dlq` / `goldberg trace <raw_path|sha256|doc_id>` — what failed /
  why a specific document did (not) ingest.
- `uv run goldberg audit --manifest <manifest.json>` — completeness: did anything
  not ingest.

## MCP server (agent-agnostic, no shell needed)

The same observability + query capabilities are exposed as a **hosted MCP server**
([ADR 0010](doc/decisions/0010-mcp-server.md)) for any MCP-capable agent (Claude,
Codex, …): tools `system_status`, `recent_failures`, `trace_document`,
`search_evidence`, `find_claims`, `search_concepts`, `get_document`. Run it with
`uv run goldberg mcp-serve` (needs `uv sync --extra mcp`); connect at
`http://<host>:8765/mcp`. Tools return structured, citable data — no shell, no
hand-written Elasticsearch queries.

---
*`CLAUDE.md` is a symlink to this file — one operating brief for every agent
(Claude Code, Codex, …). `AGENTS.md` is canonical.*
