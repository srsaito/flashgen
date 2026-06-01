FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    ANKI_DATA_DIR=/root/.local/share/Anki2

# X11 and Qt6 runtime deps; Anki bundles its own Qt but still needs system X11 libs
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/*

# Download and install Anki (bundles Python + Qt6 — no separate Qt install needed)
ARG ANKI_VERSION=24.11
RUN curl -fsSL \
        "https://github.com/ankitects/anki/releases/download/${ANKI_VERSION}/anki-${ANKI_VERSION}-linux-qt6.tar.zst" \
        -o /tmp/anki.tar.zst \
    && mkdir /tmp/anki-src \
    && tar -I zstd -xf /tmp/anki.tar.zst -C /tmp/anki-src --strip-components=1 \
    && cd /tmp/anki-src \
    && bash install.sh \
    && rm -rf /tmp/anki.tar.zst /tmp/anki-src

# Fetch AnkiConnect plugin (2055492159) from GitHub master
RUN curl -fsSL \
        "https://github.com/FooSoft/anki-connect/archive/refs/heads/master.tar.gz" \
        -o /tmp/ankiconnect.tar.gz \
    && tar -xzf /tmp/ankiconnect.tar.gz -C /tmp \
    && mkdir -p /opt/ankiconnect \
    && cp -r /tmp/anki-connect-master/plugin/. /opt/ankiconnect/ \
    && rm -rf /tmp/ankiconnect.tar.gz /tmp/anki-connect-master

# Override default config: bind to 0.0.0.0 so other containers on the Docker
# network can reach AnkiConnect. Port is NOT published to the host.
COPY ankiconnect-config.json /opt/ankiconnect/config.json

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=5 \
    CMD curl -sf -X POST http://localhost:8765 \
        -H "Content-Type: application/json" \
        -d '{"action":"version","version":6}' | grep -q '"result"' || exit 1

EXPOSE 8765

CMD ["/entrypoint.sh"]
