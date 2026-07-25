# Architecture — the Goldberg document analysis platform

**This document is the canonical technical description of the system.** It is written
so that someone with no access to the running deployment could rebuild an equivalent
system from it: what the components are, how they connect, what the contracts between
them are, and — deliberately — **why each significant choice was made**, including the
options that were tried and rejected. Where an experiment settled a question, the
result is recorded here so a rebuilder does not have to repeat it.

The dated decision records in [`decisions/`](./decisions/) remain the evidence: each
ADR captures one decision at the moment it was taken, including options considered and
spike results. This document is the *current* state; where the two disagree, this
document wins and the ADR is the history. An index of every ADR with its live status is
at the end ([§16](#16-decision-record-index)).

**Status of this document: current as of 2026-07-24.** The system is deployed and
running on Halob. Section [§14](#14-known-gaps-and-honest-limitations) lists what is
*not* built, so nothing here reads as more complete than it is.

---

## Contents

1. [What the system is for](#1-what-the-system-is-for)
2. [The shape of the system in one page](#2-the-shape-of-the-system-in-one-page)
3. [External dependencies — and why each one](#3-external-dependencies--and-why-each-one)
4. [Repository topology — why four repos](#4-repository-topology--why-four-repos)
5. [The ingestion path (write side)](#5-the-ingestion-path-write-side)
6. [Why trigger, not poll](#6-why-trigger-not-poll)
7. [Why Docling — and not the alternatives](#7-why-docling--and-not-the-alternatives)
8. [The provenance model](#8-the-provenance-model)
9. [The data model](#9-the-data-model)
10. [Enrichment](#10-enrichment)
11. [The query path (read side)](#11-the-query-path-read-side)
12. [Observability](#12-observability)
13. [Deployment topology](#13-deployment-topology)
14. [Known gaps and honest limitations](#14-known-gaps-and-honest-limitations)
15. [Rebuilding this system from scratch](#15-rebuilding-this-system-from-scratch)
16. [Decision record index](#16-decision-record-index)

---

## 1. What the system is for

The platform is a **defence research and drafting tool for a live UK private
prosecution** in which the system's owner is one of the defendants. That purpose drives
almost every technical decision below, so it is worth stating precisely. The system has
two jobs:

1. **Answer attributed questions across an evidence corpus.** Not "what does the corpus
   say about X" but "*who said* what about X, *when*, and *in which document*". Every
   answer must cite the source document, the commit of the original file, the speaker,
   and the date, because the output is destined for a court.
2. **Surface contradictions.** Where a party's account of the same fact shifts between
   documents, that shift is argument material. This requires comparing **claims** across
   documents, not just retrieving text.

Two consequences follow that a general-purpose document search system would not have:

- **Completeness is a correctness property, not an ops metric.** A document that
  silently fails to ingest is invisible: it is absent from every answer, and nothing
  indicates the absence. An answer built on a corpus with an unknown hole is worse than
  no answer. This is why the system has a provenance manifest, a dead-letter queue, a
  reconciliation audit, and a rule that a document is never acked as processed unless it
  reached a terminal state ([§8](#8-the-provenance-model), [§12](#12-observability)).
- **Provenance is load-bearing.** "This document says X" is not usable unless it can be
  traced to an exact, unmodified original with a verifiable history. This is why the
  original files live in a git repository that is never written to by the pipeline, and
  why every derived artifact carries `raw_path` + `raw_commit` + `raw_sha256`.

A third property is a legal-handling requirement: metadata fields that encode legal
judgments (CPIA s.17 material, privilege, disclosure status) are **human-authored and
default to the most protective value**. The LLM must never invent them
([§9](#9-the-data-model)).

---

## 2. The shape of the system in one page

```
   ┌─────────────────┐
   │  goldberg-raw   │  git repo, immutable originals. THE system of record.
   │   (git + LFS)   │  A human/agent commits a file here. That is the only manual step.
   └────────┬────────┘
            │  git post-commit / post-merge hook
            │  → `goldberg publish-commit <sha>`
            ▼
   ┌─────────────────────────────────────────┐
   │ NATS JetStream   stream GOLDBERG        │  durable, survives processor downtime
   │ subject goldberg.raw.commit  {sha,...}  │
   └────────┬────────────────────────────────┘
            │  durable pull consumer "ingest-processor"
            ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │ ingest service  (`goldberg ingest-serve`)                            │
   │                                                                      │
   │  1. refresh provenance   → config/provenance-manifest.json           │
   │     (sha256 → raw_path, raw_commit, matters)   *** BEFORE indexing ***│
   │  2. git-resolve the commit's changed, allowlisted files              │
   │  3. per file:  EXTRACT ──▶ ENRICH ──▶ write to SINKS                 │
   │     ack only when every file is terminal; else nak → retry → DLQ     │
   └───┬───────────────┬──────────────────────────────┬───────────────────┘
       │               │                              │
       ▼               ▼                              ▼
  ┌─────────┐   ┌──────────────┐              ┌────────────────────┐
  │ Docling │   │ OpenAI       │              │ Sinks              │
  │ (OCR /  │   │ gpt-4o-mini  │              │  • Elasticsearch   │ ← the queryable corpus
  │ layout) │   │ claims+summary│             │    goldberg_documents
  └─────────┘   └──────────────┘              │  • goldberg-extracted (md+frontmatter)
                                              │  • concept wiki (partially built, §14)
                                              └────────────────────┘
                                                       │
      pipeline events (every stage) ──▶ ES goldberg_pipeline_events ──▶ status/trace/dlq/audit
                                                       │
                                                       ▼
   read side:  `goldberg` CLI  ·  MCP server (:8765/mcp, 8 tools)  ·  Streamlit dashboard
               an LLM agent queries these and synthesises a CITED answer
```

**The one-sentence version:** a commit to a git repo of original documents publishes an
event to NATS; a durable consumer records provenance, OCRs the changed files through
Docling, enriches them into attributed claims with an LLM, and indexes them into
Elasticsearch, where a CLI and an MCP server let an agent answer questions with
citations.

---

## 3. External dependencies — and why each one

The system deliberately owns very little infrastructure. Four external services do the
heavy lifting; all are self-hosted except the LLM.

| Dependency | Version / image | Role | Why this one |
|---|---|---|---|
| **Elasticsearch** | 8.x (client pinned `>=8,<9`) | The queryable corpus, the pipeline-event log, and the wiki index | See below |
| **NATS + JetStream** | NATS 2.x with JetStream enabled | The ingest trigger bus; durability across processor downtime | See below |
| **Docling** (`docling-serve`) | `ghcr.io/docling-project/docling-serve-cpu:latest`, port 5001 | PDF/scan/docx → markdown with layout and table awareness | [§7](#7-why-docling--and-not-the-alternatives) |
| **OpenAI** | `gpt-4o-mini` via the official SDK | Enrichment: summary, entities, attributed claims | [§10](#10-enrichment) |

Optional / peripheral: **SilverBullet** (the concept-wiki space, indexed as
`silverbullet-goldberg`); **Papra** (a document-management app, now off the ingest path
— see [§7](#7-why-docling--and-not-the-alternatives)); **Streamlit** (the operations
dashboard, an optional install extra).

### Why Elasticsearch

The requirement that settled this was not full-text search — several things do that —
but **claim-level querying across the whole corpus**, which is what contradiction
detection needs. Three options were weighed ([ADR 0001](./decisions/0001-wiki-rag-sink-backend.md)):

- **Elasticsearch (chosen).** Full control of the document schema, so citations map
  exactly to our own provenance fields. Critically, `claims` is indexed as a **nested**
  type, so "who asserted what about whom" is a real query with per-claim field matching,
  and claims are aggregatable across documents. It was already running on the host, and
  it keeps the retrieval index on-network.
- **Ragie (managed RAG).** Lowest build effort, and a working uploader already existed
  in a sibling project. Rejected because managed chunking is a black box: attribution
  and provenance-to-commit fidelity degrade, and **cross-document claim comparison is
  not expressible as a query at all**. It also means exporting the entire corpus of a
  live legal case to a third party.
- **An Obsidian vault.** Rejected on a category error: it is a browse surface, not a
  retrieval engine. It cannot serve programmatic attributed Q&A.

The cost accepted: we build the retrieval and citation layer ourselves rather than
plugging into a managed API. That is the right trade when attribution fidelity is the
product.

Three indices are used:

| Index | Contents |
|---|---|
| `goldberg_documents` | The corpus: one document per raw file, with metadata, content, and nested claims |
| `goldberg_pipeline_events` | Every stage event for every document — the audit trail and the basis of `status`/`trace`/`dlq` |
| `silverbullet-goldberg` | The synthesised concept-wiki pages (maintained by a sibling project's indexer) |

A legacy index `goldberg_files` (~589 documents) exists from the predecessor system. It
is **not** used by this pipeline; its content was re-ingested into `goldberg_documents`
with real provenance. Do not confuse the two — older documents still refer to
`goldberg_files`.

### Why NATS JetStream

NATS carries exactly one thing on the ingest path: the `goldberg.raw.commit` trigger.
The reason it is a message bus and not, say, an HTTP call from the git hook is
**durability**:

- A **durable pull consumer** means an event published while the ingest service is down
  is delivered when the service returns. An HTTP POST to a dead service is simply lost.
- **Nak/redelivery** gives at-least-once processing with a bounded retry count, and
  `max_deliver` gives a well-defined point at which a message becomes a dead letter
  rather than retrying forever.
- **The publisher must never block the producer.** The git hook is fire-and-forget: if
  the broker is unreachable, the commit still succeeds and the trigger is simply lost —
  recovered by the startup catch-up. A synchronous, failure-propagating trigger would
  mean a broker outage breaks `git commit`, which is unacceptable.

NATS was also already running as shared infrastructure on the host, so this added no new
stateful component.

**The messaging contract** (defaults in `messaging/config.py`; keep these stable, other
services bind to them):

| Setting | Default | Env override |
|---|---|---|
| Broker URL | `nats://192.168.86.31:4222` | `NATS_URL` |
| Stream | `GOLDBERG` (captures `goldberg.>`) | `GOLDBERG_NATS_STREAM` |
| Commit subject | `goldberg.raw.commit` | `GOLDBERG_NATS_COMMIT_SUBJECT` |
| Durable consumer | `ingest-processor` | `GOLDBERG_NATS_DURABLE` |
| Max deliveries | `5` | `GOLDBERG_NATS_MAX_DELIVER` |
| Ack wait | `300s` | `GOLDBERG_NATS_ACK_WAIT` |
| Dedup window | `120s`, keyed on `Nats-Msg-Id` = commit sha | `GOLDBERG_NATS_DEDUP_WINDOW` |

**The 300-second ack-wait is a scar, not a guess.** It was originally 30s. A slow
Docling OCR of a large scanned PDF routinely exceeds that, so the server redelivered
messages that were *still being processed* — producing duplicate work and spurious
dead-letters under entirely normal conditions. Any rebuild must set the ack wait
comfortably above the worst-case single-document extract-plus-enrich time.

---

## 4. Repository topology — why four repos

The predecessor project mixed three things with completely different lifecycles in one
repository: original data, machine-extracted data, and code. Separating them is the
first architectural decision.

| Repo | Contents | Lifecycle | Written by |
|---|---|---|---|
| **goldberg-system** | Pipeline + tooling (this repo) | Versioned software; tested; shareable | Humans/agents |
| **goldberg-raw** | Original documents (PDF, `.eml`, docx, images) | **Immutable**, private, legally sensitive | Humans/agents only — *never* the pipeline |
| **goldberg-extracted** | Markdown + metadata derived from raw | **Regenerable**, disposable, machine-written | The pipeline |
| **goldberg-casework** | Briefings, applications, analysis, legal research | **Irreplaceable** human work product | Humans/agents |

Why this matters beyond tidiness:

- **`goldberg-raw` is the system of record and the provenance anchor.** Because the
  pipeline never writes to it, there is no path by which machine processing can corrupt
  the evidence. Every derived artifact points *back* at it. The corpus in Elasticsearch
  is a **derived, rebuildable artifact** — which is precisely what makes the processing
  stack portable ([§13](#13-deployment-topology)).
- **`goldberg-extracted` is regenerable**, so it can be committed in batches or thrown
  away and rebuilt. It exists for human browsing and as a mirror, not as a source.
- **One-way flow.** Writes to `goldberg-extracted` do not trigger the pipeline. There is
  no two-hop, no loop.

**Large binaries** in `goldberg-raw` use **git-LFS selectively**, configured via
`.gitattributes` ([ADR 0002](./decisions/0002-large-binary-handling.md)): large binary
types (PDF, images, archives) go to LFS; text originals (`.eml`, `.md`) stay in plain
git where they are diffable and cost no quota. Plain-git-everything was rejected because
GitHub hard-rejects any file over 100 MB; all-LFS was rejected because it burns quota on
small text files. The working tree still contains real (smudged) files, and each raw
file still has a commit sha, so provenance is unaffected.

**Audio and video are excluded from the raw repo entirely** (`exclude_globs` in
`config/evidence-allowlist.yaml`). They carry no OCR-able text and dominated repository
size (4.7 GB, ~80%). Originals remain in the frozen archive.

**Cross-repo references must not be filesystem paths.** Casework cites evidence through
the index — by `doc_id` / `raw_path` — not by relative path across repo boundaries.

### The frozen predecessor and the allowlist

The predecessor repo is **frozen** and never modified. Migrating it into `goldberg-raw`
was not a copy: a spike found that of 5,018 files, only 27% resolved to a legal matter,
and the remainder were overwhelmingly *not evidence* — scratch directories (1,996 files
in `tmp/`), source code, build output, and 914 files of authored work product that
belong in `goldberg-casework`.

The lesson, which any rebuilder inheriting a legacy corpus should take: **migration is
allowlist-driven, not "copy everything".** `config/evidence-allowlist.yaml` names the
trees that migrate (`evidence`, `telegram`, `goldberg_1099`, `exhibits`,
`downloaded-artifacts`, `reports`, `analysis`), the trees explicitly excluded with
reasons, and the file globs never migrated. The same allowlist then governs what the
live ingest path will look at, so the two can never drift.

---

## 5. The ingestion path (write side)

### 5.1 The contract with the human (or agent)

The single most important operational rule: **the person or agent adding a document does
not convert, summarise, or index anything by hand.** They:

1. Write the **original, unmodified** file into `goldberg-raw/…` at a conventional path.
2. Add a folder-level `metadata.yaml` capturing provenance the machine cannot infer —
   in particular the legal-handling flags and the matter.
3. `git add` → `git commit`.
4. Stop. Everything downstream is automatic.

### 5.2 Trigger: git hook → NATS

`goldberg-raw` clones set `core.hooksPath` to this repo's versioned
[`hooks/`](../hooks/) directory. Two hooks are installed:

- **`post-commit`** — fires for ordinary commits.
- **`post-merge`** — fires for non-fast-forward pull-merges, which `post-commit` does
  *not* cover.

Both run `goldberg publish-commit <sha> --source <hook>`, which publishes one JSON
message `{sha, ts, source}` to `goldberg.raw.commit`, with `Nats-Msg-Id` set to the
commit sha so the stream's dedup window collapses duplicate publishes.

**The hooks never fail `git`.** No `set -e`; every path exits 0; a failure is logged via
`logger` and swallowed. A broker outage costs a trigger, not a commit — and a lost
trigger is recovered by the startup catch-up. This is a deliberate inversion of the
usual "fail loudly" instinct: the *evidence repository* must remain usable even when the
pipeline is entirely down.

**A known gap, stated plainly:** a *fast-forward* `git pull` fires neither hook. Such
changes are picked up by the startup catch-up or a manual `goldberg ingest catchup`.

### 5.3 Transport: the durable consumer

The ingest service binds a durable pull consumer (`ingest-processor`) to the commit
subject. Delivery semantics, which are the heart of the "no silently dropped document"
guarantee:

| Outcome | Action | Rationale |
|---|---|---|
| Every file in the commit reached a terminal state | **ack** | Work is done |
| A commit resolved to **zero** allowlisted files | **ack** | A genuine empty result is a success |
| Any file failed transiently (Docling down, sink write failed) | **nak** | Redeliver and retry |
| The commit **could not be resolved at all** (unknown sha, `.git` problem) | **nak**, never ack | See below |
| Redeliveries reached `max_deliver` (5) | **term** + emit a `failed` DLQ event | Bounded retry |
| The message is unparseable (poison) | **term** + DLQ event | Never redeliver garbage |

The distinction between "resolved to zero files" and "could not resolve" is
load-bearing and was validated in production. During the live cutover, the container
(running as root) could not run `git` on the host-owned bind mount — git refused with
"detected dubious ownership". Commit resolution raised, the processor **nak'd rather
than acking an empty result**, and *no document was dropped*. An implementation that
treated an unresolvable commit as "nothing to do" would have silently lost evidence.
(The fix is `git config --system --add safe.directory /data/goldberg-raw`, baked into
`deploy/Dockerfile.ingest`.)

Blocking work runs off the event loop (`asyncio.to_thread`) so the NATS connection keeps
servicing heartbeats during a long extraction. The loop never dies: a per-commit
exception is logged and consumption continues.

### 5.4 Startup catch-up: one bounded pass, never a loop

On start (unless `--no-catchup`) the service runs **exactly one** bounded catch-up pass
to close any gap opened by downtime, a missed hook, or a fresh deploy:

1. **Refresh provenance** — walk the allowlisted trees; for every file whose SHA-256 is
   not yet in the manifest, register an entry (sha256 + git `raw_commit` + `matters` /
   `document_type` / `origin` from the `metadata.yaml` chain) and persist the manifest
   atomically. Only genuinely-new hashes trigger a git-commit lookup, bounding the cost.
2. **Compute the resume set** — the `raw_sha256` values already in the index.
3. **Select the bounded difference** — not-yet-indexed, non-media entries, capped at
   `--batch` (default 50).
4. **Process** them through the same extract → enrich → index path, under a
   `catchup-<ts>` run id.

Then it goes idle and waits for events. **This is not polling**: it happens once, at
boot. The distinction is the entire point of [§6](#6-why-trigger-not-poll).

Because the pass is bounded, a backlog larger than one batch would otherwise be
invisible. So the report also computes the **true unbounded pending count** and exposes
`remaining_pending`; a non-zero value marks `/health` **degraded**, prompting another
`goldberg ingest catchup`. Silence would have been the failure mode; visible degradation
is the fix.

### 5.5 Per-document processing

For each changed, allowlisted file (`migrate/reingest.py :: process_one`):

```
already indexed (sha in resume set)?          → skipped-indexed   (no re-extraction)
media extension (.mp4/.mp3/…)?                → skipped-media
file missing from the working tree?           → "missing"  (transient → retry; may be an unpulled LFS object)
    ↓
EXTRACT via Docling  →  DoclingError          → extract-failed (transient → retry → DLQ)
    ↓
extraction empty?                             → skipped-empty  (terminal, not a failure)
    ↓
ENRICH (OpenAI) + merge with manifest provenance
    ↓
WRITE to every sink; all ok?                  → indexed
                            any sink failed?  → sink-failed (transient → retry)
```

Terminal-OK statuses are `indexed`, `skipped-indexed`, `skipped-media`, `skipped-empty`.
Everything else is treated as transient and retried. Note the deliberate asymmetry:
**an empty extraction is terminal, but an extraction failure is not** — so a document
that fails only because Docling is down is retried when Docling returns, instead of
being permanently written off.

Documents are processed concurrently (`--workers`, default 2 — the host is 4-core) since
the work is I/O-bound on Docling, OpenAI, and Elasticsearch.

---

## 6. Why trigger, not poll

This is one of the two choices most worth understanding before rebuilding, because the
system went through **three** trigger designs and the reasoning is not obvious from the
final state.

### The history

**v1 — Papra webhook** ([ADR 0005](./decisions/0005-live-service-webhook-driven.md), retired).
A document-management app (Papra) ingested files, extracted them, and fired a
`document:created` webhook; the service enriched and indexed from that. It worked and it
was the fewest moving parts. It was **architecturally wrong**: it indexed off the DMS's
extraction *before* registering git provenance, so documents landed with no `raw_commit`
and no `matters` — the DMS's filename stood in for `raw_path`. The system of record had
been quietly delegated to a tool with no concept of a commit.

**v2 — polling reconciler** ([ADR 0011](./decisions/0011-auto-ingestion-reconciler.md), retired).
The provenance model was corrected: git-raw is the system of record, a manifest maps
sha256 → provenance, extraction goes directly to Docling. But nothing was left watching
the raw tree, so a dropped file sat unnoticed until a human ran a command. The fix was a
daemon that every 300s re-hashed the whole allowlisted tree, diffed it against what was
already indexed, and ingested the difference. Robust, idempotent, provenance-first — and
wasteful in a specific way.

**v3 — git-commit → NATS → durable consumer** ([ADR 0013](./decisions/0013-event-driven-ingestion.md), current).

### Why polling was replaced

- **The steady state was a re-hash storm.** On a quiescent corpus, the reconciler's
  full-time job was SHA-256-ing every allowlisted file, forever, to discover that
  nothing had changed. The cost scaled with **corpus size**, not with **change rate** —
  exactly backwards. Over an SMB-mounted NAS this is a recurring I/O storm.
- **There was a latency floor.** A dropped file waited up to a full interval before
  ingestion even began, and the only way to reduce that was to make the storm worse.
- **An exact signal already existed.** `goldberg-raw` is a git working tree. A commit is
  the authoritative statement that content changed, and it *already enumerates precisely
  which files changed*. No scan is needed to find them. Polling was re-deriving, at
  great expense, information git had already recorded for free.

### Why not filesystem events (inotify/FSEvents)?

This was considered twice and rejected twice. `goldberg-raw` lives on an **SMB-mounted
NAS**, where change-notification delivery is unreliable. Events are silently missed —
and a silently missed event in a legal corpus is an invisible hole in the evidence, the
one failure mode the whole design is built to prevent. A watcher would have been cheaper
than polling and just as event-driven; it was rejected on **reliability of the
notification channel**, not on cost.

### Why the result is safe

The event-driven design keeps the reconciler's robustness without its cost, by combining
three mechanisms:

| Mechanism | Covers |
|---|---|
| Git hook → NATS | The normal case: ingest begins on commit, latency in seconds |
| **Durable** consumer | The service being *down* when the event fires — it is delivered on return |
| One-shot startup catch-up | A *lost* trigger (broker outage, hook not installed, fast-forward pull) |

A quiescent corpus costs **nothing**: the service blocks on the consumer. Work happens
only when a commit says there is work, and touches only that commit's changed files.

**The trade accepted:** ingestion now depends on the hook being wired
(`core.hooksPath`). A clone without it silently stops triggering. That risk is mitigated
— not eliminated — by the startup catch-up and by `goldberg audit`, which both surface
the resulting backlog.

---

## 7. Why Docling — and not the alternatives

The other choice most worth understanding. The question is: how do you turn a scanned
legal PDF into markdown that an LLM can reason over?

### What was surveyed

A research spike surveyed Paperless-ngx, Docspell, Docling, Marker, `unstructured`,
Apache Tika, LlamaParse, and managed cloud extraction APIs, plus the hand-rolled route
(`pdftotext` + Tesseract + `pandoc` + a readability extractor + passthrough dispatch by
MIME type), which was the original plan.

### Why not a hand-built extractor set

The original roadmap scoped six bespoke extractors. Before building them the question
asked was whether existing software already automated this — and one such tool (Papra)
was *already deployed* on the host with an org created for this case. Building six
extractors to duplicate deployed, working software is waste. The extractor set shrank to
the genuine gaps.

### Why not a full DMS as the system of record

Papra was adopted as an ingest/OCR front end, **not** as the system of record. The
reason is structural and worth generalising: **a content-addressed document store has no
concept of a commit.** It identifies documents by SHA-256, which is excellent, but it
cannot carry the provenance pair (`raw_path` + `raw_commit`) that a court-facing citation
needs. Letting it own the originals severs the git linkage the output depends on.

### Why not Paperless-ngx

Not adopted: its wins over the alternative are ML classification and scanner workflow,
neither of which this system needs. A sibling project had already chosen Papra for this
homelab, and adding a second DMS buys nothing.

### Why Docling and not the bundled Tesseract

Papra ships a bundled flat-text Tesseract extractor and supports pointing at an external
extraction backend (`CONTENT_EXTRACTION_STRATEGY`). Docling was chosen as that backend:

- **Layout and table awareness.** Legal PDFs are full of tables, multi-column layouts,
  and reading-order traps. Flat-text OCR destroys exactly the structure that makes an
  exhibit legible — a table of transactions becomes an unordered word soup.
- **docx and richer formats**, which the bundled engine does not handle.
- **MIT-licensed and self-hosted**, so nothing leaves the network at the extraction
  stage.

### Then Docling was called *directly* — the experimental result

This is the finding a rebuilder would otherwise have to rediscover:

> **Papra 26.4.0 ignores its Docling configuration** and falls back to its slow,
> crash-prone internal OCR. Configuring the external backend did not take effect.

The bulk ingest path was therefore changed to call `docling-serve` **directly**
(`goldberg-raw` file → Docling → markdown), bypassing the DMS entirely. Papra was
subsequently retired from the ingest path ([ADR 0011](./decisions/0011-auto-ingestion-reconciler.md))
and from the deployment stack ([ADR 0012](./decisions/0012-deployment-topology.md)). It
may still be used as a human drop-target or viewer, but it is not part of this system.

**Two further Docling findings baked into the client** (`extract/docling_client.py`):

1. **Use the async convert flow, not the synchronous one.** `docling-serve` has a
   synchronous wait cap (`DOCLING_SERVE_MAX_SYNC_WAIT`, 120s) which was **failing roughly
   15% of the corpus's largest evidence files** — precisely the big scanned bundles that
   matter most. The client submits to `/v1/convert/file/async`, polls
   `/v1/status/poll/{task_id}` every 3s up to `max_wait` (default 900s), and fetches
   `/v1/result/{task_id}`.
2. **Text and structured files bypass Docling entirely.** `.md`, `.markdown`, `.txt`,
   `.text`, `.json`, `.tsv`, `.csv` are read as-is. Docling cannot and should not OCR
   them — and this passthrough is what lets text documents keep flowing when Docling is
   down.

**Empty vs. failed is a distinction the client makes carefully.** A result with no
`md_content` means Docling found no extractable text (a blank or graphic-only page) —
that is an *empty* result, returned as `""` and recorded as `skipped-empty`, not a
failure. Transient network errors are wrapped as `DoclingError` so a blip fails one
document rather than crashing a bulk run.

**Graceful degradation, not a startup gate.** The ingest service does *not* refuse to
start when Docling is unreachable. It warns; text/passthrough files still ingest; OCR
files dead-letter and are retried on redelivery. A dependency outage degrades one class
of document, never the service.

---

## 8. The provenance model

Three identifiers do distinct jobs. Conflating them is the most likely rebuild mistake.

| Identifier | Definition | Purpose | Stability |
|---|---|---|---|
| **`raw_sha256`** | `sha256(raw file bytes)` | **The correlation ID.** Joins goldberg-raw ↔ manifest ↔ events ↔ ES doc ↔ extracted file | Stable forever for given bytes |
| **`doc_id`** | `gb_` + `sha256(raw_path + "\0" + extracted content)` | The Elasticsearch `_id`; makes re-ingest an **update**, never a duplicate | Changes if extraction output changes |
| **`raw_commit`** | The git commit that introduced the file | The citable provenance anchor | Immutable |

**Why the content hash is the correlation ID** rather than a minted UUID: it is
content-addressed, therefore deterministic, identical at every stage and in every
representation, and stable across re-runs, re-extraction, and re-indexing. A UUID would
have to be threaded through every hop and would break on any replay. It also happens to
equal a content-addressed DMS's own hash, which is what made the join between the two
stores possible.

**Why `doc_id` includes the path as well as the content:** the same bytes at two
different paths are two documents (an exhibit filed under two matters is genuinely two
records); the same path with the same content is one document, updated in place.

### The provenance manifest

`config/provenance-manifest.json` maps `sha256 → {raw_path, raw_commit, matters,
document_type, party_role, origin}`. It is built by walking `goldberg-raw`, and it is
**the authoritative "should exist" set** for reconciliation.

**Provenance is registered before indexing. Always.** Both the catch-up pass and the
per-commit processor call `refresh_provenance()` first, so no document can reach the
index without a manifest entry recording its commit and matter. This ordering is the
structural fix for the v1 defect where documents landed with no provenance at all.

The manifest is written **atomically** (temp file + `os.replace`) so a concurrent reader
or a crash never sees a half-written file — which matters over an SMB mount.

**Where matters come from:** the folder-level `metadata.yaml` chain, via a light
folder-defaults merge (parent provides defaults, child overrides, lists union). The
archive is already organised by party/matter, so `case_number` in an ancestor folder
becomes `matters` on every document beneath it. **No LLM guessing for the structural
legal fields** — the machine only fills summary/keywords/entities/author/claims.

**The join was validated before being relied on:** 20 of 21 documents in the DMS matched
an archive file *exactly* by content hash (the 21st was a leftover test artifact), and
matter resolution through the folder chain resolved correctly wherever the metadata
existed. That is why the join key is a content hash and not a custom property on the
external store.

### Known limitation

A content change to an existing file produces a **new `doc_id`** (it is content-derived)
rather than superseding the previous version, so the older version lingers in the index.
Versioning/dedup is an open decision — see [§14](#14-known-gaps-and-honest-limitations).

---

## 9. The data model

### The two-axis taxonomy

Documents are classified on **two independent axes**, not one flat category list. This
is what stops categories from sliding between buckets.

- **`origin`** — `received` vs `authored`.
- **`role`** — `input` (indexed knowledge the system reasons over) vs `output` (a
  deliverable we produce).

| Category | origin | role | Notes |
|---|---|---|---|
| `evidence/` | received | **input** | Source facts, organised by party/matter |
| `exhibits/` | received | **input** | Documentary exhibits |
| `reports/` | authored | **input** | Reusable legal-research memos |
| `analysis/` | authored (AI) | **input** | Cached LLM research — a memoised answer, reused |
| `briefings/` | authored | **output** | Short notes to legal support |
| `filings/` | authored | **output** | Court documents / applications |

**The rule: `role = input` is enriched and indexed; `role = output` is not.** Outputs are
deliverables, not knowledge.

`analysis/` is worth calling out as a deliberate design: an analysis document is a
**memoised answer**. Because enrichment is claim-aware, cached research becomes
queryable and claim-comparable alongside the evidence, so the system accumulates its own
reusable doctrine. This is a knowledge-layer loop (produce → cache → index → reuse),
distinct from the extraction pipeline's strict one-way rule, and must be designed so
that persisting an analysis document does not trigger uncontrolled reprocessing.

### Representation: markdown + YAML frontmatter

Each extracted document is **one markdown file: a YAML frontmatter prelude plus the
extracted text as the body** ([ADR 0004](./decisions/0004-metadata-representation.md)).

The predecessor used separate `metadata.yaml` files with a full inheritance engine
(locked / overridable / non-inherited / irreversible semantics). That was **dropped as
the primary mechanism**, for a reason worth internalising: once most metadata is
machine-derived per document, inheritance loses its main payoff (DRY hand-authored
metadata), and **a retrieval chunk cannot inherit from a directory anyway** — it needs
its own metadata. What was kept is a *light* folder-defaults merge for the few genuinely
folder-uniform, human-set fields (the legal-handling flags, often `matters`/`parties`),
without the conflict semantics.

### The metadata schema

Defined as pydantic models in `metadata/schema.py` (`extra="forbid"` throughout — an
unexpected field is an error, not silently dropped).

| Field group | Fields | Populated by |
|---|---|---|
| Content | `summary`, `long_summary`, `keywords`, `topic`, `date` | Machine (enrichment) |
| Classification | `document_type`, `party_role`, `origin`, `role` | Machine, human-lockable |
| Attribution | `author` (**who is speaking**, distinct from `parties` = who it is *about*) | Machine |
| Retrieval | `entities`, `parties`, `claims` | Machine |
| Matters | `matters` (a **list** — the corpus spans several cases), `primary_matter` | Folder `metadata.yaml` |
| Provenance | `raw_path`, `raw_commit`, `raw_sha256`, `ingested_at` | Pipeline |
| **Handling** | `cpia_s17`, `privileged`, `sensitivity`, `disclosure_status`, `source_channel`, `reviewed` | **Human only** |

**`HandlingFlags` default to the most protective value**: `cpia_s17=True`,
`privileged=True`, `sensitivity=SENSITIVE`, `disclosure_status=UNKNOWN`,
`source_channel=UNKNOWN`, `reviewed=False`. A `requires_caution` property returns True
until a human has reviewed *and* cleared them. **The LLM must not invent these fields.**
Anything not yet reviewed is treated as sensitive.

A **`Claim`** is `{subject, predicate, object, asserted_by}`. This shape is the whole
basis of contradiction detection: two documents asserting different `object`s for the
same `subject`+`predicate`, with different `asserted_by`, is a contradiction that can be
found by query rather than by reading.

**Matters in this corpus:** `422500059892` (main prosecution), `422500059914`,
`648MC011`, `L00SS179`.

### The Elasticsearch mapping

`sinks/elasticsearch_indexer.py :: INDEX_MAPPING`. Design points:

- `dynamic: False` — unmapped fields are stored in `_source` but not indexed. No
  surprise mappings from LLM output.
- `content`, `summary`, `long_summary` are `text` (BM25); structured fields are
  `keyword` (filter/facet).
- **`claims` is `nested`**, with `subject`/`predicate`/`object` as `text` plus a
  `.kw` keyword sub-field, and `asserted_by` as `keyword`. Nested is required so that
  per-claim field combinations match correctly rather than matching across different
  claims in the same document.
- `date` is `keyword`, not `date` — document dates in this corpus are frequently
  free-form and would fail strict date parsing.
- `ensure_index()` creates the index, or on an existing index issues an additive
  `put_mapping` so a new field becomes queryable without a reindex.
- The ES `_id` is the `doc_id`, making writes idempotent.

A dense-vector field for semantic retrieval is a deliberate follow-up, not built —
see [§14](#14-known-gaps-and-honest-limitations).

---

## 10. Enrichment

`enrichment/openai_enricher.py`. One LLM call per document returns a single JSON object:
`summary`, `long_summary`, `keywords` (5–12), `entities`, `author`, `document_type`, and
`claims`. The system prompt frames the model as a legal-document analyst, instructs it
never to invent facts, and requires JSON only.

**Model: `gpt-4o-mini`.** Cloud LLM use is a **deliberate, logged exception** to the data
boundary, recorded in the project charter — not a silent default. Legally-sensitive
material is sent to a cloud provider for enrichment; that was an explicit decision, and
a rebuild handling comparable material should make it explicitly rather than inherit it.

**Full-context enrichment.** An early implementation truncated at 12k characters; that
was removed so claims and summaries reflect the *entire* document. Modern context
windows make this feasible.

**Budget by tokens, not characters — a real production failure.** The truncation was
first replaced by a 200,000-**character** cap. Token-dense OCR text runs about 1.5
characters per token, so 200k characters is ~128.6k tokens: it hard-failed a 128k-context
model with `context_length_exceeded`, producing a dead-letter storm on exactly the
largest and most important evidence files. The fix, and the correct approach:

- Budget the outgoing message by **tokens**, measured with `tiktoken`
  (`_BODY_TOKEN_BUDGET = 100_000`, leaving headroom for prompt and completion).
- Keep the **leading** text when truncating — headers, parties, and opening claims are
  the most salient.
- **Shrink-and-retry** on a `context_length_exceeded` rejection: halve the budget, up to
  3 attempts (100k → 50k → 25k). If it still fails, the document is genuinely impossible
  and should dead-letter — which is the correct outcome, not a crash.
- Detect the context error **narrowly** (`code == "context_length_exceeded"` or the
  message text), never as a blanket exception catch.

**Offline caveat:** `tiktoken` lazily *downloads* BPE ranks on first use. On an
air-gapped host, pin `TIKTOKEN_CACHE_DIR` to a pre-populated cache. An unrecognised
model name falls back to `cl100k_base`.

**Credentials** are resolved environment-first (`OPENAI_API_KEY`, optionally from a
gitignored `.env`), then `~/.config/goldberg/secrets.toml` — so the same code works on a
developer machine and in a container.

---

## 11. The query path (read side)

The pipeline above is the write side. The read side is deliberately **retrieval tools
plus an agent**: the tools retrieve grounded, structured hits; the **agent synthesises
the answer and cites it**. The tools never generate prose, and the agent never invents
facts.

```
question ──▶ goldberg claims / search / wiki / get / facets ──▶ Elasticsearch
                                                                     │
                                                    grounded hits with provenance
                                                                     ▼
                        agent answers, citing doc_id + raw_path + speaker + date
```

### Two representations, queried together

- **The document index** (`goldberg_documents`) — primary evidence, quotable, attributed.
- **The concept wiki** (`silverbullet-goldberg`) — synthesised entity/concept/comparison
  pages: the *map* of who's who and how they connect.

They are complementary and both should be queried. A spike confirmed this concretely:
the same query returned, from the wiki, a synthesised entity page linking a party to
co-defendants; and from the corpus, the primary evidence document itself. Map and
territory.

### CLI surface (`goldberg`)

| Command | Purpose |
|---|---|
| `search "<text>"` | Full-text over the evidence documents (`--matter`, `--author`, `--type`) |
| `claims` | The nested attributed-claims query — "who asserted what" (`--by`, `--subject`, `--object`) |
| `wiki "<text>"` | Search the concept wiki (`--layer entity\|concept\|comparison`, `--tag`) |
| `get <doc_id>` | Read a document's full text to quote it |
| `facets` | Orient: counts by matter/author/type/party |
| `status [--yaml]` | System health, corpus size, per-stage counts, DLQ depth |
| `doctor [--yaml]` | Per-component liveness board |
| `audit [--missing]` | Reconciliation: expected vs actual, what did not ingest |
| `trace <raw_path\|sha256\|doc_id>` | One document's stage timeline and where it stopped |
| `dlq` | Inspect dead-lettered documents |
| `ingest-serve` | Run the event-driven ingest service |
| `ingest catchup` | One bounded catch-up pass, then exit |
| `publish-commit <sha>` | Publish a commit trigger (used by the git hooks) |
| `migrate populate-raw \| manifest \| reingest` | Corpus migration and bulk re-ingest |
| `mcp-serve` / `dashboard` | The MCP server / the Streamlit dashboard |

### The MCP server

The primary human is rarely at a dashboard; they ask an agent. So the same capabilities
are exposed as a **hosted MCP server** (`goldberg mcp-serve`, `streamable-http` on
`:8765/mcp`, optional `mcp` install extra) with eight read-only tools:

`system_status` · `recent_failures` · `trace_document` · `component_health` ·
`search_evidence` · `find_claims` · `search_concepts` · `get_document`

Three design rules, all worth copying:

1. **Every tool is a thin wrapper over the same functions the CLI uses.** There is no
   second implementation to drift.
2. **Tools expose *intents*; the server owns the *mechanism*.** The model passes a
   natural query plus typed filters and gets structured, citable JSON. It never writes
   an Elasticsearch query.
3. **No `run_es_query` or `run_shell` escape hatch.** That would push mechanism onto the
   model and is a security footgun. Read-only throughout; writes stay in the CLI and
   pipeline.

Hosted (one always-on endpoint many agents connect to) rather than per-agent stdio, and
agent-agnostic by construction: `AGENTS.md` is the canonical operating brief and
`CLAUDE.md` is a symlink to it, so non-Claude agents are first-class.

---

## 12. Observability

For this system, observability is not an ops nicety — it is how the correctness property
of [§1](#1-what-the-system-is-for) is enforced. It must answer three questions: *is it
working?*, *did anything not ingest?*, and *why did X not ingest?*

### The event model

Every stage boundary, for every document, emits one `PipelineEvent`:

```
ts · run_id · component · stage · status · doc_id · sha256 · raw_path · attempt · reason · error
stages:   received | extracted | enriched | indexed | wiki_authored
statuses: started | ok | skipped | failed
```

Events are indexed into `goldberg_pipeline_events`. **Emission is best-effort and must
never break the pipeline** (`safe_emit`): a telemetry failure is logged, not fatal.

The design intent recorded in [ADR 0008](./decisions/0008-observability-architecture.md)
is NATS JetStream as the durable event backbone with an ES projection for querying;
what is *built* is the ES projection written to directly, with the DLQ realised on the
ingest consumer's nak/term semantics. A rebuilder should read that ADR for the fuller
design and this section for what exists.

### Reconciliation — the completeness check

`goldberg audit` is a set join on the content hash:

- **Expected** = the provenance manifest (the authoritative "should exist" set)
- **Actual** = `goldberg_documents`

reporting **missing** (expected − actual), **extra**, and **stale** (content hash changed
since indexing), and joining the event log to attach each missing document's last-known
stage and reason. This is the direct answer to "is there something that did not ingest?"
— and it is what makes a bulk migration self-verifying.

A **third axis — `goldberg audit --orphans`** — closes a hole the manifest-vs-index join
cannot see. That join compares two sets (manifest, index) but is blind to a *third* source
of truth: the raw tree on disk. A document **deleted from goldberg-raw** survives in both
the manifest and the index (see §14 — the pipeline has no delete path), so the plain join
still reports it "matched" and the corpus COMPLETE. `--orphans` checks each manifest
`raw_path` against the actual files in goldberg-raw and reports every one whose source is
gone, annotating each with the ES `doc_id` when a document still exists to be expunged. It
distinguishes the dangerous class (**indexed orphan** — a stale ES document whose source
was removed) from the benign one (**manifest-only** — provenance for a pruned large binary
that was never indexed anyway, per ADR 0002). Verified 2026-07-25: the live corpus has 36
manifest-only orphans (all pruned `.mp4`) and **zero indexed orphans**.

### Component health — `goldberg doctor`

Audit and trace are **data-plane** questions ("did this document ingest?"). They do not
answer the **control-plane** question ("is the pipeline itself up right now?"). The
doctor board probes six components concurrently, each read-only, individually
time-bounded, and never raising:

| Component | Probe |
|---|---|
| `elasticsearch` | `_cluster/health` + a `_count` on each required index |
| `docling` | `GET /health` == `{"status":"ok"}` |
| `enricher` | `GET /v1/models` — a **metadata call, never a completion**, so probing bills no tokens |
| `mcp_server` | A real `initialize` handshake over streamable-http |
| `live_index_watcher` | **Inferred** from pipeline-event freshness (default 15 min window) |
| `wiki_synthesis` | Index presence vs. whether synthesis is actually wired |

Status is a three-value enum — **UP / DEGRADED / DOWN** — so "reachable but not fully
correct" is structurally distinct from "unreachable". The overall verdict is the *worst*
component, and the exit code follows it, making it usable in CI or cron.

**The honesty rule, which is the part most worth copying:** two probes cannot be true
liveness checks and say so. The watcher runs on a host the command may not be able to
reach, so its status is *inferred* from event freshness and every result carries
`inferred=true` — green means "events are flowing", not "the process was pinged". And
wiki synthesis reports **DEGRADED by default** because the refresh mechanism does not
exist yet; the probe deliberately refuses to paint that leg green.

(A related fix worth noting: `no_recent_failures` was once permanently red because it
counted *all* historical failures; it now counts only failures within a configurable
recent window.)

### The dashboard

One canonical `SystemState` model, **two renderers**
([ADR 0009](./decisions/0009-operations-dashboard.md)): a Streamlit UI for humans and
**YAML for LLMs** (`goldberg status --yaml`). The dual-mode requirement is the
architecture, not a feature: both are renderers of the same object, so they cannot
drift. YAML rather than JSON is deliberate — the least-punctuated structured format for
a model to skim, matching how the corpus metadata is already represented.

`aggregate()` is pure read. The dashboard never generates telemetry.

---

## 13. Deployment topology

[ADR 0012](./decisions/0012-deployment-topology.md), as revised by ADR 0013.
`deploy/docker-compose.yml` defines **three stateless processing services and nothing
else**:

| Service | Image / build | Ports | Command |
|---|---|---|---|
| `docling` | `ghcr.io/docling-project/docling-serve-cpu:latest` | 5001 | (default) |
| `ingest` | `deploy/Dockerfile.ingest` | 8098 (`/health`) | `goldberg ingest-serve` |
| `mcp` | `Dockerfile.mcp` | 8765 | `goldberg mcp-serve` |

### The load-bearing decisions

**Elasticsearch and NATS are NOT in the stack.** They are stateful, already running, and
*everything* depends on them — so they are a shared layer that must outlive any redeploy,
reached over TCP via `${GOLDBERG_ES_URL}` and `${NATS_URL}`. Bundling a second
Elasticsearch would risk the live corpus on every redeploy and would turn "move to a
faster host" into a data migration. The processing stack is the portable piece; the data
and the bus stay put.

**Portability comes from `goldberg-raw` + the manifest being the source of truth.** The
Elasticsearch corpus is *derived*. To lift and shift: copy the repo and `.env`, set
`GOLDBERG_ES_URL` and `GOLDBERG_RAW_PATH`, and bring the stack up — the ingest service
rebuilds the corpus on the new host via the startup catch-up. No ES data migration.

**Inter-service addressing is by compose service name, never IP.** Only the external ES
and NATS URLs are host values in `.env`.

**Volumes — both are subtle and both have bitten:**

- **`goldberg-raw` read-only, including `.git`.** `GOLDBERG_RAW_PATH` must be the git
  **working-tree root** so the `.git` directory travels with the mount: the service runs
  `git log` / `git diff` to stamp `raw_commit` and to compute the catch-up diff. A mount
  without `.git` silently breaks provenance.
- **The config directory read-write and persistent.** The service *writes* the
  provenance manifest on every commit and catch-up pass. If that lived in an image layer
  it would be lost on every redeploy — and with it the record that makes the corpus
  rebuildable. The same mount supplies a container-tuned `projects.yaml`
  (`deploy/projects.container.yaml`, with in-container paths), selected via
  `GOLDBERG_PROJECTS_CONFIG`. **This is the one manual config step per host.**

**`git config --system --add safe.directory /data/goldberg-raw`** is baked into the
ingest image. Without it, the container (root) refuses to run git on a host-owned bind
mount and every commit resolution fails.

**Petite-host safety.** The target host is a 4-core Celeron NAS. Docling is memory-capped
so a large scan OOMs *the container* (dead-lettered and retried) rather than the host;
ingest defaults are conservative (workers 2, batch 50, max-deliver 5).

**Healthcheck timing.** The ingest `/health` endpoint only opens *after* the startup
catch-up, which can take minutes. A short healthcheck start-period marked the container
unhealthy mid-catch-up and an autoheal supervisor restarted it into a catch-up loop.
Mitigated with a 900s start-period; the real fix (open `/health` before the catch-up) is
an open follow-up.

### Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `GOLDBERG_ES_URL` | `http://192.168.86.31:9200` | Elasticsearch |
| `GOLDBERG_ES_INDEX` | `goldberg_documents` | Corpus index |
| `GOLDBERG_EVENTS_INDEX` | `goldberg_pipeline_events` | Event index |
| `NATS_URL` | `nats://192.168.86.31:4222` | Broker |
| `GOLDBERG_NATS_STREAM` / `_SUBJECT_PREFIX` / `_COMMIT_SUBJECT` / `_DURABLE` / `_MAX_DELIVER` / `_ACK_WAIT` / `_DEDUP_WINDOW` | See [§3](#3-external-dependencies--and-why-each-one) | JetStream naming and delivery |
| `GOLDBERG_DOCLING_URL` | `http://localhost:5001` (`http://docling:5001` in-stack) | Docling |
| `GOLDBERG_DOCLING_MAX_WAIT` | `900` | Per-document conversion wait (seconds) |
| `OPENAI_API_KEY` | — | Enrichment (optional; text files ingest without it) |
| `GOLDBERG_PROJECTS_CONFIG` | `config/projects.yaml` | Path to the projects config |
| `GOLDBERG_CONFIG_DIR` | `<repo>/config` | Config directory for all loaders |
| `GOLDBERG_MCP_HOST` / `_PORT` | `0.0.0.0` / `8765` | MCP bind |
| `TIKTOKEN_CACHE_DIR` | — | Set on air-gapped hosts |

`config/projects.yaml` is the single source of truth for where the sibling repos and
services live; every module asks it rather than hard-coding paths.

---

## 14. Known gaps and honest limitations

Stated so that nothing above reads as more complete than it is.

- **Concept-wiki synthesis is not wired.** The wiki index exists and is searchable, and a
  pure renderer (`wiki/page.py`) can build an entity page from enriched claims. The
  LLM-driven "orient → author → validate → apply" loop and the automatic refresh from
  ingestion are **not built**. The doctor probe correctly reports this leg as DEGRADED.
  Design: [ADR 0007](./decisions/0007-concept-wiki-output.md).
- **Document versioning.** A content change to an existing file produces a *new*
  `doc_id`; the previous version lingers in the index rather than being superseded. This
  needs a dedup/versioning decision.
- **Deletion is not propagated — by design, but incompletely guarded.** The pipeline has
  **no delete path**. A deletion commit resolves to zero ingestable files
  (`commit_files.changed_files` drops `D`-status paths) and is acked as a no-op; catch-up
  and `refresh_provenance` only ever *add*; no sink implements delete. So removing a file
  from goldberg-raw leaves its ES document and manifest entry in place indefinitely. This
  is the correct default for an immutable evidence corpus (nothing silently vanishes), but
  it means **there is no supported way to expunge a document** — a legitimate need (e.g.
  privilege or an order to destroy). Expunging today is manual: `DELETE` the ES doc by
  `_id` in `goldberg_documents` and remove the manifest entry. Detection *is* now
  automated — **`goldberg audit --orphans`** flags every manifest `raw_path` whose source
  file is gone and marks which still have an ES document to expunge (verified against the
  live corpus 2026-07-25: 36 manifest-only, 0 indexed). Empirically established by the
  deletion probe of 2026-07-25; a supported `goldberg expunge <doc_id>` remains unbuilt.
- **Health before catch-up.** `/health` should open before the startup catch-up runs, so
  a long catch-up cannot trip a supervisor. Currently mitigated with a long start-period.
- **Fast-forward `git pull` fires no hook.** Covered by startup catch-up and `audit`, not
  by the trigger.
- **The resume set reads up to 10,000 `raw_sha256` values** in one query. A corpus beyond
  that needs a scroll or point-in-time follow-up.
- **No dense-vector retrieval.** Retrieval is BM25 plus structured claim queries. A
  semantic kNN field was specified but deliberately deferred — the current combination
  answers the actual questions, and the value of adding embeddings is unproven here.
- **`.eml` extraction.** An email-to-markdown extractor exists (`extract/eml.py`) for the
  gap Docling does not cover; the DMS-era path did not handle email bodies at all.
- **Papra remains in the codebase** (`papra/`) and in `pipeline.py`'s
  `backfill_from_papra`, but is **off the ingest path and out of the deployment**. Treat
  it as historical.
- **The retired service code** (`service/`) is kept for reference and is not deployed.
- **Mind of Steele reuse is referential, not a dependency.** The sibling project supplied
  the pattern (LLM summarisation, ES indexer, NATS-driven service loop); it lives on a
  different machine and is intentionally *not* a hard dependency. What exists here is our
  own implementation of the same pattern.

---

## 15. Rebuilding this system from scratch

A condensed, ordered procedure. Each step names the section with the reasoning.

**1. Provision the external services** ([§3](#3-external-dependencies--and-why-each-one)).
Elasticsearch 8.x, NATS with JetStream enabled, and a host that can run containers.
These are shared and long-lived; do not bundle them with the processing stack.

**2. Create the repositories** ([§4](#4-repository-topology--why-four-repos)). At minimum
`-system` (code) and `-raw` (originals). Configure selective git-LFS in `-raw` via
`.gitattributes` **before** committing large binaries. Add `-extracted` and `-casework`
if you want the mirror and the separation of authored work.

**3. Decide what is evidence.** Write the allowlist (`config/evidence-allowlist.yaml`):
included trees with their `origin`, excluded trees *with reasons*, and file globs never
migrated (media, `.DS_Store`, `__pycache__`). If you are migrating a legacy corpus,
expect most of it not to be evidence.

**4. Establish folder metadata.** Put a `metadata.yaml` at the appropriate level of each
tree carrying the matter/case number, party role, document type, and the legal-handling
flags. This is the human-authored tier; the machine fills everything else
([§9](#9-the-data-model)).

**5. Build the provenance manifest** — walk the raw tree, computing for each allowlisted
file its sha256, its introducing commit, and its inherited matters. `goldberg migrate
manifest`. This is your authoritative "should exist" set ([§8](#8-the-provenance-model)).

**6. Stand up extraction.** Run `docling-serve`. Call it **directly**, via the **async**
submit/poll/result flow, with a generous wait. Pass text and structured formats through
untouched ([§7](#7-why-docling--and-not-the-alternatives)).

**7. Implement enrichment.** One JSON-returning LLM call per document producing summary,
entities, author, and **attributed claims**. Budget by **tokens** with shrink-and-retry;
never by characters ([§10](#10-enrichment)).

**8. Create the index with an explicit mapping.** `dynamic: false`; `claims` as
**nested**; the deterministic `doc_id` as the ES `_id`
([§9](#9-the-data-model)).

**9. Wire the ingest service.** Provenance-first, then extract → enrich → sinks, with
per-file terminal/transient status classification and an ack-only-when-terminal rule
([§5](#5-the-ingestion-path-write-side)).

**10. Wire the trigger.** Git `post-commit` and `post-merge` hooks (via `core.hooksPath`)
publishing a commit event to a durable JetStream consumer. **Hooks must never fail
`git`.** Set the ack wait above worst-case document processing time
([§6](#6-why-trigger-not-poll)).

**11. Add the one-shot startup catch-up** — bounded, non-looping, reporting any backlog
it did not reach as a degraded health status ([§5.4](#54-startup-catch-up-one-bounded-pass-never-a-loop)).

**12. Add observability before the bulk migration, not after.** Events, DLQ, `audit`,
`trace`, `doctor`. The migration is then self-verifying: it can prove "N expected, N
indexed, 0 missing" ([§12](#12-observability)).

**13. Backfill.** `goldberg migrate reingest --resume`, then `goldberg audit` to prove
completeness.

**14. Expose the read side.** The CLI first, then the MCP server. Intent-shaped,
read-only tools; no shell or raw-query escape hatch ([§11](#11-the-query-path-read-side)).

**15. Deploy** as a processing-only compose stack against the external ES and NATS, with
raw mounted read-only *including `.git`*, and the config directory read-write and
persistent ([§13](#13-deployment-topology)).

Verification procedure for a running system:
[`runbooks/verifying-the-system-is-up.md`](./runbooks/verifying-the-system-is-up.md).
Trigger wiring: [`runbooks/wiring-the-ingest-trigger.md`](./runbooks/wiring-the-ingest-trigger.md).

---

## 16. Decision record index

Each ADR records one decision with its options and, where applicable, its spike results.

| ADR | Decision | Status |
|---|---|---|
| [0001](./decisions/0001-wiki-rag-sink-backend.md) | RAG on Elasticsearch as the canonical backend | **Accepted** |
| [0002](./decisions/0002-large-binary-handling.md) | Selective git-LFS in `goldberg-raw` | **Accepted** |
| [0003](./decisions/0003-document-management-papra-integration.md) | Papra + Docling as the extraction front end | **Partly superseded** — Docling kept, called directly; Papra retired from the ingest path (0011) and the deployment (0012) |
| [0004](./decisions/0004-metadata-representation.md) | Markdown + YAML frontmatter; drop the inheritance engine | **Accepted** |
| [0005](./decisions/0005-live-service-webhook-driven.md) | Papra-webhook-driven live service | **Superseded** by 0011 → 0013 |
| [0006](./decisions/0006-ingestion-provenance-architecture.md) | git-raw as system of record; sha256 join; the manifest | **Accepted (spike-validated)** — still the provenance model |
| [0007](./decisions/0007-concept-wiki-output.md) | SilverBullet concept wiki as a downstream sink | **Accepted, partially built** ([§14](#14-known-gaps-and-honest-limitations)) |
| [0008](./decisions/0008-observability-architecture.md) | Event backbone, DLQ, reconciliation, doctor | **Core delivered** |
| [0009](./decisions/0009-operations-dashboard.md) | One `SystemState`, two renderers | **Phases 1–2 built** |
| [0010](./decisions/0010-mcp-server.md) | Hosted MCP server, intent-shaped read-only tools | **Accepted (built + tested)** |
| [0011](./decisions/0011-auto-ingestion-reconciler.md) | Polling reconciler as the canonical ingest path | **Superseded** by 0013 |
| [0012](./decisions/0012-deployment-topology.md) | Portable processing stack against external ES/NATS | **Accepted** (ingest service revised by 0013) |
| [0013](./decisions/0013-event-driven-ingestion.md) | git-hook → NATS → durable processor | **Accepted** — the current ingest path |

Supporting records: [`verification/event-driven-ingestion-results.md`](./verification/event-driven-ingestion-results.md)
(live cutover evidence), [`design.md`](./design.md) (the original design session — historical),
[`workflow.md`](./workflow.md) (the ingestion contract from the document-author's point
of view), [`roadmap.md`](./roadmap.md) (mission history).
