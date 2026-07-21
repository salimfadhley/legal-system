# Architecture

## Why four repositories

The predecessor repo (`the_goldberg_files`) mixed three things that have completely different lifecycles: original data, machine-extracted data, and code. The platform separates them, so each can be versioned, secured, and regenerated independently.

| Repo | Contents | Lifecycle |
|---|---|---|
| **goldberg-system** | pipeline + tooling (this repo) | versioned software; tests; may be shared |
| **goldberg-raw** | original documents (PDF, .eml, docx, images) | **immutable**, private, legally sensitive |
| **goldberg-extracted** | markdown + metadata + summaries derived from raw | **regenerable**, disposable, machine-written |
| **goldberg-casework** | briefings, applications, analysis, legal research | **irreplaceable** human work product |

The predecessor `the_goldberg_files` is **frozen** as a rollback / archive point and is never modified. All paths are recorded in [`config/projects.yaml`](../config/projects.yaml).

```
                       ┌────────────────────────┐
   push (originals) →  │      goldberg-raw      │  immutable source of truth
                       └───────────┬────────────┘
                                   │ trigger (hook/watcher on Halob → NATS)
                                   ▼
                       ┌────────────────────────┐
                       │     goldberg-system    │  the pipeline (this repo)
                       │  extract → enrich →     │
                       │  index → wiki           │
                       └───────────┬────────────┘
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
   ┌────────────────────┐  ┌────────────────────┐
   │ goldberg-extracted │  │   Elasticsearch    │
   │ (frontmatter docs) │  │ goldberg_documents │
   └────────────────────┘  └─────────┬──────────┘
                                     │
                    query layer — `goldberg` CLI (search / claims / get / facets)
                                     │
                    an agent (Claude Code) synthesises a cited, attributed answer

   goldberg-casework  ── authored work product (not shown in the data flow)
```

Extraction is offloaded to the already-deployed **Papra** DMS (backed by a
self-hosted **Docling** server) — see [ADR 0003](decisions/0003-document-management-papra-integration.md).
The RAG/query surface is **RAG-on-Elasticsearch** ([ADR 0001](decisions/0001-wiki-rag-sink-backend.md)),
not a separate wiki. Each extracted document is a markdown-with-frontmatter file
([ADR 0004](decisions/0004-metadata-representation.md)).

## Where it runs — Halob

Everything runs on **Halob** (home NAS, `192.168.86.31`). Already-running infrastructure we reuse: Elasticsearch (`:9200`), NATS (`:4222`), Docker via Portainer, Copyparty/Syncthing (file drop), Obsidian (candidate wiki). See [servers/halob.md](servers/halob.md).

## Code reuse — Mind of Steele

The pipeline does **not** reinvent the hard parts. Mind of Steele (`~/workspace/mind_of_steele`) already runs this exact pattern for its video archive and supplies:

- `common/llm_support.py` — `generate_video_summary()`, `extract_keywords_from_summary()`, `format_summary_with_yaml_header()`
- `elasticsearch/` — `indexer.py` + `chunker.py` + `summary_parser.py`
- `ragie_uploader/` — RAG sink
- `live_summarize/main.py` — the NATS-driven service loop (template for our `live-index` service)

MoS lives on the Mac, not Halob, so it is referenced (`reuse.mind_of_steele` in projects.yaml) and will be resolved as a git source or vendored package when the pipeline modules are wired in — not copied.

## Cross-repo references

Because authored work (casework) and evidence (raw/extracted) live in separate repos, **do not cross-reference by filesystem path across repos**. Two supported mechanisms:
1. All four repos are checked out under a **common parent** (`/Volumes/Home/work/project_goldberg`), so a shared-root path resolves.
2. Preferred for durable links: reference documents via **Elasticsearch doc-ids / wiki links**, so casework points at evidence through the index, not the filesystem.

## Open decisions

- **Wiki sink**: Ragie (managed RAG, reuse MoS uploader) vs Obsidian vault vs RAG-on-Elasticsearch. Built behind an interface; backend chosen later.
- **Trigger locus**: Halob-local hook/watcher on the `goldberg-raw` clone (recommended) vs GitHub webhook → Halob receiver (needed only if content is committed off-Halob).
- **Large binaries** in `goldberg-raw`: plain git vs git-LFS for large PDFs/media.
