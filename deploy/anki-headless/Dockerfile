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
#
# The snappy/opus/webp/minizip group is required from Anki 26.09 (Qt 6.11) on:
# that Qt links QtWebEngine against those as SYSTEM libraries where Qt 6.6
# (Anki 24.11) bundled them, so without them `import aqt.qt` dies with
# `ImportError: libsnappy.so.1`. libpulse0/libspeechd2 back Qt's Multimedia and
# TextToSpeech modules, and libxcursor1/libxi6/libxkbcommon0/libxrandr2 are new
# entries in 26.09's own install.sh DEBIAN_DEPS. `git` is the AnkiConnect fetch
# (see below) — SourceHut rejects plain tarball curls.
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
    libxcursor1 \
    libxi6 \
    libxkbcommon0 \
    libxrandr2 \
    libsnappy1v5 \
    libopus0 \
    libwebpdemux2 \
    libwebpmux3 \
    libminizip1t64 \
    libpulse0 \
    libspeechd2 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Download and install Anki (bundles Python + Qt6 — no separate Qt install needed).
# We replicate the essential parts of Anki's install.sh — copy the bundle and
# create the `anki` launcher symlink — but SKIP its xdg-mime desktop/MIME
# registration. That step is useless in a headless container and requires
# xdg-utils, without which install.sh exits 127 (`xdg-mime: command not found`).
#
# ANKI_VERSION pins the release. From 26.08 on, the Linux assets are per-arch
# (anki-<v>-linux-{x86_64,aarch64}.tar.zst) rather than the single -linux-qt6
# asset 24.11 shipped, so the URL is built from TARGETARCH. Production is
# x86_64; arm64 is here so the image also builds natively on an Apple Silicon
# Mac, which is how upgrades get rehearsed off-prod (bead flashgen-mfb).
ARG ANKI_VERSION=26.09.3
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) ANKI_ARCH=x86_64 ;; \
      arm64) ANKI_ARCH=aarch64 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 --retry-connrefused --connect-timeout 20 --max-time 900 \
        "https://github.com/ankitects/anki/releases/download/${ANKI_VERSION}/anki-${ANKI_VERSION}-linux-${ANKI_ARCH}.tar.zst" \
        -o /tmp/anki.tar.zst; \
    mkdir -p /tmp/anki-src /usr/local/share/anki; \
    tar -I zstd -xf /tmp/anki.tar.zst -C /tmp/anki-src --strip-components=1; \
    cp -av --no-preserve=owner,context -- /tmp/anki-src/* /usr/local/share/anki/; \
    ln -sf /usr/local/share/anki/anki /usr/local/bin/anki; \
    rm -rf /tmp/anki.tar.zst /tmp/anki-src

# Fetch AnkiConnect (addon id 2055492159) from UPSTREAM.
#
# History worth keeping, because the obvious reading of it is wrong: FooSoft's
# GitHub repo was archived in Nov 2025 and its addNote path used removed APIs
# (requireReset) that crashed on Anki 24.11, so this image used to ship the
# soft-fork Anki-Connect-Plus. But "archived" meant RELOCATED, not abandoned —
# upstream moved to SourceHut and is now the far more active project (near-daily
# commits, dated release tags), while the fork has been dormant since 2026-02
# and its AnkiWeb listing tops out at 26.02.1+. requireReset is gone from
# upstream entirely and addNote/addNotes run through an explicit legacy shim, so
# our version:6 HTTP calls work unchanged — verified against this exact pin by
# the probe described in bead flashgen-mfb (all 18 actions flashgen uses pass).
#
# Upstream's plugin/__init__.py sets required_anki_version = (26, 8, 0) and
# RAISES below it, so this pin and ANKI_VERSION above are coupled: upstream
# AnkiConnect cannot run on Anki 24.11, and the two must be bumped together.
#
# Fetched with `git`, not curl: SourceHut answers plain /archive/*.tar.gz
# requests with HTTP 418 (anti-scraper). The tag makes this reproducible, which
# the previous fork fetch was not — it pulled an unpinned `main` tarball.
ARG ANKICONNECT_REF=26.10.14.0
RUN set -eux; \
    git clone --branch "${ANKICONNECT_REF}" --depth 1 \
        https://git.sr.ht/~foosoft/anki-connect /tmp/anki-connect; \
    mkdir -p /opt/ankiconnect; \
    cp -r /tmp/anki-connect/plugin/. /opt/ankiconnect/; \
    rm -rf /tmp/anki-connect

# Override default config: bind to 0.0.0.0 so other containers on the Docker
# network can reach AnkiConnect. Port is NOT published to the host.
COPY ankiconnect-config.json /opt/ankiconnect/config.json

# FlashGen sync bootstrap addon: headless AnkiWeb login + background auto-sync
# (bootstrapped into the profile's addons21 dir by entrypoint.sh).
COPY flashgen-sync/ /opt/flashgen-sync/

# Seed prefs21.db: pre-set the UI language so a FRESH/wiped anki-data volume does
# NOT block on Anki's first-run language-selection modal (which hangs headlessly,
# before the profile opens and the sync addon can run). entrypoint.sh copies this
# onto an empty volume only. Generated at build time from a tiny, auditable script
# (Anki bundles its own Python; the system python3 is only needed for this step).
COPY seed_prefs.py /opt/seed_prefs.py
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && mkdir -p /opt/flashgen-seed \
    && python3 /opt/seed_prefs.py /opt/flashgen-seed/prefs21.db \
    && rm -rf /var/lib/apt/lists/*

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
