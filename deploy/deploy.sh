#!/usr/bin/env bash
# Deploy flashgen-mcp to the Lightsail instance via Tailscale.
# Usage: ./deploy/deploy.sh
# Requires: ssh access to flashgen-mcp (Tailscale), cloudflared tunnel already created.

set -euo pipefail

REMOTE="flashgen-mcp"
REMOTE_DIR="/home/ubuntu/flashgen"

echo "==> Syncing code..."
rsync -az --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  --exclude='anki_audio_out' \
  --exclude='.beads' \
  ./ "${REMOTE}:${REMOTE_DIR}/"

echo "==> Installing dependencies..."
ssh "${REMOTE}" "cd ${REMOTE_DIR} && ~/.local/bin/uv sync --no-dev"

echo "==> Restarting flashgen-mcp service..."
ssh "${REMOTE}" "sudo systemctl restart flashgen-mcp"

echo "==> Service status:"
ssh "${REMOTE}" "sudo systemctl status flashgen-mcp --no-pager"

echo ""
echo "Done. Tunnel health: cloudflared tunnel info flashgen-mcp"
