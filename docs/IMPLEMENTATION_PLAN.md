# FlashGen MCP Implementation Plan

## Direction

FlashGen now uses a single-repository model. The MCP/server work lives in the `flashgen` repo beside the existing CLI instead of in a separate `flashgen-mcp` repo.

The guiding architecture is:

- one repository
- one card-generation engine
- two front doors: CLI and MCP/server
- one primary hosted LLM workflow: Claude Project plus a custom remote MCP connector registered on an individual Claude Free/Pro/Max account

## Current State

- `flashgen.py` remains the CLI entrypoint and current engine host.
- `flashgen.create_flashcard(...)` is the current shared function the MCP layer should call.
- `src/flashgen_mcp/` contains the initial FastAPI app scaffold.
- `docs/SYSTEM_DESIGN.md` documents the target architecture and deployment model.
- The Claude Project named `flashgen` exists and contains the project prompt files `instructions.md` and `system_prompt.md`.
- The intended user workflow is conversation-first: Claude drafts FlashGen JSON, the user reviews and iterates, then Claude calls MCP only when the user explicitly asks to create the Anki card.
- The plan of record is individual Claude account setup only. Team/Enterprise organization connector administration is out of scope for the first end-to-end system.

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
12. Register and test the custom connector from Claude web or Claude Desktop on the owner's individual Claude account, then enable it in the `flashgen` project.
13. Build the Lightsail Anki runtime with Anki, AnkiConnect, `Xvfb`, persistent profile storage, and temporary private UI access.
14. Containerize the Anki runtime separately from the FlashGen MCP service.
15. Add deployment docs/assets for Lightsail from the same repo checkout.
16. Move reusable engine code from `flashgen.py` into focused package modules.
17. Harden configuration, errors, logging, and runtime health checks.

## Claude Remote MCP Work

Claude is the lead integration target. Anthropic remote custom connectors are reached from Anthropic's cloud infrastructure, not from the user's local device, so the production MCP endpoint must be publicly reachable over HTTPS. Tailscale remains useful for local development and private smoke tests, but it cannot be the only connectivity path for Claude.

The first supported account model is individual Claude Free/Pro/Max. The setup flow to validate and document is:

1. Open Claude web or Claude Desktop.
2. Navigate to Customize > Connectors.
3. Click `+` and choose `Add custom connector`.
4. Enter the FlashGen remote MCP URL, preferably `https://mcp.ssaito.net/mcp`.
5. Use Advanced settings only if OAuth client ID/secret configuration is required by the deployed auth layer.
6. Click `Add`.
7. Click `Connect` and complete authentication if prompted.
8. In the `flashgen` Claude Project chat, enable the FlashGen connector from the `+` / Connectors or Search and tools menu.
9. Open Claude mobile and confirm the already-registered connector is available there.

Claude mobile is a supported use surface after registration, but it is not a configuration surface. The iPhone app can use remote MCP connectors that were already added through the Claude account, but it cannot add a new custom MCP server directly.

Claude-facing tools should start small:

- `validate_flashcard`: validate and normalize reviewed JSON without creating an Anki note.
- `create_flashcard`: create the Anki note and audio from reviewed JSON.

`create_flashcard` is a write action. Its tool description should make clear that Claude should call it only after the user has inspected the JSON and explicitly requested card creation. The server should still validate every request because the Claude project prompt is guidance, not a security boundary.

Connector setup tasks:

1. Publish the MCP server at a stable URL such as `https://mcp.ssaito.net/mcp`.
2. Configure OAuth-compatible authentication at the edge, preferably with Cloudflare.
3. Add the URL as a custom connector in Claude web or Claude Desktop under Customize > Connectors.
4. Authenticate the connector as the allowed individual user.
5. Enable the connector in the `flashgen` Claude Project.
6. Test `validate_flashcard` from Claude web or desktop.
7. Test `create_flashcard` from Claude web or desktop after explicit user confirmation.
8. Test the already-registered connector from Claude mobile.

MarkItDown can be used as a learning or smoke-test reference, but not as a direct public-hosting pattern. Microsoft's `markitdown-mcp` exposes a single `convert_to_markdown(uri)` tool and supports Streamable HTTP/SSE endpoints when run with `markitdown-mcp --http --host 127.0.0.1 --port 3001`. Its own README states that it is intended for local trusted agents, binds to localhost by default, and does not support authentication. Do not expose MarkItDown directly to the public Internet. If it is used before FlashGen MCP is ready, keep it local in Claude Desktop or place it behind an authenticated wrapper with restricted URI schemes and network access.

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
- building the `anki-headless` container
- building the `flashgen-mcp` container
- configuring environment variables
- running `flashgen_mcp.app:app`
- starting Anki under `Xvfb`
- installing and verifying AnkiConnect
- temporarily exposing the Anki UI for owner login/setup over a private path only
- restarting through systemd
- proxying through nginx if needed
- making AnkiConnect reachable from the FlashGen process without exposing port `8765` publicly

## Anki Runtime Work

Anki and AnkiConnect will run on the Lightsail instance. Because Anki requires a display, the deployment should run it under `Xvfb` inside an `anki-headless` container. The container should persist the Anki profile, collection, add-ons, media, and login state in Docker volumes so a reboot can restart Anki without manual login.

Initial setup tasks:

1. Build or choose a base image with Anki, AnkiConnect, `Xvfb`, and enough Qt/runtime libraries.
2. Start Anki with a virtual display such as `DISPLAY=:99`.
3. Provide temporary private UI access for setup, preferably through SSH or Tailscale-only VNC/noVNC.
4. Log in to AnkiWeb, sync, and verify the target deck/model.
5. Confirm Anki retains credentials in the persisted profile after container restart and host reboot.
6. Disable or remove temporary UI exposure after setup unless needed for maintenance.

Networking tasks:

- Keep AnkiConnect on port `8765` private.
- Use the existing Tailscale installation for private administrative access and, if chosen, FlashGen-to-AnkiConnect traffic.
- Decide whether FlashGen reaches AnkiConnect through a Tailscale IP, private Docker network, host networking, or a locked-down localhost bridge.
- Configure `ANKI_CONNECT_URL` from that decision.
- Add firewall rules so Cloudflare/nginx expose only the public MCP endpoint and health endpoint, not AnkiConnect.

Operational tasks:

- Add systemd units or a compose-managed service so both containers restart after reboot.
- Add health checks for Anki process, AnkiConnect response, deck existence, model existence, and test media write.
- Document recovery steps for failed sync, expired Anki login, broken display, or missing AnkiConnect add-on.
- Keep the container split because future MCP services will share this server, and Anki's Qt/display requirements should not leak into the FlashGen MCP runtime.

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
- Verify connector registration from Claude web or Claude Desktop before testing on Claude mobile.
- Revisit ChatGPT and Gemini after Claude individual-account usage works end to end because each provider's MCP connector behavior differs.

## Development Bootstrap

The next development session can reasonably happen on the Lightsail instance because the work depends on real server services: Docker, systemd, nginx, Tailscale, Anki, AnkiConnect, and Cloudflare edge behavior.

Bootstrap flow:

1. SSH into the Lightsail instance.
2. Clone the repo into `/opt/flashgen`.
3. Run Codex from `/opt/flashgen`.
4. Implement runtime/deployment assets in the repo, not as untracked one-off server edits.
5. Commit and push changes back to GitHub.
6. Pull/redeploy from the committed source-controlled assets.

## Task Tracking

Beads is initialized in this repo. MCP follow-up work should be tracked here, not in the old `flashgen-mcp` repo.
