# Deploying / updating the ingest service (Portainer, no host builds)

The event-driven ingest service (`goldberg ingest-serve`, ADR 0013) runs on Halob as a
Docker container. **Halob is a low-spec 4-core Celeron — never build images on it, and
never run `docker` on the host directly.** Deploys go **through Portainer** (endpoint
`2`, `http://192.168.86.31:19900`, `X-API-Key` — token in the Mind-of-Steele Portainer
doc): the image is built **elsewhere** and Halob only ever runs the pre-built artefact.

The image bakes the code in at build time (`deploy/Dockerfile.ingest` COPYs `src/`), so
shipping a code change = build a new image + get it onto Halob + recreate the container.

## Go-forward (preferred): GitHub Actions → ghcr → Portainer

The durable mechanism (see `.github/workflows/`): a push to `main` builds `linux/amd64`
in GitHub Actions and pushes to `ghcr.io/salimfadhley/legal-ingest` (using the
built-in `GITHUB_TOKEN` — no credential to manage on the push side). Portainer then
deploys the pre-built image (Halob pulls, never builds). One-time setup still needed:
let Portainer/Halob **pull** the image — either make the ghcr package public, or register
a fine-grained read-`packages` token in Portainer — and define the `goldberg` stack.

## Interim (what deployed the extracted-mirror change, 2026-08-06)

Until the CI stack is wired, deploy by building on a capable host and loading the image
onto Halob through Portainer's Docker API:

```bash
# 1. Build linux/amd64 on a capable host (a Mac, CI — NOT Halob):
docker buildx build --platform linux/amd64 -f deploy/Dockerfile.ingest \
  -t legal-ingest:extracted --load .

# 2. Package and load it onto Halob's Docker THROUGH Portainer (no host build, no registry):
docker save legal-ingest:extracted | gzip > /tmp/img.tar.gz
curl -s -X POST -H "X-API-Key: $PORTAINER_KEY" -H "Content-Type: application/x-tar" \
  --data-binary @/tmp/img.tar.gz \
  http://192.168.86.31:19900/api/endpoints/2/docker/images/load

# 3. Cut over via the Portainer Docker API (all POSTs to .../api/endpoints/2/docker):
#    - stop old:    /containers/legal-ingest/stop
#    - rename aside:/containers/legal-ingest/rename?name=legal-ingest-old   (rollback)
#    - create new:  /containers/create?name=legal-ingest   (body = replicate the old
#      container's Env + host-network + binds, ADD the goldberg-extracted rw mount and
#      the --extracted-root flag)
#    - start:       /containers/<id>/start
```

Replicate the running container's config exactly (`GET /containers/legal-ingest/json`)
and add only:
- bind `…/goldberg-extracted:/data/goldberg-extracted` (read-write), and
- command args `--extracted-root /data/goldberg-extracted`.

Verify from the container logs (`GET /containers/<id>/logs`): expect
`mirroring extracted docs → /data/goldberg-extracted` and `running startup catch-up`,
`RestartCount 0`. The `/health` endpoint (port 8098) only opens **after** the startup
catch-up (up to the 900s start-period) — read the logs, don't wait on `/health`.

**Rollback:** the previous container is kept stopped as `legal-ingest-old` on the old
image. To revert: stop/remove the new `legal-ingest`, rename `legal-ingest-old`
back to `legal-ingest`, start it. Remove `legal-ingest-old` once the new deployment
is confirmed stable.

## Persisting the mirror to the remote (cadence)

The sink writes files into the mounted `goldberg-extracted` working tree but does not
commit. A periodic host job keeps the GitHub remote current:

```bash
cd /share/home/sal/work/project_goldberg/goldberg-extracted
git add -A && git commit -q -m "mirror: ingest deltas $(date -u +%FT%TZ)" && git push
```

Wire it as a cron/systemd-timer. Until then the working tree is current but the remote
only reflects the last manual push (the backfill).
