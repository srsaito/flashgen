# FlashGen MCP Implementation Plan

## Direction

FlashGen now uses a single-repository model. The MCP/server work lives in the `flashgen` repo beside the existing CLI instead of in a separate `flashgen-mcp` repo.

The guiding architecture is:

- one repository
- one card-generation engine
- two front doors: CLI and MCP/server

## Current State

- `flashgen.py` remains the CLI entrypoint and current engine host.
- `flashgen.create_flashcard(...)` is the current shared function the MCP layer should call.
- `src/flashgen_mcp/` contains the initial FastAPI app scaffold.
- `docs/SYSTEM_DESIGN.md` documents the target architecture and deployment model.

## Milestones

1. Fold the early MCP scaffold and docs into the `flashgen` repo.
2. Add `pyproject.toml` for `uv` while keeping `requirements.txt` usable for pip installs.
3. Keep the MCP package under `src/flashgen_mcp/`.
4. Add a health test for the MCP server scaffold.
5. Define a shared card request/result schema.
6. Add a validation-only MCP card endpoint.
7. Wire the MCP endpoint to the existing `flashgen.create_flashcard(...)` function.
8. Move reusable engine code from `flashgen.py` into focused package modules.
9. Add deployment docs/assets for Lightsail from the same repo checkout.
10. Harden configuration, errors, logging, and runtime health checks.

## Near-Term Refactor Path

The CLI was already shaped so parsing and orchestration are separable. Preserve that path:

1. Keep `flashgen.py` working as-is.
2. Have MCP call `create_flashcard(...)` directly.
3. Extract pure helpers first, such as furigana normalization and filename generation.
4. Extract AnkiConnect and OpenAI/TTS integration behind small interfaces.
5. Make `flashgen.py` a thin stdin/stdout wrapper over the shared package.
6. Make `flashgen_mcp.app` a thin HTTP/MCP wrapper over the same package.

## Lightsail Work

Lightsail should clone and deploy this `flashgen` repo. It should not be edited as a separate source tree.

Deployment work should add source-controlled instructions or files for:

- cloning `/opt/flashgen`
- syncing dependencies with `uv`
- configuring environment variables
- running `flashgen_mcp.app:app`
- restarting through systemd
- proxying through nginx if needed
- making AnkiConnect reachable from the server process

## Task Tracking

Beads is initialized in this repo. MCP follow-up work should be tracked here, not in the old `flashgen-mcp` repo.
