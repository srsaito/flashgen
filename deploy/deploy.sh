#!/usr/bin/env bash
# Deploy flashgen-mcp to the Lightsail instance via Tailscale.
# Usage: ./deploy/deploy.sh
# Requires: ssh access to ubuntu@100.84.134.120 (Tailscale)

set -euo pipefail

REMOTE="ubuntu@100.84.134.120"
REMOTE_DIR="/home/ubuntu/flashgen"

echo "==> Syncing code..."
rsync -az --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  --exclude='anki_audio_out' \
  ./ "${REMOTE}:${REMOTE_DIR}/"

echo "==> Installing dependencies..."
ssh "${REMOTE}" "cd ${REMOTE_DIR} && uv sync --no-dev"

echo "==> Installing systemd service..."
ssh "${REMOTE}" "sudo cp ${REMOTE_DIR}/deploy/flashgen-mcp.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable flashgen-mcp && sudo systemctl restart flashgen-mcp"

echo "==> Installing nginx config..."
ssh "${REMOTE}" "sudo cp ${REMOTE_DIR}/deploy/nginx-mcp.conf /etc/nginx/sites-available/mcp.ssaito.net && sudo ln -sf /etc/nginx/sites-available/mcp.ssaito.net /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx"

echo "==> Service status:"
ssh "${REMOTE}" "sudo systemctl status flashgen-mcp --no-pager"

echo ""
echo "Done. Check health: curl http://100.84.134.120/health"
