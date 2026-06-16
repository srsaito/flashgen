#!/usr/bin/env bash
set -euo pipefail

ADDON_DIR="${ANKI_DATA_DIR}/addons21/2055492159"
ADDON_SRC="/opt/ankiconnect"

# Bootstrap AnkiConnect on first container start (volume is empty)
if [ ! -f "${ADDON_DIR}/__init__.py" ]; then
    echo "[entrypoint] Installing AnkiConnect add-on..."
    mkdir -p "${ADDON_DIR}"
    cp -r "${ADDON_SRC}/." "${ADDON_DIR}/"
fi

# Start Xvfb virtual display
echo "[entrypoint] Starting Xvfb on :99..."
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &
sleep 2

# Optional VNC server for initial GUI setup (deck creation, AnkiWeb sync).
# Enable with: docker compose run --rm -e ENABLE_VNC=1 -p 5900:5900 anki
# Then tunnel: ssh -L 5900:localhost:5900 flashgen-mcp
# Connect any VNC viewer to localhost:5900
if [ "${ENABLE_VNC:-0}" = "1" ]; then
    if [ -n "${VNC_PASSWORD:-}" ]; then
        # macOS Screen Sharing refuses no-auth ("None") VNC, so support a password.
        echo "[entrypoint] Starting x11vnc on port 5900 (password auth)..."
        x11vnc -storepasswd "${VNC_PASSWORD}" /root/.vncpass >/dev/null 2>&1
        x11vnc -display :99 -rfbauth /root/.vncpass -listen 0.0.0.0 -noipv6 -xkb \
               -forever -shared -noxrecord -noxdamage &
    else
        echo "[entrypoint] Starting x11vnc on port 5900 (no password)..."
        x11vnc -display :99 -nopw -listen 0.0.0.0 -noipv6 -xkb -forever -shared \
               -noxrecord -noxdamage &
    fi
fi

echo "[entrypoint] Starting Anki (AnkiConnect will listen on :8765)..."
exec anki --no-auto-update
