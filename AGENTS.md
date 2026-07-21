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

When the user asks about the **case / evidence / documents** (e.g. "who did
Goldberg say was the prosecuting entity?"), **do not answer from memory** — run the
`goldberg` query tools to retrieve grounded evidence, then answer **with citations**
(`doc_id` + `raw_path` + speaker + date). Never invent; if nothing relevant is
indexed, say so.

- `uv run goldberg claims [--by <speaker>] [--subject X] [--object X]` — who
  asserted what (attributed; use for "who said" + contradiction-hunting).
- `uv run goldberg search "<keywords>" [--matter M] [--author A]` — full-text.
- `uv run goldberg get <doc_id>` — read a document's full text to quote it.
- `uv run goldberg facets` — orient (counts by matter/author/type/party).

**Full instructions: [`doc/runbooks/querying-the-corpus.md`](doc/runbooks/querying-the-corpus.md).**
