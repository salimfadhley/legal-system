# Deploying / updating the ingest service (and enabling the goldberg-extracted mirror)

The event-driven ingest service (`goldberg ingest-serve`, ADR 0013) runs on Halob as a
**Docker container** defined in [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml).
Its code is **baked into the image at build time** (`deploy/Dockerfile.ingest` COPYs
`src/`), so shipping a code change means **rebuilding the image**, not just restarting.

> **Access required:** this needs Docker on Halob, i.e. root / a password'd `sudo` or the
> docker group. It is **not** currently a Portainer-managed stack (no `goldberg` stack in
> Portainer — it was brought up with a manual `docker compose up`), so the Portainer API
> key cannot rebuild it. Either run the commands below on Halob, or register the stack in
> Portainer first (see the last section).

## Enable the goldberg-extracted mirror (ADR 0015)

The compose is already wired: the `ingest` service mounts `goldberg-extracted` read-write
at `/data/goldberg-extracted` and passes `--extracted-root /data/goldberg-extracted`, so
every newly-ingested document is mirrored there as a frontmatter `.md` as it is indexed.
To make it live, deploy the updated image.

### 1. Get the new code onto Halob

The repo working tree is at `/share/home/sal/work/project_goldberg/goldberg-system` (same
tree as the Mac mount). Ensure it is current:

```bash
cd /share/home/sal/work/project_goldberg/goldberg-system && git pull
```

`goldberg-extracted` must exist beside it (it does — populated by the backfill):
`/share/home/sal/work/project_goldberg/goldberg-extracted`. The compose default
`GOLDBERG_EXTRACTED_PATH=../goldberg-extracted` resolves to it.

### 2. Rebuild + restart (one command)

```bash
cd /share/home/sal/work/project_goldberg/goldberg-system/deploy
sudo docker compose up -d --build ingest
```

`--build` picks up the new `--extracted-root` code; `up -d` recreates just the `ingest`
container. Verify:

```bash
sudo docker compose logs -f ingest        # expect "mirroring extracted docs → /data/goldberg-extracted"
curl -s localhost:8098/health             # last activity + catch-up summary
```

### 3. Persist the mirror to the remote (cadence)

The sink writes files into the mounted working tree but does **not** commit. A periodic
job keeps the remote current (run on the host, where the git identity/credentials live):

```bash
cd /share/home/sal/work/project_goldberg/goldberg-extracted
git add -A && git commit -q -m "mirror: ingest deltas $(date -u +%FT%TZ)" && git push
```

Wire it as a cron/systemd-timer (e.g. hourly). Until then the working tree is current but
the GitHub remote only reflects the last manual push.

## Rolling back

`git checkout <prev> -- deploy/ src/` then re-run step 2, or drop the `--extracted-root`
line from the command in `docker-compose.yml` and redeploy — the sink is purely additive,
so removing it stops mirroring without affecting ES ingestion.

## Optional: make it Portainer-managed

To manage future deploys via the Portainer API (like the `papra` / `silverbullet-goldberg`
stacks): create a stack named `goldberg` from `deploy/docker-compose.yml` on endpoint 2,
supplying the env. Thereafter rebuild/redeploy is an API call with the Portainer token
(no host shell) — see the Mind-of-Steele Portainer doc.
