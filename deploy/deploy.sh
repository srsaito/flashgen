#!/usr/bin/env bash
# Deploy FlashGen to the Lightsail instance via Tailscale (Docker Compose model).
# Usage: ./deploy/deploy.sh
# Requires: ssh access to flashgen-mcp (Tailscale); Docker + compose plugin and the
# cloudflared tunnel already set up on the instance. See docs/DEPLOYMENT.md.

set -euo pipefail

REMOTE="flashgen-mcp"
REMOTE_DIR="/home/ubuntu/flashgen"

# rsync has NO default I/O timeout — without --timeout it hangs forever on a stalled
# SSH transport. Bound both the rsync I/O and the SSH connect.
echo "==> Syncing code to ${REMOTE}..."
rsync -az --delete --timeout=60 -e 'ssh -o ConnectTimeout=10' \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  --exclude='anki_audio_out' \
  --exclude='.beads' \
  --exclude='.env' \
  --exclude='build' \
  --exclude='dist' \
  --exclude='.coverage' \
  --exclude='.pytest_cache' \
  ./ "${REMOTE}:${REMOTE_DIR}/"

# Build + roll both containers. Run from deploy/ so the compose project is "deploy".
# entrypoint.sh refreshes the bundled add-ons on start, so image changes deploy onto
# the existing anki-data volume without wiping the collection. `up -d` stops the old
# containers gracefully (SIGTERM) so Anki flushes state.
echo "==> Building images..."
ssh -o ConnectTimeout=10 "${REMOTE}" "cd ${REMOTE_DIR}/deploy && docker compose build"

echo "==> Rolling containers..."
ssh -o ConnectTimeout=10 "${REMOTE}" "cd ${REMOTE_DIR}/deploy && docker compose up -d"

echo "==> Status:"
ssh -o ConnectTimeout=10 "${REMOTE}" "cd ${REMOTE_DIR}/deploy && docker compose ps"

echo ""
echo "Health (local):  ssh ${REMOTE} 'curl -s http://127.0.0.1:8000/health'"
echo "Health (public): curl -s https://mcp.ssaito.net/health"
echo "Anki sync log:   ssh ${REMOTE} 'docker logs flashgen-anki | grep flashgen-sync'"
