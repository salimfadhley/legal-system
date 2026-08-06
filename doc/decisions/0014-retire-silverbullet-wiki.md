# ADR 0014 — Retire the SilverBullet concept-wiki; keep ES + claims as the substrate

**Status:** Proposed (recommendation) · **Date:** 2026-08-06 ·
**Supersedes:** [ADR 0007](0007-concept-wiki-output.md)

## Context

[ADR 0007](0007-concept-wiki-output.md) adopted a **SilverBullet LLM-wiki** as the
corpus's "by concept" output — a browsable, cross-linked map of entities, concepts,
and (crucially) *contradictions*, authored downstream of enrichment by a new
`WikiAuthorSink`, reusing the Mind-of-Steele indexer/linter/author machinery. It was
a reasonable bet at the time. Fifteen months of hindsight (well, two weeks) say it did
not pay off, and it is now carrying cost for no use.

**What actually got built vs. what 0007 designed:**

ADR 0007 §2 defined three downstream sinks off the enricher:

```
enrich ─┬─▶ ElasticsearchIndexer (query)      ← BUILT, and it is what everyone uses
        ├─▶ ExtractedRepoWriter  (git mirror) ← BUILT but never WIRED (see ADR 0015)
        └─▶ WikiAuthorSink       (wiki)       ← NEVER BUILT (only the pure renderer exists)
```

Only the ES indexer ran. `ExtractedRepoWriter` existed but was reachable only behind the
retired Papra-backfill flag, so `goldberg-extracted` stayed empty until
[ADR 0015](0015-extracted-repo-as-derived-store.md) wired and populated it. The wiki has
just the pure page **renderer**
(`src/goldberg_system/wiki/page.py`); the LLM "orient → author → validate → apply"
loop, the `comparison/` (contradiction) pages, and the event hook — the whole point of
M11 — were left as "remaining for M11 proper" and never completed.

**Observed live state (2026-08-06):**

- **The wiki is dark.** The synthesised pages were last modified **April–May 2026**;
  the space holds **5 entity pages, 1 concept page, 0 contradiction pages**, and a log
  of ~115 ad-hoc queries. The `silverbullet-goldberg` ES index has 312 indexed pages
  (mostly that query log and raw scaffolding), not synthesised knowledge.
- **No one reads it.** The client reports never having viewed or searched it. The
  casework agent's repo references `goldberg wiki` / `search_concepts` 8 times, but
  those are inherited "query both representations" boilerplate, with no sign of
  reliance. *(Confirmation requested from casework, 2026-08-06 mailbox thread "Do you
  actually use the SilverBullet concept-wiki?"; this ADR will record the answer.)*
- **It costs running infrastructure.** A SilverBullet server (`:3100`), a separate
  `silverbullet-goldberg` ES index, and a watchdog/linter daemon on Halob — all live,
  all for a feature nothing consumes.

**Meanwhile the value 0007 was chasing already exists in cheaper form:**

- The **per-document enhanced markdown is already git-hosted** — Docling's extracted
  markdown lives as `**/markdown/*.md` inside `goldberg-raw` (33 dirs, 999 tracked
  `.md` files) alongside `metadata.yaml`. So the "browsable, git-hosted, grep-able"
  representation 0007 imagined as `ExtractedRepoWriter → goldberg-extracted` is *there*,
  just co-located with raw rather than in a separate repo.
- The **`goldberg_documents` ES index** (6,649 docs) + **attributed claims** is the
  substrate that is actually queried — via the CLI and the MCP server's
  `search_evidence` / `find_claims`. That is where the real usage is.

The wiki is empty not because SilverBullet is the wrong *container* (its pages are
themselves just markdown files) but because the synthesis author was never built. So
the live decision is not "SilverBullet vs. git markdown" — it is **"do we want a
synthesised concept layer at all, given the ES + claims substrate is what gets
used?"**

## Decision

1. **Retire the SilverBullet concept-wiki as the corpus's concept-index output.** Do
   not build the `WikiAuthorSink` (M11). Stand down the `silverbullet-goldberg` server,
   its ES index, and its watchdog/linter daemon. Per [[shared-infra-docs]] the actual
   teardown of that shared Halob machinery is documented in **Mind of Steele**, not
   here; this ADR records only the goldberg-side decision to stop depending on it.

2. **Keep Elasticsearch + attributed claims as the single query substrate.** It is what
   the CLI and MCP tools already use and what casework already relies on. Contradiction
   work runs directly over the nested `claims` (see the claim-graph design memo,
   `doc/prompt/20260806T073358Z_claim-graph-for-contradiction-detection/`).

3. **If a synthesised human-readable layer is ever wanted, generate it as plain
   git-hosted markdown, on demand — not a running wiki.** The obvious first (and maybe
   only) consumer is **contradiction / `comparison` pages** — exactly the "argument-grade
   output" 0007 §3 wanted, and exactly what the casework claim-graph request would feed.
   Emit them as markdown files (citing `raw_path` + `doc_id`, preserving the casework
   verification rule) into a git repo — `goldberg-extracted` is the natural home, or a
   folder in `goldberg-casework`. No server, no daemon, no separate index; diff-able and
   GitHub-browsable. Build it only when a real consumer justifies it.

## Consequences

- **Dead code / dead surface to remove or leave inert:** `CorpusQuery.wiki()`, the
  `goldberg wiki` CLI command, the MCP `search_concepts` tool, and the `wiki:` line in
  `goldberg status`. Prefer removal (with a note) over silent inertia so the corpus-query
  story is honest.
- **Docs to update:** `doc/runbooks/querying-the-corpus.md`, the "query both
  representations" instruction in the project `CLAUDE.md`/`AGENTS.md` query skill, and
  the same boilerplate mirrored into `goldberg-casework`. Drop the "two representations
  — query both" framing; there is one substrate.
- **`goldberg-extracted`** stays an empty placeholder. Decide its fate deliberately:
  repurpose it as the home for on-demand git-markdown synthesised pages (decision §3),
  or delete the repo. Do not leave it provisioned-and-empty indefinitely.
- **Nothing of value is lost.** No user reads the wiki; the synthesised pages are three
  months stale; the contradiction layer it promised was never authored. The enhanced
  markdown and the claims substrate — the parts that carry the actual content — are
  untouched.
- **Consistency with the v2 direction.** The Mr Lawyer rebuild proposal already drops the
  wiki in favour of an `entities` ES index; retiring it in v1 aligns the two.

## Open / pending

- **Casework usage confirmation** (mailbox, 2026-08-06). If casework *does* rely on
  `search_concepts` in some workflow, this decision downgrades from "retire" to "stop
  investing; keep the index read-only until the claim-graph work replaces it." Recorded
  here once they reply.
- **Teardown sequencing** with Mind of Steele (the daemon + server live there).
