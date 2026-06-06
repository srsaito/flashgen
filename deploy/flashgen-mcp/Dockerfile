# flashgen-mcp: FastAPI/uvicorn MCP + HTTP server.
# Reads ANKI_CONNECT_URL and API keys from the environment, and reaches
# AnkiConnect over the private compose network (http://anki:8765).
# Kept separate from anki-headless so Anki's Qt/X11/Xvfb requirements never
# leak into the MCP runtime.
#
# Built with uv (same toolchain as the host) for reproducible, locked installs.
# Build context is the repo root (see docker-compose.yml: context: ..).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# curl is only needed for the container healthcheck below
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: install locked dependencies only (cached unless pyproject/uv.lock change).
# README.md is referenced by pyproject as the project readme.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2: add the source (flashgen.py + flashgen_mcp both live under src/) and
# install the project itself. The editable install puts src/ on sys.path, so
# `import flashgen` and `import flashgen_mcp` both resolve regardless of cwd.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Liveness probe hits /mcp (GET) which returns 200 without touching AnkiConnect,
# so the MCP container stays healthy independent of the anki container's state.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -sf http://localhost:8000/mcp >/dev/null || exit 1

# Bind to 0.0.0.0 inside the container; the port is published only to host
# loopback (see docker-compose.yml), where host cloudflared proxies it.
CMD ["uvicorn", "flashgen_mcp.app:app", "--host", "0.0.0.0", "--port", "8000"]
