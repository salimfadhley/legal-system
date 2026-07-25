# Runbook — corpus completeness (`goldberg audit`)

**Audience:** an operator (or an agent acting for one) who needs to answer three
questions about corpus *integrity*, not liveness:

1. **Did anything fail to ingest?** — a document that should be in the corpus but isn't
   (an invisible hole in a legal corpus).
2. **Is anything indexed that shouldn't be?** — a document in the index with no manifest
   provenance.
3. **Was anything deleted from `goldberg-raw` but left behind?** — a stale record whose
   source file no longer exists.

For "is the pipeline *up* right now?" use [`goldberg doctor`](./component-health.md); for
"why did *one* document (not) ingest?" use `goldberg trace`. This runbook is about the
*completeness* of the whole corpus.

`goldberg audit` is **read-only** and drives cleanly from cron/CI — it **exits non-zero**
whenever it finds a gap or an orphan.

## The three axes

Completeness is a join across **three** sources of truth. The manifest and the index are
two sets; the raw tree on disk is the third. The default audit joins the first two:

| Axis | Definition | Flag | Meaning |
|------|------------|------|---------|
| **missing** | in the manifest, not in the index | (always shown; `--missing` lists) | The document never ingested — the drops. **This is the important one.** |
| **extra** | in the index, not in the manifest | (always shown; `--extra` lists) | Indexed without provenance (e.g. a legacy Papra-era doc whose `raw_path` is a filename, not a manifest path). |
| **stale** | content hash changed since indexing | (always shown) | The source changed after it was indexed; the index is behind. |
| **orphan** | in the manifest, but the source file is **gone from `goldberg-raw`** | `--orphans` | A document deleted from raw. The manifest-vs-index join **cannot see this** — the stale manifest entry still "matches" the index, so the plain audit reports COMPLETE. |

### Why `--orphans` is a separate axis

The pipeline has **no delete path** (established by the deletion probe of 2026-07-25, see
[ADR 0008 §4](../decisions/0008-observability-architecture.md) and
[architecture §14](../architecture.md#14-known-gaps-and-honest-limitations)):

- a deletion commit resolves to **zero** ingestable files (`commit_files.changed_files`
  drops `D`-status paths) and is acked as a no-op;
- catch-up / `refresh_provenance` only ever **add** manifest entries;
- no sink implements delete.

So removing a file from `goldberg-raw` leaves its ES document **and** its manifest entry
in place. Because both survive, they still join — the default audit calls the corpus
COMPLETE while a stale document lingers. `--orphans` checks each manifest `raw_path`
against the **actual raw tree** and reports every one whose file is gone.

It splits the result into two classes:

- **indexed orphan** — the source is gone **and** an ES document still exists. This is the
  dangerous class: a stale, un-expungeable record. The report prints its `ES _id`.
- **manifest-only** — the source is gone and there is **no** ES document. Usually benign:
  provenance was recorded for a large media binary (`.mp4`, …) that is `_SKIP_EXT` and was
  never indexed anyway, then pruned from raw per [ADR 0002](../decisions/0002-large-binary-handling.md).

## Usage

```bash
# The corpus manifest is config/provenance-manifest.json (the "expected" set).
uv run goldberg audit --manifest config/provenance-manifest.json

# List the specific gaps:
uv run goldberg audit --manifest config/provenance-manifest.json --missing
uv run goldberg audit --manifest config/provenance-manifest.json --extra

# Also check for documents deleted from goldberg-raw (the orphan axis):
uv run goldberg audit --manifest config/provenance-manifest.json --orphans
```

`--orphans` reads the raw tree from the resolved `raw` project path (a cheap `stat` per
manifest entry — no full re-scan), so run it from an environment where `goldberg-raw` is
mounted (any host with the project config; inside the `mcp`/`ingest` container it is on the
bind mount).

## Reading the output

```
✗ GAPS FOUND
  expected (manifest): 1656
  indexed (actual):    1577
  matched:             1576
  MISSING (not ingested): 80
  extra (no manifest entry): 1
  missing by matter:
       49  (none)
       31  422500059892

Orphans (source deleted from goldberg-raw): ✗ ORPHANS FOUND
  manifest entries checked: 1656
  orphaned (file gone):     36
    of which still indexed: 0  (expungeable from ES)
  - telegram/data/.../20250419_170632_26.mp4  [manifest-only (no ES doc)]
  ...
```

- **`of which still indexed: 0`** is the number that matters for the orphan axis. Zero
  means no stale ES documents — the only orphans are provenance-only entries for pruned
  media. A **non-zero** count means real stale records exist (each printed with its
  `ES _id`) and should be expunged.
- **`MISSING`** is the completeness gap — documents that never ingested. Investigate each
  with `goldberg trace <raw_path>` (it joins the event log / DLQ for the last-known stage
  and reason), and re-drive via startup catch-up (`goldberg ingest catchup`) or a DLQ
  retry.

## Exit codes

| Result | Exit |
|--------|------|
| Complete, and (if `--orphans`) no orphans | `0` |
| Any missing, **or** (with `--orphans`) any orphan | `1` |

```bash
uv run goldberg audit --manifest config/provenance-manifest.json --orphans \
  || echo "corpus has gaps or orphans — investigate"
```

Wire this into the same scheduler that runs `goldberg alert` so silent drops and stale
deletions surface proactively.

## Expunging an indexed orphan (manual — no supported command yet)

There is **no** `goldberg expunge` command. If `--orphans` reports an *indexed* orphan
that must be removed (e.g. privilege, or an order to destroy), remove it by hand:

```bash
# 1. Delete the ES document by the _id the audit printed.
curl -s -X DELETE "$GOLDBERG_ES_URL/goldberg_documents/_doc/<ES _id>"

# 2. Remove its entry from the provenance manifest (keyed by sha256), so the manifest
#    no longer "expects" it. Edit config/provenance-manifest.json and drop the object
#    whose "raw_path" matches, then re-run the audit to confirm it is gone.
```

Re-run `goldberg audit --manifest config/provenance-manifest.json --orphans` afterward to
confirm the orphan count dropped and nothing else regressed. Building a supported
`goldberg expunge <doc_id>` (atomic ES delete + manifest prune + an audit event) is a
known follow-up.

## Related

- [Component health](./component-health.md) — `goldberg doctor` (is the pipeline *up*?).
- `goldberg trace <raw_path|sha256|doc_id>` — why one document did (not) ingest.
- `goldberg alert` — the proactive scheduler-driven check that wraps completeness.
- [ADR 0008](../decisions/0008-observability-architecture.md) — the observability design
  (reconciliation + the 2026-07-25 orphan-axis amendment).
- [architecture §12 / §14](../architecture.md) — reconciliation in context, and the
  deletion limitation stated plainly.
