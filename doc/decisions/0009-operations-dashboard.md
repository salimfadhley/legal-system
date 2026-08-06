# ADR 0009 — Operations dashboard: one `SystemState`, two renderers (M13)

**Status:** **Accepted — phases 1–2 built** · **Date:** 2026-07-21 · **Depends on:** ADR 0008 (M12)

> **Built:** the canonical `SystemState` + `aggregate()` + the LLM-readable
> `legal_system status --yaml` (phase 1), and the Streamlit UI (`legal_system dashboard`,
> phase 2, optional `--extra dashboard`). Phase 3 (deploying the UI as a Halob container)
> is not done — the dashboard is not part of the compose stack
> ([ADR 0012](./0012-deployment-topology.md)); the always-on LLM-facing surface is the MCP
> server instead ([ADR 0010](./0010-mcp-server.md)).

## Context

M12 (ADR 0008) produces the observability *data* — the event backbone, DLQ, and
reconciliation. M13 is the **live view over it**: a dashboard to see what the system
is doing right now. The user set one shaping requirement: it must have **a
human-readable mode and an LLM-readable mode** — the *same data*, one rendered for a
person (a UI), one rendered as **YAML** so an LLM (this agent, or the casework
drafting LLM) can grok the whole system state in a single read.

## Decision

### 1. One canonical `SystemState`, two renderers

The dual-mode requirement is the architecture, not a feature bolted on. A single
**`SystemState`** model (pydantic) is assembled once by an aggregator that queries the
observability sources; **both** the human UI and the LLM YAML are *renderers* of that
one object. This guarantees the two modes never drift — they are the same data.

```
        ES: goldberg_documents
        ES: goldberg_pipeline_events   ─┐
        ES: silverbullet-legal_system       ├─▶  aggregate()  ─▶  SystemState  ─┬─▶ Streamlit UI   (human)
        NATS: goldberg.dlq.* depth      ─┘                                  └─▶ YAML export     (LLM)
        goldberg-raw manifest (expected)
```

### 2. `SystemState` shape

```
SystemState:
  generated_at: ISO-8601 UTC
  health:        { status: ok | degraded | down, checks: [{name, ok, detail}] }
  pipeline:      { in_flight, per_stage_counts, throughput_1h, last_processed_at }
  dlq:           { depth, by_stage, recent: [{doc_id, stage, reason, ts}] }
  errors:        { recent: [{component, error, ts}] }
  reconciliation:{ expected, extracted, actual, missing_count,
                   missing: [raw_path…], stale_count }
  corpus:        { documents, by_matter, by_type }
  wiki:          { pages, by_layer, last_authored_at }
```

The `missing` list is the point-of-the-whole-thing: the concrete documents that
should be in the corpus but aren't.

### 3. Human mode — Streamlit on Halob

A **Streamlit** app (container on Halob, LAN-only, alongside the other services):
live pipeline activity, per-stage throughput, the **DLQ/errors panel** (what failed,
why, with *reprocess* buttons calling `legal_system dlq retry`), the **reconciliation
drill-down** (the missing-documents list), and corpus/wiki growth over time.
Auto-refreshes; read-mostly with the DLQ-retry action as the one write.

### 4. LLM mode — `SystemState` as YAML

The same `SystemState`, serialised to compact **YAML**, exposed two ways:

- **`legal_system status --yaml`** — a CLI one-shot the agent runs to read system health
  in a single call (usable immediately, even before the Streamlit app exists).
- The Streamlit app serves the same YAML (a download / `?format=yaml` view) so a
  human and an LLM are looking at identical state.

YAML (not JSON) is deliberate: it is the most token-efficient, least-punctuated
structured format for an LLM to skim, matching how the corpus metadata is already
represented (frontmatter, ADR 0004). The agent can answer "is the system healthy?
what failed? what's missing?" from one `legal_system status --yaml`.

### 5. No new telemetry

M13 **only renders** M12's data — it never generates events or reconciliation itself.
`aggregate()` is pure read (ES + NATS depth + manifest). This keeps the dashboard
disposable/regenerable and the data model owned by M12.

## Consequences

- Build order: `SystemState` + `aggregate()` + `legal_system status --yaml` come first
  (small, immediately useful to the agent), then the Streamlit UI on top.
- `legal_system status --yaml` doubles as the LLM's system-health probe from the casework
  workspace — the drafting LLM can check corpus completeness before relying on it.
- New: a `dashboard`/`state` module (aggregator + `SystemState` + YAML renderer) and a
  Streamlit app + its Halob container. Depends on the M12 ES projection + DLQ.

## Phases (M13)

1. **`SystemState` + `aggregate()` + `legal_system status --yaml`** — the canonical model
   and the LLM-readable mode (usable the moment M12 core lands).
2. **Streamlit UI** — the human mode over the same model, with DLQ reprocess actions.
3. **Deploy** — Halob container, LAN-only, auto-refresh.
