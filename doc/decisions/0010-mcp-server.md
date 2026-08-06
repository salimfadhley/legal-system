# ADR 0010 — Hosted MCP server: LLM-native visibility + query (M14)

**Status:** Accepted (built + tested) · **Date:** 2026-07-21

## Context

The primary human is rarely at a dashboard; they ask an LLM "how's indexing going /
any bottlenecks / has anything failed?" and "what does the evidence say about X?".
The system must therefore be legible and queryable **by a fleet of LLM agents**
(Claude Code, Codex, chat models), not just via a CLI a human reads.

## Decisions

1. **One hosted MCP server, two tool families, over the existing core.** MCP is the
   emerging cross-agent standard (Claude *and* Codex are MCP clients). A single
   server exposes **observability** (`system_status`, `recent_failures`,
   `trace_document`) and **evidence query** (`search_evidence`, `find_claims`,
   `search_concepts`, `get_document`). Every tool is a thin, read-only wrapper over
   `aggregate()` / `CorpusQuery` / `read_trace` — the same functions behind the CLI —
   so there is no second implementation to drift.

2. **Tools expose *intents*, the server owns the *mechanism*.** The model passes a
   natural query + typed filters and gets structured, citable JSON back. It never
   writes an Elasticsearch query and never runs a shell command. **No `run_es_query`
   or `run_shell` escape-hatch tool** — that would push mechanism onto the model and
   is a security footgun. Read-only throughout (writes stay in the CLI/pipeline).

3. **Hosted transport (`streamable-http`).** Runs as one always-on endpoint any
   number of agents connect to (`http://<host>:8765/mcp`), rather than a per-agent
   stdio process. Tested locally; deploys to the Pi (external vantage) or Halob,
   wherever performance is best. Optional `mcp` dependency extra so only the host
   running it installs the SDK.

4. **Agent-agnostic by construction.** `AGENTS.md` (OpenAI's standard, read by Codex)
   is the canonical operating brief; `CLAUDE.md` is a symlink to it. The CLI + MCP
   server are Claude-neutral, so Codex and other agents are first-class.

## Consequences

- New `goldberg_system.mcp` package + `goldberg mcp-serve`; deps behind `--extra mcp`.
- Same guarantees as the CLI (citable provenance, no invention) but as structured
  tool I/O — better for LLMs (no text-parsing, schema-guided params, no shell).
- Deployment (Pi/Halob container) is the remaining step; the transport is already
  network-hosted.

## Validation (2026-07-21)

Started locally over `streamable-http`; an MCP client completed the `initialize`
handshake, listed all 7 tools, and called them live: `system_status` → health ok /
191 docs; `find_claims(speaker=<a named speaker>)` → 14 attributed claims;
`search_evidence(<a named organisation>)` → live hits. End-to-end confirmed.
