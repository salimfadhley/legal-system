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

When the user asks a question about the **case / evidence / documents** (e.g.
"who did Goldberg say was the prosecuting entity?", "what did the CPS say about
discontinuance?", "find where the disclosure status is disputed"), **do not answer
from memory** — the answer lives in the indexed corpus. Use the `goldberg` query
tools to retrieve grounded evidence, then answer **with citations**.

Run the tools with `uv run goldberg <command>` from this repo (config — ES/Papra/
OpenAI — is read from the gitignored `.env`):

| Command | Use it to |
|---|---|
| `uv run goldberg search "<question or keywords>" [--matter M] [--author A] [--type T]` | Full-text (BM25) search over content/summary/keywords. Returns doc_id, raw_path, matters, author, summary, highlighted snippets. |
| `uv run goldberg claims [--by <speaker>] [--subject X] [--object X] [--text X] [--matter M]` | **Attributed claims** — "who asserted what about whom". This is the tool for *"who did X say was Y"* and for spotting contradictions across documents. |
| `uv run goldberg get <doc_id> [--no-content]` | Fetch a document's full extracted text + metadata, to read and quote precisely. |
| `uv run goldberg facets` | Orient: counts by matter, author, document_type, party. |

**How to answer:**
1. Pick the tool: attribution/"who said" questions → `claims`; topic/keyword
   questions → `search`; then `get` the most relevant docs to read the exact text.
2. Synthesise the answer from what you retrieved — **never invent**.
3. **Cite every claim**: give the source `doc_id` + `raw_path`, the **speaker**
   (`asserted_by`/`author`), and the date where available. This is legal work
   product; provenance is mandatory.
4. If the corpus has nothing relevant, say so plainly rather than guessing.

The index is Elasticsearch `goldberg_documents` on Halob; each document is one
enriched markdown-with-frontmatter file (summary, keywords, entities, author,
matters, attributed claims, provenance). See `doc/design.md` and `doc/roadmap.md`.
