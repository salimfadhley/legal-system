# halob

Halob is the home NAS that runs the entire platform.

- **Host / IP**: `halob` / `192.168.86.31`
- **Access**: `ssh sal@halob`; home volume SMB-mounted on the Mac as `/Volumes/Home` (→ Halob `/share/home/...`)
- **Hardware**: Intel Celeron N5105, 31 GB RAM, ~15 TB RAID (~9.3 TB free)
- **Docker**: 28.x, managed via **Portainer** (https://halob:19943)

## Services this platform uses

| Service | Endpoint | Use |
|---|---|---|
| Elasticsearch | `http://halob:9200` (index `goldberg_files`) | search index |
| NATS | `nats://halob:4222` (UI `:31311`) | trigger / event bus |
| Docker + Portainer | `:19943` | runs the `live-index` service |
| Copyparty / Syncthing | `:3923` / `:28384` | optional file-drop inbox |
| Obsidian | vault at `/share/Docker/Obsidian/config` (`:8780/:8781`) | candidate LLM-wiki sink |

The project checkouts live under `/Volumes/Home/work/project_goldberg/` (Halob `/share/home/.../work/project_goldberg/`).

> Fuller, canonical server documentation lives in the Mind of Steele project at `~/workspace/mind_of_steele/doc/servers/halob.md`. This file records only what goldberg-system depends on.
>
> **Security note:** the MoS Obsidian doc contains a hard-coded password — rotate / move to a secret before relying on that vault.
