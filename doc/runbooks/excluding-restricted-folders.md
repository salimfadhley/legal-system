# Excluding restricted folders from indexing (`no_index`)

Some corpus material must **never** be searchable — witness statements restricted
under **CPR 32.12**, a party's own account of events that must not surface as if it
were documentary evidence, or third-party documents received under a use
restriction. This is a **legal** requirement, and the failure mode is silent: a
search that returns *more* results looks like a working search, so nobody notices a
restricted document has leaked into a drafting session.

The mechanism is **declarative and lives in the repo**, so the rule survives every
re-ingest, backfill and reindex — it does not depend on anyone remembering.

## The protocol (two roles)

1. **Casework declares the restriction** — adds `no_index` to a folder's
   `metadata.yaml` (below). This is a legal judgement and belongs to casework.
2. **Casework tells `system`** which paths are affected. `system` **purges** whatever
   was already indexed *before* the flag existed (`no_index` only stops *future*
   ingestion; it does not retroactively remove what is already in the index).

Declaring the flag and purging the back-catalogue are **two different actions** — you
need both.

## 1. Declare: `no_index` in a folder `metadata.yaml`

Add to the `metadata.yaml` at (or above) the folder you want excluded:

```yaml
no_index: true
no_index_reason: "CPR 32.12 — restricted witness statements, written undertaking (648MC011)"
```

- **Recursive:** applies to everything beneath that folder.
- **Reason is required in practice:** the next person reading the repo must know this
  is a *legal restriction*, not a performance tweak, or they will "fix" it.
- **Reversible:** delete the two keys and the subtree indexes again on the next
  ingest. (Useful when a restriction is time-bound — e.g. CPR 32.12(2)(c) lifts once
  a statement is put in evidence at a public hearing.)
- **Override a subfolder:** a child `metadata.yaml` may set `no_index: false` to
  re-include a specific subfolder.

### Placement warning — cover *every* copy

Restricted content is often present under **more than one path** (two copies of a
bundle) and **inside container files** (a full-bundle PDF whose text contains the
restricted statements as pages). A flag on one folder will not cover a copy
elsewhere. When in doubt, flag the **whole matter's folder** and re-include the
clearly-unrestricted parts with `no_index: false` — over-exclude, then narrow.

## 2. Purge what is already indexed (`system` runs this)

```
legal_system deindex --path "<raw_path prefix>" [--path "<prefix>" ...] \
    --extracted-root /path/to/goldberg-extracted \
    --reason "CPR 32.12 — ..." [--dry-run]
```

- `--dry-run` first — it only **counts** what would be removed.
- Deletes matching docs from Elasticsearch **and** removes the mirrored files from the
  derived store (otherwise a `reindex-from-extracted` would restore them). The
  operator commits the derived-store removals.
- Refuses a prefix shorter than 3 characters (so a stray `--path /` cannot wipe the
  corpus); `--force` overrides.
- **Originals are never touched** — `goldberg-raw` keeps the files; they simply stop
  being searchable.

## What enforces it (defense in depth)

- **Quiet, expected path:** ingestion selection (catch-up and reingest) skips
  `no_index` entries and logs `skipped <path> — no_index: <reason>`.
- **Loud backstop:** if a `no_index` document ever reaches the Elasticsearch indexer
  or the extracted-store writer by any route, the write is **refused** and the
  document **dead-letters** — it fails loudly rather than leaking silently.

## Current customers

- `evidence/etp_v_stephen_afshar/…648MC011…` — CPR 32.12 restricted witness
  statements (scope being finalised with casework — the full-bundle PDFs contain them
  too).
- `proof-of-evidence/` (in `goldberg-casework`) — a party's own account; must never be
  cited as documentary evidence. Today protected by directory location alone; add
  `no_index: true` for belt-and-braces.
- Any third-party document received under a use restriction.
