# ADR 0002 — Large-binary handling in `goldberg-raw`

**Status:** Accepted · **Date:** 2026-07-20 · **Mission:** M0 · **Unblocks:** M8 (migration & backfill)

## Context

`goldberg-raw` holds the immutable original documents. Most are small (PDFs,
`.eml`, images), but some are large binaries (scanned bundles, media). The repo's
origin is private GitHub, while the canonical working copy lives on Halob and the
pipeline is triggered by a **Halob-local filesystem watcher** (not by a git push).

Decision drivers: GitHub's per-file and repo-size limits; long-term repo bloat;
preserving **per-file commit-sha provenance** (every derived doc links to its raw
path + commit); keeping **real files in the working tree** so the Halob watcher
sees them; operational cost/complexity. Note that `goldberg-raw` is
**append-mostly / immutable** — files are added once and rarely change, so history
churn is low, but the cumulative size of binaries still accrues in every clone.

Facts: GitHub blocks any file **> 100 MB** on push (warns at 50 MB) and
recommends keeping repositories well under a few GB. git-LFS stores large blobs
out of band as pointers, with a GitHub free tier of ~1 GB storage + 1 GB
bandwidth/month before paid data packs.

## Options considered

### A. Plain git (all files in-repo)
- **+** Simplest; no extra tooling; every file directly versioned.
- **−** A single large scan/media file **> 100 MB** is rejected by GitHub.
- **−** Large binaries bloat every clone permanently; the corpus of PDFs + media
  grows the pack indefinitely.

### B. git-LFS for everything
- **+** Keeps the git tree lean; handles large files uniformly.
- **−** Consumes LFS quota/bandwidth for small text files that plain git handles
  well and that benefit from diffing (`.eml`, `.md`, small PDFs).
- **−** Unnecessary smudge/pointer overhead on trivial text edits.

### C. git-LFS **selectively**, via `.gitattributes` (plain git for text/small)
- **+** Large/binary types (e.g. `*.pdf`, images, video, `*.zip`) go to LFS,
  avoiding the 100 MB limit and keeping the git tree lean.
- **+** Text/small originals (`.eml`, `.md`) stay in plain git — diffable, cheap,
  no quota use.
- **+** Working tree still contains **real (smudged) files**, so the Halob
  watcher triggers normally.
- **+** Provenance preserved: the committed LFS pointer carries a sha and pins the
  object oid; each raw file still has a commit sha for `raw_commit`.
- **−** Requires `git-lfs` installed on the Mac and Halob; LFS quota to watch.

## Decision

**Use git-LFS selectively, configured via `.gitattributes`** in `goldberg-raw`:
route large binary types (PDF, images, video, archives — and anything over a size
threshold) to LFS, and keep text/small originals in plain git. (Chosen option: C
— git-LFS, applied selectively — over plain git and all-LFS.)

## Consequences

- **M8** configures `goldberg-raw/.gitattributes` with the LFS patterns **before**
  backfilling large binaries, and documents installing `git-lfs` on the Mac and
  Halob.
- Provenance model is unchanged: `raw_commit` still pins each file (via its
  pointer commit); deterministic doc-ids are unaffected.
- **Cost mitigation:** the canonical corpus lives on Halob; GitHub is a private
  mirror + provenance ledger. If GitHub LFS quota becomes a constraint, a
  follow-up option is a **self-hosted LFS server on Halob** — noted, not required
  now.
- Setup docs must state the `git-lfs` client prerequisite for any checkout.

## Downstream

Unblocks **M8 (migration & backfill)**: the raw repository's `.gitattributes` and
LFS setup are established as part of landing the corpus.
