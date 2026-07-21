"""The goldberg MCP server (M14) — LLM-native visibility + query surface.

A hosted MCP server that gives any MCP-capable agent (Claude, Codex, …) two
capabilities over the *same* core the CLI uses, with no shell and no hand-written
Elasticsearch queries:

- **Observability** — "how's indexing going / any bottlenecks / anything failed?"
- **Evidence query** — search the corpus, find attributed claims, read documents.

Tools expose *intents* (typed params, structured returns); the server owns the
mechanism. Read-only. Run hosted (``streamable-http``) locally, then on the Pi/Halob.
"""
