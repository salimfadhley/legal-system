# Wiring the ingest trigger (git hooks → `goldberg.raw.commit`)

**Mission:** event-driven-ingestion (WP04) · **Requirements:** FR-001 (every raw
commit publishes a trigger), FR-002 (the hook must never fail `git`).

This runbook wires a `goldberg-raw` working clone so that **every commit publishes a
`goldberg.raw.commit` event** onto the `GOLDBERG` JetStream stream. The event-driven
ingest processor consumes that event and ingests the changed documents.

The hooks live **in this repo** under [`hooks/`](../../hooks/) and are versioned
here — they are *not* hand-copied into `.git/hooks`. A `goldberg-raw` clone points at
them with `core.hooksPath`, so updating the hook is a `git pull` in this repo, never a
manual copy.

## What the hooks do

| Hook          | Fires on                                   | Publishes with        |
| ------------- | ------------------------------------------ | --------------------- |
| `post-commit` | any authored commit                        | `--source post-commit`|
| `post-merge`  | a **non-fast-forward** pull/merge (merge commit) | `--source post-merge` |

Both run the same one-liner:

```bash
sha="$(git rev-parse HEAD)"
goldberg publish-commit "$sha" --source <post-commit|post-merge> >/dev/null 2>&1 || \
  logger -t goldberg-hook "publish-commit failed for $sha (will be caught up at startup)"
exit 0
```

**They always `exit 0`.** A broker outage, a missing `goldberg` binary, or any other
failure is swallowed (stderr redirected, a note left via `logger`) and the developer's
`git commit` still succeeds. The dropped event is recovered by the processor's startup
catch-up (see below). This is the single most important property of the trigger: a hook
that could fail `git` would block commits to the legal corpus.

## Prerequisite: `goldberg` must be on PATH in the committing environment

The hook calls the bare command `goldberg`. Whatever shell/user creates commits in the
`goldberg-raw` clone (a person on-box, a cron job, an editor's git integration) must
resolve `goldberg` on its `PATH`. Two options if it does not:

- **Activate the venv** that provides `goldberg` in the environment that commits
  (e.g. source the project venv in the login shell / systemd unit that runs git), **or**
- **Use an absolute path**: copy the hook and replace `goldberg` with the absolute
  path to the binary (e.g. `/opt/goldberg/.venv/bin/goldberg`). Because the hooks are
  versioned here, prefer the venv-activation route so the checked-in hook stays generic.

If `goldberg` is not resolvable, the hook still exits 0 (the commit is never blocked)
and the event is recovered by startup catch-up — but no live trigger fires until PATH
is fixed.

## Wiring a `goldberg-raw` clone

Point the clone's `core.hooksPath` at this repo's `hooks/` directory (use an
**absolute** path so it resolves regardless of the clone's working directory):

```bash
# <raw>    = path to the goldberg-raw working clone
# <system> = path to this goldberg-system checkout (contains hooks/)
git -C <raw> config core.hooksPath <system>/hooks
```

Example on Halob:

```bash
git -C /share/home/sal/work/project_goldberg/goldberg-raw \
    config core.hooksPath /share/home/sal/work/project_goldberg/goldberg-system/hooks
```

Verify the setting and that the hooks are executable:

```bash
git -C <raw> config --get core.hooksPath          # → <system>/hooks
ls -l <system>/hooks/post-commit <system>/hooks/post-merge   # both -rwx…
```

> `core.hooksPath` makes git ignore `.git/hooks` entirely and use only this directory.
> Both `post-commit` and `post-merge` are shipped here, so both events are covered.

## Verify the wiring (empty commit)

Make a throwaway commit in the wired clone and confirm a message lands on the stream:

```bash
git -C <raw> commit --allow-empty -m "test: ingest trigger wiring"
```

Then confirm a `goldberg.raw.commit` message reached the `GOLDBERG` stream. With the
NATS CLI against the broker the processor uses:

```bash
# newest message on the commit subject (default subject: goldberg.raw.commit)
nats stream view GOLDBERG --subject goldberg.raw.commit --last
# or watch live while you commit:
nats sub goldberg.raw.commit
```

You can also publish directly (bypassing git) to confirm the CLI + broker independently:

```bash
goldberg publish-commit "$(git -C <raw> rev-parse HEAD)" --source post-commit
# → published commit <sha> to goldberg.raw.commit
```

(Stream `GOLDBERG`, subject `goldberg.raw.commit`, and the `GOLDBERG_NATS_*` env
overrides are defined by the WP03 messaging config; the hook inherits whatever the
committing environment sets.)

## The fast-forward-pull gap (documented, accepted)

A **fast-forward** `git pull` — the common case when the local branch has no divergent
commits — moves `HEAD` **without creating a merge commit**, so it fires **neither**
`post-commit` **nor** `post-merge`. Commits arriving that way publish no live trigger.

This gap is **intentional and covered**: the ingest processor runs a **bounded startup
catch-up** every time it starts (`goldberg ingest-serve`, unless `--no-catchup`), which
reconciles the corpus against the raw tree and ingests anything the event stream missed.
Fast-forwarded-in commits are picked up on the next processor start.

## Manual escape hatch: `goldberg ingest catchup`

To close the gap on demand (after a fast-forward pull, a broker outage, or any doubt
about delivery) without restarting the service, run one bounded catch-up pass:

```bash
goldberg ingest catchup             # one bounded pass over goldberg-raw, then exit
goldberg ingest catchup --batch 200 # widen the bound if a large backlog accumulated
```

It ingests only documents not already indexed, then exits (no loop). Safe to run
repeatedly; it is idempotent with respect to already-indexed documents.

## Validate delivery end-to-end (operator, run once on Halob)

There is **no automated test for live delivery** — it requires the real `goldberg-raw`
clone, a running broker, and the ingest processor, none of which are available in unit
tests. Run these commands **once on Halob**, with the processor running, to confirm a
real commit produces a stream message *and* gets indexed:

```bash
# 0. Preconditions: clone wired (core.hooksPath set), broker up, processor running:
git -C <raw> config --get core.hooksPath        # → <system>/hooks
goldberg ingest-serve &                          # startup catch-up, then consume

# 1. Note the current indexed count, then make a real (non-empty) commit in <raw>:
goldberg status --yaml | grep -i indexed         # baseline
#   …add/modify a document leaf under <raw>, then:
git -C <raw> add -A && git -C <raw> commit -m "test: end-to-end ingest trigger"

# 2. Confirm the trigger reached the stream:
nats stream view GOLDBERG --subject goldberg.raw.commit --last   # newest commit msg

# 3. Confirm the processor ingested it (count rises / trace the new doc):
goldberg status --yaml | grep -i indexed         # should have advanced
goldberg trace <raw_path-of-the-new-doc>         # per-document trace

# 4. Clean up the throwaway commit if it was only a test:
git -C <raw> reset --hard HEAD~1
```

If step 2 shows a message but step 3 does not advance, the trigger is wired correctly
and the problem is downstream (processor/indexer) — check `goldberg dlq` and the
processor logs, not the hooks.
