# Reuse — Mind of Steele resolution

The pipeline reuses **Mind of Steele** (MoS, `~/workspace/mind_of_steele`) rather
than reinventing enrichment, Elasticsearch indexing, and RAG upload. This note
records **how** MoS is resolved as a dependency — the M1 decision — while the
actual wiring happens later (M3 enrich, M4 sinks).

## The boundary (M1)

M1 defines a typed boundary, not an implementation:

- `goldberg_system.enrichment.adapter.EnrichmentAdapter` — the Protocol the
  pipeline enriches against (`enrich(request) -> EnrichmentResult`, producing
  summary, keywords, entities, the `author`/speaker, and attributed `claims`).

Downstream missions provide concrete adapters that call MoS's `common.llm_support`
(and, for sinks, its `elasticsearch` + `ragie_uploader`). Because the pipeline
depends only on the Protocol, MoS can be swapped or mocked without touching
pipeline code, and M1's tests run with a fake adapter and **no external services**.

## How MoS is obtained

MoS lives on the Mac, not on Halob, and is not published to an index. It is
resolved as a **git source dependency**, pinned to a commit, and installed into
the environment only when the concrete adapters are wired (M3/M4). Until then it
is intentionally **not** a hard dependency of `goldberg-system` (keeping M1 pure).

- Preferred: add MoS as a git dependency (pinned) in the optional dependency group
  that M3/M4 activate — e.g. `uv add "mind-of-steele @ git+file:///Users/salimfadhley/workspace/mind_of_steele@<sha>"`
  (or the GitHub URL once MoS is pushed), imported behind the adapter.
- Alternative: vendor the specific MoS modules if a pinned import proves awkward
  across the Mac/Halob boundary.

The decision to record here: **resolve MoS behind the `EnrichmentAdapter`
Protocol; import it as a pinned git source in the M3/M4 dependency group; keep M1
free of the dependency.**
