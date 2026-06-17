# FlashGen Deployment (Lightsail)

How the FlashGen MCP server runs in production, how to deploy changes, and how to
recover it. The server is hosted on an AWS Lightsail instance and reached by Claude
as a custom MCP connector at `https://mcp.ssaito.net`.

> Source of truth is this repo. Edits happen on the dev machine and are pushed to
> the instance; the instance runs containers built from these files. There is **no**
> in-place editing on the server.

## Architecture

```
Claude (web / desktop / iOS / Code)
      │  HTTPS, OAuth bearer
      ▼
mcp.ssaito.net ──► Cloudflare ──► cloudflared tunnel (host systemd)
                                        │ http://127.0.0.1:8000
                                        ▼
                           ┌─────────────────────────────┐  docker compose (project "deploy")
                           │ flashgen-mcp container       │  publishes 127.0.0.1:8000
                           │  uvicorn flashgen_mcp.app    │
                           │  validate/create_flashcard   │
                           └──────────────┬──────────────┘
                                          │ http://anki:8765  (flashgen-net, private)
                                          ▼
                           ┌─────────────────────────────┐
                           │ flashgen-anki container      │  Xvfb + Anki 24.11 (Qt6)
                           │  Anki Connect Plus (:8765)   │  + flashgen-sync addon
                           │  anki-data volume (profile)  │  ──► AnkiWeb (auto-login + sync)
                           └─────────────────────────────┘
                                          ▼
                              AnkiWeb  ◄──►  your iPhone / Desktop Anki
```

Card flow: Claude → MCP → `create_flashcard` (TTS via Gemini/OpenAI) → Anki Connect
Plus `addNote` → `flashgen-sync` addon auto-syncs to AnkiWeb → your devices.

## Instance facts
- AWS Lightsail, **Ubuntu 24.04**, **x86_64** (required — Anki's Linux build is x86_64-only), 2 vCPU / 3.7 GB RAM + **2 GB swapfile** (`/swapfile`, in `/etc/fstab`).
- Tailscale IP `100.84.134.120`. **All public Lightsail firewall ports are closed.**
- Repo at `/home/ubuntu/flashgen` (an rsync'd copy — `.git`/`.venv`/`.env` excluded; not a git checkout).

## Access (no public ports)
- **Primary SSH** — Tailscale: `ssh flashgen-mcp` (Tailscale key-expiry disabled for the node).
- **Break-glass SSH** — over the Cloudflare tunnel: `ssh flashgen-mcp-cf` (`ProxyCommand cloudflared access ssh --hostname ssh.ssaito.net`), gated by Cloudflare Access (one-time PIN to the allow-listed email).
- **Last resort** — the Lightsail browser SSH console is **not** viable (incompatible with the Tailscale-only sshd binding); use a Lightsail snapshot/disk recovery instead.

## Prerequisites on the instance (one-time)
- Docker Engine + the **compose v2 plugin** at `/usr/local/lib/docker/cli-plugins/docker-compose`
  (the Ubuntu `docker.io` package does not bundle it; install the binary from the docker/compose releases).
- `cloudflared` (host systemd `cloudflared.service`) with the tunnel + `/etc/cloudflared/config.yml`
  (checked-in copy at `deploy/cloudflared-config.yml`). Ingress: `mcp.ssaito.net → http://127.0.0.1:8000`,
  `ssh.ssaito.net → ssh://127.0.0.1:22`.
- `tailscaled` (host systemd).
- The old systemd `flashgen-mcp.service` (uvicorn on the host) is **superseded by the container and
  is stopped + disabled.** `deploy/flashgen-mcp.service` is kept only for historical reference.

## Environment (`/home/ubuntu/flashgen/.env`)
Copy `.env.example` → `.env` and fill in. Loaded by both containers via `env_file: ../.env`.

| Var | Used by | Purpose |
|-----|---------|---------|
| `FLASHGEN_MCP_TOKEN` | flashgen-mcp | Bearer token the OAuth flow issues to Claude |
| `GEMINI_API_KEY` | flashgen-mcp | Default TTS (Gemini); also model access |
| `OPENAI_API_KEY` | flashgen-mcp | OpenAI TTS / auto-translation |
| `ANKIWEB_USERNAME` | flashgen-anki | AnkiWeb login (flashgen-sync addon) |
| `ANKIWEB_PASSWORD` | flashgen-anki | AnkiWeb login (flashgen-sync addon) |

`ANKI_CONNECT_URL` is **not** set here — `docker-compose.yml` overrides it to `http://anki:8765`
for the flashgen-mcp container.

## Deploy a change
From the dev machine (run `./deploy/deploy.sh`, or manually):

```bash
# 1. sync code to the instance (excludes .git/.venv/.env)
rsync -az --delete --timeout=60 -e 'ssh -o ConnectTimeout=10' \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='*.egg-info' \
  --exclude='anki_audio_out' --exclude='.beads' --exclude='.env' \
  --exclude='build' --exclude='dist' --exclude='.coverage' --exclude='.pytest_cache' \
  ./ flashgen-mcp:/home/ubuntu/flashgen/

# 2. rebuild + roll the containers (run from the deploy/ dir → compose project "deploy")
ssh flashgen-mcp 'cd /home/ubuntu/flashgen/deploy && docker compose build && docker compose up -d'
```

Notes:
- The **anki image is heavy** (Ubuntu + Anki + Qt6 + Chromium libs, ~1.4 GB); the first build is slow,
  later builds reuse cached layers.
- `entrypoint.sh` **refreshes both add-ons on every start** (Anki Connect Plus + `flashgen-sync`), so
  image updates deploy onto the existing volume **without touching the collection**.
- `docker compose up` stops the old container **gracefully (SIGTERM)** — important so Anki flushes state.
- Use rsync **with `--timeout=60`** (rsync has no default I/O timeout and will hang forever on a stalled
  SSH transport); for a single file, prefer `scp`.

## Operations
```bash
cd /home/ubuntu/flashgen/deploy
docker compose ps                         # both should be (healthy)
docker compose logs -f flashgen-mcp       # MCP server
docker logs flashgen-anki | grep flashgen-sync   # addon login + "sync ok" lines
curl -s http://127.0.0.1:8000/health      # {"ok":true} when anki reachable
curl -s -o/dev/null -w '%{http_code}\n' https://mcp.ssaito.net/health   # 200 via cloudflared
```
AnkiConnect (8765) is **not** published to the host; reach it from the host via the container IP
(`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' flashgen-anki`) — used by
`tests/test_anki_runtime.py`.

## Add the Claude connector
Settings → Connectors → **Add custom connector** → URL `https://mcp.ssaito.net/mcp` → authorize on the
"Authorize FlashGen MCP" page. Set `create_flashcard` to **needs approval**, `validate_flashcard` to
**always allow**. (Approval is enforced client-side; the server just executes authorized calls.)

## Disaster recovery
The Anki profile/collection lives **only** in the `deploy_anki-data` Docker volume (AnkiWeb is the
cloud copy). The `flashgen-sync` addon logs into AnkiWeb on every start and does **incremental** syncs.

- **Container/image rebuilt, volume intact** → nothing to do; the addon re-logs-in and resumes syncing.
- **Volume wiped/recreated** → the empty collection needs an initial **full download** from AnkiWeb,
  which the addon currently **skips** (it only does safe incremental syncs, never an automatic one-way
  overwrite). Until [`flashgen-2b9`](../.beads) automates this, recover manually:
  1. `docker compose run -d --name anki-setup -e ENABLE_VNC=1 -e VNC_PASSWORD=<pw> -p 127.0.0.1:5900:5900 anki`
  2. `ssh -L 5901:127.0.0.1:5900 flashgen-mcp`, connect a VNC viewer to `localhost:5901`
  3. In Anki: Sync → **Download from AnkiWeb** → confirm decks appear
  4. `docker rm -f anki-setup` then `docker compose up -d` (the volume now holds the collection)
- **Instance lost** → restore from a Lightsail snapshot, or re-provision and redeploy (the volume is the
  only stateful piece; everything else is in this repo + `.env`).

`flashgen-2b9` will make the wiped-volume case automatic: when a full sync is required **and** the local
collection is empty (nothing to lose), perform `full_download` instead of skipping.

## Security posture
- No public inbound ports (Lightsail firewall closed); access via Tailscale + the Access-gated Cloudflare tunnel.
- MCP auth: OAuth 2.1 + bearer (`FLASHGEN_MCP_TOKEN`). AnkiConnect bound to the private compose network only.
- Secrets (API keys, AnkiWeb password) live in `/home/ubuntu/flashgen/.env` (root-readable), never in the image or git.
