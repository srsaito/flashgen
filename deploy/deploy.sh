#!/usr/bin/env bash
# Deploy FlashGen to the Lightsail instance via Tailscale (Docker Compose model).
#
# Usage:
#   ./deploy/deploy.sh                # flashgen-mcp only — the routine case
#   ./deploy/deploy.sh --with-anki    # both services (rebuilds the ~1.6 GB anki image)
#   ./deploy/deploy.sh --check        # report what needs deploying, change nothing
#   ./deploy/deploy.sh --accept-anki  # record the current anki context as deployed
#
# WHY THE DEFAULT IS MCP-ONLY
# The compose build contexts are cleanly separated:
#   anki          <- deploy/anki-headless/ only
#   flashgen-mcp  <- repo root, via deploy/flashgen-mcp/Dockerfile
# So only a change under deploy/anki-headless/ can affect the anki image. That
# container holds the collection (the deploy_anki-data volume) and its image is
# ~1.6 GB of Ubuntu + Anki + Qt6 + Chromium, so there is no reason to recreate
# it for a Python change.
#
# You do not have to remember that rule: this script computes a checksum of
# deploy/anki-headless/ and compares it to the one recorded at the last anki
# build. If they differ it STOPS rather than silently shipping a stale anki
# image. See docs/DEPLOYMENT.md "Which services to deploy".

set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="flashgen-mcp"
REMOTE_DIR="/home/ubuntu/flashgen"
# Kept OUTSIDE ${REMOTE_DIR} on purpose: rsync --delete would remove any file
# under it that is not in the local tree.
ANKI_MARKER="/home/ubuntu/.flashgen-anki-context-sha"

WITH_ANKI=0
CHECK_ONLY=0
ACCEPT_ANKI=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --with-anki)   WITH_ANKI=1 ;;
    --check)       CHECK_ONLY=1 ;;
    --accept-anki) ACCEPT_ANKI=1 ;;
    --force)       FORCE=1 ;;
    -h|--help)     sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# Deterministic checksum of the anki build context. __pycache__ and .pyc are
# excluded because they are build droppings, not inputs.
anki_context_sha() {
  find deploy/anki-headless -type f \
    -not -path '*__pycache__*' -not -name '*.pyc' -print0 \
    | sort -z | xargs -0 shasum | shasum | awk '{print $1}'
}

ssh_run() { ssh -o ConnectTimeout=10 "${REMOTE}" "$@"; }

LOCAL_ANKI_SHA="$(anki_context_sha)"
REMOTE_ANKI_SHA="$(ssh_run "cat ${ANKI_MARKER} 2>/dev/null || true" | tr -d '[:space:]')"

if [[ "${ACCEPT_ANKI}" == 1 ]]; then
  ssh_run "printf '%s\n' '${LOCAL_ANKI_SHA}' > ${ANKI_MARKER}"
  echo "Recorded deploy/anki-headless/ as deployed: ${LOCAL_ANKI_SHA}"
  exit 0
fi

if [[ -z "${REMOTE_ANKI_SHA}" ]]; then
  ANKI_STATE="unknown"
elif [[ "${REMOTE_ANKI_SHA}" == "${LOCAL_ANKI_SHA}" ]]; then
  ANKI_STATE="current"
else
  ANKI_STATE="stale"
fi

DEPLOYED_SHOWN="${REMOTE_ANKI_SHA:0:12}"
[[ -z "${DEPLOYED_SHOWN}" ]] && DEPLOYED_SHOWN="none recorded"

echo "==> flashgen-mcp:  will deploy (repo root is its build context)"
echo "==> flashgen-anki: ${ANKI_STATE} (local ${LOCAL_ANKI_SHA:0:12}, deployed ${DEPLOYED_SHOWN})"

if [[ "${CHECK_ONLY}" == 1 ]]; then
  case "${ANKI_STATE}" in
    stale)   echo "    deploy/anki-headless/ changed — run with --with-anki" ;;
    unknown) echo "    no baseline recorded — run with --accept-anki if anki is current" ;;
    current) echo "    nothing to do for anki" ;;
  esac
  exit 0
fi

if [[ "${ANKI_STATE}" == "stale" && "${WITH_ANKI}" == 0 && "${FORCE}" == 0 ]]; then
  cat >&2 <<EOF

REFUSING TO DEPLOY: deploy/anki-headless/ has changed since the last anki build.

Shipping only flashgen-mcp would leave a stale anki image running. Either:
  ./deploy/deploy.sh --with-anki   # rebuild both (slow: ~1.6 GB image)
  ./deploy/deploy.sh --force       # mcp only, knowing anki is stale
EOF
  exit 1
fi

if [[ "${ANKI_STATE}" == "unknown" ]]; then
  echo "    WARNING: no anki baseline recorded on the host. Assuming the running"
  echo "    anki image is current. Record it with --accept-anki to silence this."
fi

# rsync has NO default I/O timeout — without --timeout it hangs forever on a
# stalled SSH transport. Bound both the rsync I/O and the SSH connect. The
# whole tree syncs (both build contexts live in it); only the BUILD is scoped.
# .env and deploy/secrets are excluded so host secrets are never overwritten.
echo "==> Syncing code to ${REMOTE}..."
rsync -az --delete --timeout=60 -e 'ssh -o ConnectTimeout=10' \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  --exclude='anki_audio_out' \
  --exclude='.beads' \
  --exclude='.env' \
  --exclude='deploy/secrets' \
  --exclude='build' \
  --exclude='dist' \
  --exclude='.coverage' \
  --exclude='.pytest_cache' \
  ./ "${REMOTE}:${REMOTE_DIR}/"

if [[ "${WITH_ANKI}" == 1 ]]; then
  SERVICES=""   # empty = all services
  echo "==> Building images (both services)..."
else
  SERVICES="flashgen-mcp"
  echo "==> Building images (flashgen-mcp only)..."
fi

# Run from deploy/ so the compose project is "deploy". entrypoint.sh refreshes
# the bundled add-ons on start, so an anki image change deploys onto the
# existing anki-data volume without wiping the collection. `up -d` stops the
# old containers gracefully (SIGTERM) so Anki flushes state.
ssh_run "cd ${REMOTE_DIR}/deploy && docker compose build ${SERVICES}"

echo "==> Rolling containers..."
ssh_run "cd ${REMOTE_DIR}/deploy && docker compose up -d ${SERVICES}"

if [[ "${WITH_ANKI}" == 1 ]]; then
  ssh_run "printf '%s\n' '${LOCAL_ANKI_SHA}' > ${ANKI_MARKER}"
  echo "==> Recorded anki context ${LOCAL_ANKI_SHA:0:12} as deployed."
fi

echo "==> Status:"
ssh_run "cd ${REMOTE_DIR}/deploy && docker compose ps"

echo ""
echo "Image built:     ssh ${REMOTE} 'docker inspect flashgen-mcp:latest --format \"{{.Created}}\"'"
echo "Health (local):  ssh ${REMOTE} 'curl -s http://127.0.0.1:8000/health'"
echo "Health (public): curl -s https://mcp.ssaito.net/health"
echo "Anki sync log:   ssh ${REMOTE} 'docker logs flashgen-anki | grep flashgen-sync | tail'"
