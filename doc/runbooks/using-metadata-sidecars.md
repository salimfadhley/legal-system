# How to correct or annotate a document's metadata (sidecars)

The auto-generated metadata is produced from one file in isolation, so it is
detailed but context-blind. This is how you override it authoritatively, in the repo,
next to the file. (Design/rationale: goldberg-system `doc/system/metadata.md`.)

## Where a sidecar goes and what it's called

Put a file **next to** the document, named `<the document's exact filename>.metadata.yaml`:

```
evidence/…/SM01_summary_overview_of_third_party_tax_specialist_services.pdf
evidence/…/SM01_summary_overview_of_third_party_tax_specialist_services.pdf.metadata.yaml   ← sidecar
```

A file named exactly `metadata.yaml` is **folder** defaults (applies to everything below
it). `<name>.metadata.yaml` is a **sidecar** for that one file. Sidecars are never
themselves ingested.

## Resolution order (most-specific wins)

```
repo-root metadata.yaml → … → the file's folder metadata.yaml → <file>.metadata.yaml
```

The last layer to set a field wins. **Scalars** (author, claim_source, no_index, date…)
override; **lists** (matters, keywords, parties) union.

## Valid keys (with one example each)

```yaml
author: Paul Keitch                       # who is speaking (overrides the inferred author)
claim_source: Paul Keitch                 # authoritative speaker for EVERY claim in this file
document_type: witness statement
matters: ["422500059892"]                 # list — unions with inherited
parties: ["Simon Goldberg", "Salim Fadhley"]

date: "2026-07-17"                         # the document's OWN date, where it has one
date_basis: "PDF CreationDate"            # free text: how we know it. "on its face" if printed on it
date_uncertain: true                      # defaults TRUE unless date_basis is "on its face"

method: "opened legislation.gov.uk XML for SI 2020/759 on 2026-08-07, diffed"  # what you ACTUALLY did
source_channel: "disclosure under rights of access"     # how the document reached us
obtained_note: "watermarked 'Not for use in legal proceedings'"

superseded_by: "analysis/2026-08-07_corrected_version.md"   # doc_id or raw_path that replaces this

no_index: true                            # remove THIS file from search (legal restriction)
no_index_reason: "CPR 32.12 — restricted witness statement, written undertaking"  # REQUIRED with no_index

notes: |                                  # free prose — see below, it does two jobs
  Exhibit SM/01, the disclosure officer's own statement. UNDATEABLE: no date on its
  five pages; do NOT treat the 7 Aug 2026 scan timestamp as the document date. Served
  by Oct 2025. Every figure is as at an unknown date.
```

There is **no `verified: true`** and there never will be — a boolean is not a warranty.
Record what you did in `method`; absence of `method` means unverified.

### What `notes` does
1. It is fed to the enricher as **authoritative context** before extraction, so the
   summary/claims/attribution reflect what you know, not what the file says blind.
2. It is appended to the indexed content, **fenced** so it can never be read as the
   document's own words:
   ```
   [ANNOTATION — casework, not part of this document]
   …your note…
   [/ANNOTATION]
   ```
   and it is a searchable `notes` field in its own right.

## What happens if you make a mistake

A sidecar with an **unknown/typo'd key**, malformed YAML, or `no_index` without
`no_index_reason` is **dropped whole** and the document is ingested with its normal
(inherited) metadata, carrying a visible `metadata_error`. A typo can **never** make the
document vanish — loud and present, never silent and absent. Run the linter before you
rely on a sidecar:

```
legal_system metadata lint <path-in-goldberg-raw>
```

It self-tests first and refuses to report if it can't tell a good sidecar from a bad one;
then it flags unknown keys, bad YAML, orphan sidecars, and no_index-without-reason.

## How to make it take effect, and how to prove it did

- **New documents**: the sidecar is applied automatically at ingestion.
- **A document already indexed**: commit the sidecar, then it is re-processed and the
  sidecar takes effect. (Trigger: currently `system` runs
  `legal_system re-enrich --path <raw_path> --raw-root <goldberg-raw>` for you — ask, or
  once the live service is on this build, the sidecar commit re-ingests the path on its own.
  `system` will confirm which is live.)
- **Prove it through the tools you actually use** (not by reading the sidecar back — a
  sidecar that parses but never reaches the index is the failure to catch):
  - `search_evidence` returns the corrected `author`.
  - `find_claims` attributes to the corrected `claim_source`.
  - the note appears on the document, FENCED.
  - a `no_index` file disappears from `search_evidence` while its siblings remain.
  - the new MCP `raw_index_gap` tool tells you if a path is in raw but not the index —
    so "sidecar didn't take effect" and "document isn't indexed at all" are distinguishable.
