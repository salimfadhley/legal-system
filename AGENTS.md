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

## Skill: answering questions about the corpus

When the user asks about the **case / evidence / documents** (e.g. "who did a
given author say was responsible for X?"), **do not answer from memory** — run the
`goldberg` query tools to retrieve grounded evidence, then answer **with citations**
(`doc_id` + `raw_path` + speaker + date). Never invent; if nothing relevant is
indexed, say so.

Query the **document index** — the primary evidence — for everything. (The old
SilverBullet *concept wiki* was retired in [ADR 0014](doc/decisions/0014-retire-silverbullet-wiki.md);
there is one substrate now.)

- `uv run goldberg claims [--by <speaker>] [--subject X] [--object X]` — who
  asserted what (attributed; use for "who said" + contradiction-hunting).
- `uv run goldberg search "<keywords>" [--matter M] [--author A]` — full-text over
  the evidence documents.
- `uv run goldberg get <doc_id>` — read a document's full text to quote it.
- `uv run goldberg facets` — orient (counts by matter/author/type/party).
- Or `grep`/browse the **`goldberg-extracted`** git store — every doc as
  markdown+frontmatter (extracted text + attributed `claims`), versioned and citable.

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
`search_evidence`, `find_claims`, `get_document`. Run it with
`uv run goldberg mcp-serve` (needs `uv sync --extra mcp`); connect at
`http://<host>:8765/mcp`. Tools return structured, citable data — no shell, no
hand-written Elasticsearch queries.

## Inter-agent mail — Halob

- **My address:** `goldberg/system` — the name I answer to on this hub. The project is
  `goldberg` (the **umbrella** name: this project spans `goldberg-system`, `goldberg-raw`,
  `goldberg-extracted` and `goldberg-casework`, and same-project agents are the only ones
  who can use `goldberg/all` / `goldberg/any`); my role is `system`. Don't let it drift:
  if it ever changes, `register` again under the new name and update this line.
- **Hub:** the box is `192.168.86.31` — my MCP endpoint is
  `http://192.168.86.31:8080/goldberg/system/mcp`. That URL *is* my identity.
  - **In MCP config, use the raw IP — not `halob`, not `halob.local`.** Three names,
    three different failures:
    - bare `halob` → the network's DNS resolver (AdGuard) runs *on halob itself*, so
      when it hiccups the name stops resolving while the box is perfectly healthy. As
      of 24 Jul 2026 it returns `ENOTFOUND` outright.
    - `halob.local` → resolves fine from `curl`, `ping` and Node, but **not from the
      Claude Code binary**: it is Bun-compiled and Bun's DNS does not do mDNS, so the
      MCP client fails with `getaddrinfo ENOTFOUND halob.local` in
      `~/Library/Caches/claude-cli-nodejs/<project>/mcp-logs-agent-inbox/*.jsonl`
      while every shell tool insists the hub is up. Diagnosed 24 Jul 2026.
    - the IP → works everywhere. Only risk is DHCP moving it; if the hub goes quiet,
      re-check the address before assuming an outage.
  - `halob.local` is still fine for **shell** access (curl/ssh/browser) — the
    restriction is specifically the MCP endpoint URL.
- **On start:** read **http://192.168.86.31:8080/prompts/agent** and action it. It is the
  source of truth and may have changed since I last read it (re-read especially if
  `hub_info` → `version` differs from the version in that prompt).
- **Self-check:** if I have no agent-inbox tools (`ping`, `check_inbox`, `send_message`, …),
  I am **not** connected — tell my human, don't pretend mail works. If they've just added
  the server, the tools only load on a **session restart** — ask for one.
- **Every turn:** call `check_inbox` at the **start of the turn**. That is the whole
  mechanism — a running turn can't be interrupted, so if I don't look, I don't get mail.
- **Counterpart:** `goldberg/casework` — the legal/case agent (I stay in the technical lane
  and route legal work there). After a hub storage reset, re-verify their address with
  `list_agents` rather than trusting this line.
- **Coordinator:** `agent-inbox/host` · **problems with the hub itself:** `agent-inbox/admin`
- **Connect command** (user scope — never `--scope project`, which would commit a
  deployment-specific URL into the repo):
  `claude mcp add --transport http agent-inbox http://192.168.86.31:8080/goldberg/system/mcp --scope user`

---
*`CLAUDE.md` is a symlink to this file — one operating brief for every agent
(Claude Code, Codex, …). `AGENTS.md` is canonical.*
