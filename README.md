# goldberg-system

Code and tooling for the **Goldberg document analysis platform** — the pipeline that turns raw legal documents into a searchable, LLM-queryable knowledge base.

This is one of four repositories (plus a frozen predecessor). See [`doc/architecture.md`](doc/architecture.md) for the full topology and [`doc/workflow.md`](doc/workflow.md) for what happens to a document when it is ingested.

## The four repositories

| Repo | Role |
|---|---|
| **goldberg-system** (this) | The pipeline & tooling: ingest → extract → summarize → index → wiki |
| **goldberg-raw** | Immutable original documents; the push that triggers the pipeline |
| **goldberg-extracted** | Machine-generated markdown + metadata (regenerable artifact store) |
| **goldberg-casework** | Authored work product: briefings, applications, analysis, research |

The predecessor repo `the_goldberg_files` is **frozen** as an archive / rollback point and is not modified.

Locations of all repos and services are recorded in [`config/projects.yaml`](config/projects.yaml) — the single source of truth for cross-project paths.

## Quick start (uv)

```bash
uv sync                       # create the environment
uv run pytest                 # run the tests (TDD is mandatory here)
uv run goldberg config        # print the resolved project/service locations
```

## Where things run

Everything runs on **Halob** (the home NAS, `192.168.86.31`): Elasticsearch (`:9200`), the NATS bus (`:4222`), Docker/Portainer. See [`doc/servers/halob.md`](doc/servers/halob.md).

## Reuse

The heavy lifting (LLM summary + keyword extraction, Elasticsearch chunk+index, RAG upload) is reused from **Mind of Steele** rather than reinvented — see `reuse.mind_of_steele` in `config/projects.yaml` and the reuse notes in `doc/architecture.md`.

## Governance

This project is managed with **Spec Kitty** (`.kittify/`, `AGENTS.md`). Mission-style work goes through `/spec-kitty.specify`; the files under `doc/` are the human-facing design record and are maintained directly.

---
*See [`doc/index.md`](doc/index.md) for the full documentation index, and [`doc/resume/`](doc/resume/) for session hand-off prompts.*
