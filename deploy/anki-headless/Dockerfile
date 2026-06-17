FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    ANKI_DATA_DIR=/root/.local/share/Anki2

# System runtime deps for Anki's bundled Qt6. Beyond the X11/XCB libs, Anki's
# Qt6 links QtWebEngine (Chromium), which needs NSS (libnss3/libnspr4 — without
# them the GUI crashes at import with `ImportError: libsmime3.so`) plus the
# GTK/ATK/Cairo/Pango/CUPS/ALSA Chromium runtime libs. (LANG above: Anki refuses
# to start without a UTF-8 locale; C.UTF-8 is built into glibc, no package.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    zstd \
    xvfb \
    x11vnc \
    libgl1 \
    libegl1 \
    libdbus-1-3 \
    libglib2.0-0 \
    libfontconfig1 \
    libfreetype6 \
    libx11-6 \
    libxcb1 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xinerama0 \
    libxcb-xkb1 \
    libxkbcommon-x11-0 \
    libxrender1 \
    libxtst6 \
    libxss1 \
    fonts-noto-cjk \
    libnss3 \
    libnspr4 \
    libxcomposite1 \
    libxcb-cursor0 \
    libasound2t64 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libatspi2.0-0t64 \
    libcups2t64 \
    libgtk-3-0t64 \
    libgdk-pixbuf-2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    libatomic1 \
    libwayland-cursor0 \
    libwayland-egl1 \
    && rm -rf /var/lib/apt/lists/*

# Download and install Anki (bundles Python + Qt6 — no separate Qt install needed).
# We replicate the essential parts of Anki's install.sh — copy the bundle and
# create the `anki` launcher symlink — but SKIP its xdg-mime desktop/MIME
# registration. That step is useless in a headless container and requires
# xdg-utils, without which install.sh exits 127 (`xdg-mime: command not found`).
ARG ANKI_VERSION=24.11
RUN curl -fsSL --retry 3 --retry-connrefused --connect-timeout 20 --max-time 600 \
        "https://github.com/ankitects/anki/releases/download/${ANKI_VERSION}/anki-${ANKI_VERSION}-linux-qt6.tar.zst" \
        -o /tmp/anki.tar.zst \
    && mkdir -p /tmp/anki-src /usr/local/share/anki \
    && tar -I zstd -xf /tmp/anki.tar.zst -C /tmp/anki-src --strip-components=1 \
    && cp -av --no-preserve=owner,context -- /tmp/anki-src/* /usr/local/share/anki/ \
    && ln -sf /usr/local/share/anki/anki /usr/local/bin/anki \
    && rm -rf /tmp/anki.tar.zst /tmp/anki-src

# Fetch Anki Connect Plus — a maintained soft-fork of FooSoft AnkiConnect that
# supports Anki 24.11 via the modern CollectionOp API. The original FooSoft repo
# was archived (Nov 2025) and its addNote path uses removed APIs (requireReset)
# that crash/modal on Anki 24.11. Same HTTP actions + config schema.
RUN curl -fsSL --retry 3 --retry-connrefused --connect-timeout 20 --max-time 180 \
        "https://github.com/Soki-Team/Anki-Connect-Plus/archive/refs/heads/main.tar.gz" \
        -o /tmp/ankiconnect.tar.gz \
    && tar -xzf /tmp/ankiconnect.tar.gz -C /tmp \
    && mkdir -p /opt/ankiconnect \
    && cp -r /tmp/Anki-Connect-Plus-main/plugin/. /opt/ankiconnect/ \
    && rm -rf /tmp/ankiconnect.tar.gz /tmp/Anki-Connect-Plus-main

# Override default config: bind to 0.0.0.0 so other containers on the Docker
# network can reach AnkiConnect. Port is NOT published to the host.
COPY ankiconnect-config.json /opt/ankiconnect/config.json

# FlashGen sync bootstrap addon: headless AnkiWeb login + background auto-sync
# (bootstrapped into the profile's addons21 dir by entrypoint.sh).
COPY flashgen-sync/ /opt/flashgen-sync/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Anki's UI is QtWebEngine (Chromium). The container runs as root, and Chromium's
# zygote refuses to run as root without --no-sandbox (crbug.com/638180), crashing
# the GUI at window open. This env makes QtWebEngine pass --no-sandbox. Placed
# after the heavy download layers so editing it doesn't trigger an Anki re-download.
ENV QTWEBENGINE_DISABLE_SANDBOX=1

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=5 \
    CMD curl -sf -X POST http://localhost:8765 \
        -H "Content-Type: application/json" \
        -d '{"action":"version","version":6}' | grep -q '"result"' || exit 1

EXPOSE 8765

CMD ["/entrypoint.sh"]
