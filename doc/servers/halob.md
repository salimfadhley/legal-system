# halob

Halob is the home NAS that runs the entire platform.

- **Host / IP**: `halob` / `192.168.86.31`
- **Access**: `ssh sal@halob`; home volume SMB-mounted on the Mac as `/Volumes/Home` (→ Halob `/share/home/...`)
- **Hardware**: Intel Celeron N5105, 31 GB RAM, ~15 TB RAID (~9.3 TB free)
- **Docker**: 28.x, managed via **Portainer** (https://halob:19943)

## Services this platform uses

| Service | Endpoint | Use |
|---|---|---|
| Elasticsearch | `http://halob:9200` | The corpus (`goldberg_documents`), the event log (`goldberg_pipeline_events`), the wiki index (`silverbullet-goldberg`). `goldberg_files` is the **frozen legacy** index — not used by this pipeline. |
| NATS + JetStream | `nats://halob:4222` (UI `:31311`) | Stream `GOLDBERG`, subject `goldberg.raw.commit` — the ingest trigger |
| Docker + Portainer | `:19943` | Runs the processing stack: `docling` (`:5001`), `ingest` (`/health` on `:8098`), `mcp` (`:8765`) |
| SilverBullet | `:3100` | The concept-wiki space |
| Copyparty / Syncthing | `:3923` / `:28384` | Optional file-drop inbox |

Elasticsearch and NATS are **shared, stateful infrastructure** that outlives any redeploy; the processing services are stateless and portable ([ADR 0012](../decisions/0012-deployment-topology.md)).

The project checkouts live under `/Volumes/Home/work/project_goldberg/` (Halob `/share/home/.../work/project_goldberg/`).

> Fuller, canonical server documentation lives in the Mind of Steele project at `~/workspace/mind_of_steele/doc/servers/halob.md`. This file records only what goldberg-system depends on.
>
> **Security note:** the MoS Obsidian doc contains a hard-coded password — rotate / move to a secret before relying on that vault.
