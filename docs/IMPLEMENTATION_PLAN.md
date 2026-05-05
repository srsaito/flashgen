# FlashGen MCP Implementation Plan

## Direction

FlashGen now uses a single-repository model. The MCP/server work lives in the `flashgen` repo beside the existing CLI instead of in a separate `flashgen-mcp` repo.

The guiding architecture is:

- one repository
- one card-generation engine
- two front doors: CLI and MCP/server
- one primary hosted LLM workflow: Claude Project plus remote MCP connector

## Current State

- `flashgen.py` remains the CLI entrypoint and current engine host.
- `flashgen.create_flashcard(...)` is the current shared function the MCP layer should call.
- `src/flashgen_mcp/` contains the initial FastAPI app scaffold.
- `docs/SYSTEM_DESIGN.md` documents the target architecture and deployment model.
- The Claude Project named `flashgen` exists and contains the project prompt files `instructions.md` and `system_prompt.md`.
- The intended user workflow is conversation-first: Claude drafts FlashGen JSON, the user reviews and iterates, then Claude calls MCP only when the user explicitly asks to create the Anki card.

## Milestones

1. Fold the early MCP scaffold and docs into the `flashgen` repo.
2. Add `pyproject.toml` for `uv` while keeping `requirements.txt` usable for pip installs.
3. Keep the MCP package under `src/flashgen_mcp/`.
4. Add a health test for the MCP server scaffold.
5. Define a shared card request/result schema.
6. Add a validation-only MCP card endpoint.
7. Wire the MCP endpoint to the existing `flashgen.create_flashcard(...)` function.
8. Define the Claude-facing MCP tool names, descriptions, schemas, and write-action semantics.
9. Add remote MCP transport support suitable for Claude custom connectors, preferably Streamable HTTP at `/mcp`.
10. Put the service behind public HTTPS at `mcp.ssaito.net`.
11. Add OAuth-compatible access through Cloudflare.
12. Register and test the custom connector in Claude, then enable it in the `flashgen` project.
13. Move reusable engine code from `flashgen.py` into focused package modules.
14. Add deployment docs/assets for Lightsail from the same repo checkout.
15. Harden configuration, errors, logging, and runtime health checks.

## Claude Remote MCP Work

Claude is the lead integration target. Anthropic remote custom connectors are reached from Anthropic's cloud infrastructure, not from the user's local device, so the production MCP endpoint must be publicly reachable over HTTPS. Tailscale remains useful for local development and private smoke tests, but it cannot be the only connectivity path for Claude.

Claude-facing tools should start small:

- `validate_flashcard`: validate and normalize reviewed JSON without creating an Anki note.
- `create_flashcard`: create the Anki note and audio from reviewed JSON.

`create_flashcard` is a write action. Its tool description should make clear that Claude should call it only after the user has inspected the JSON and explicitly requested card creation. The server should still validate every request because the Claude project prompt is guidance, not a security boundary.

Connector setup tasks:

1. Publish the MCP server at a stable URL such as `https://mcp.ssaito.net/mcp`.
2. Configure OAuth-compatible authentication at the edge, preferably with Cloudflare.
3. Add the URL as a custom connector in Claude's connector settings.
4. Authenticate the connector as the allowed user.
5. Enable the connector in the `flashgen` Claude Project.
6. Test the full flow from desktop and mobile Claude clients.

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

## Cloudflare and Domain Work

Cloudflare should be the preferred public edge for remote MCP access. It should provide DNS, TLS, access policy, OAuth-compatible login, request logging, and rate limiting in front of the FlashGen service.

Domain work:

- Manage or delegate `ssaito.net` DNS through Cloudflare.
- Add `mcp.ssaito.net` as the public hostname for FlashGen MCP.
- Route `https://mcp.ssaito.net/mcp` to the deployed MCP service.
- Keep a separate `/healthz` endpoint for deployment monitoring.

Auth work:

- Start with a single allowed user account.
- Require OAuth login before MCP tool calls.
- Avoid exposing provider API keys or AnkiConnect credentials to Claude.
- Revisit ChatGPT and Gemini after Claude works end to end because each provider's MCP connector behavior differs.

## Task Tracking

Beads is initialized in this repo. MCP follow-up work should be tracked here, not in the old `flashgen-mcp` repo.
