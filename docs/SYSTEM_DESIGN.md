# FlashGen System Design

## Architecture Decision

FlashGen is a single repository with one card-generation engine and two front doors:

- `flashgen.py` is the local CLI entrypoint. It reads formatted JSON from stdin, fills missing translations when needed, normalizes furigana markup, generates audio, stores media in Anki, and creates the Anki note.
- `src/flashgen_mcp/` is the MCP/server entrypoint. It should expose the same card-generation capability over HTTP/MCP without reimplementing card semantics.

The MCP layer should be a thin adapter over the FlashGen engine. Improvements to translation behavior, furigana handling, audio generation, duplicate checking, field mapping, and Anki note creation should benefit both the MacBook CLI workflow and the Lightsail-hosted server workflow.

The primary hosted LLM workflow is now Claude, using a Claude Project named `flashgen`. The project carries the FlashGen prompt context through its `instructions.md` and `system_prompt.md` files. The plan of record is an individual Claude Free/Pro/Max account using a custom remote MCP connector added from Claude web or Claude Desktop. Team/Enterprise administration is intentionally out of scope for the first end-to-end system. ChatGPT and Gemini remain useful compatibility targets, but they are no longer the lead design surface for the remote MCP flow.

## Repository Model

`flashgen` is the source of truth for both CLI and MCP/server development.

The former `flashgen-mcp` repo was intentionally folded into this repo while the MCP work was still early. Keeping one repo avoids cross-repo version skew, duplicated schemas, duplicated tests, and awkward deployment coordination. Future changes that affect both the CLI and server can land atomically in one commit.

The retained layout is:

```text
src/flashgen.py          # CLI entrypoint (flashgen console script) and current card-generation engine
src/flashgen_mcp/        # MCP/server package
docs/                    # shared architecture and implementation docs
tests/                   # CLI and MCP regression tests
```

## Engine Boundary

The current reusable boundary is `flashgen.create_flashcard(...)`. The CLI already delegates to this function after parsing stdin JSON, which makes it a useful bridge for the server.

Near term, the MCP service should call `create_flashcard(...)` directly after validating the request. If process isolation becomes necessary, the server can temporarily invoke `flashgen.py` as a subprocess with JSON over stdin, but direct function calls are preferred because they are easier to test and produce cleaner error handling.

Medium term, move the reusable engine out of the CLI file into focused package modules, for example:

```text
src/flashgen_core/schema.py       # request/result models and validation
src/flashgen_core/furigana.py     # furigana normalization and stripping
src/flashgen_core/anki.py         # AnkiConnect client and note creation
src/flashgen_core/audio.py        # TTS filename and file generation
src/flashgen_core/service.py      # create_flashcard orchestration
flashgen.py                      # thin CLI wrapper around flashgen_core
src/flashgen_mcp/app.py           # thin server wrapper around flashgen_core
```

That refactor should preserve the existing CLI behavior while making the shared engine explicit.

## Request Flow

CLI flow:

1. User copies JSON from ChatGPT.
2. The `flashgen` alias pipes clipboard contents into the `flashgen` console script.
3. `flashgen.py` repairs/parses JSON and calls `create_flashcard(...)`.
4. The engine fills missing translation fields, normalizes furigana for display, resolves TTS proxy text for synthesis, resolves the TTS provider/model, generates audio, stores media through AnkiConnect, creates the Anki note, and prints structured JSON.

MCP/server flow:

1. Client sends a structured card request to the MCP/server endpoint.
2. The MCP layer validates and normalizes request fields.
3. The MCP layer calls the same FlashGen engine used by the CLI.
4. The server returns the engine result or a structured error response.

The server must not maintain a second copy of card schema rules. The CLI and MCP request models should converge on the same field names and behavior.

Claude Project flow:

1. User practices Japanese with Claude in the `flashgen` project.
2. When a phrase should become a card, the user asks Claude to draft the FlashGen JSON using the project context.
3. Claude returns JSON in the established FlashGen contract.
4. User inspects the JSON and iterates with Claude until the fields, furigana, prompt text, tags, and TTS proxy text look correct.
5. User explicitly asks Claude to create the Anki card.
6. Claude calls the remote FlashGen MCP tool with the reviewed JSON.
7. FlashGen validates the payload, creates the Anki note and audio through the shared engine, and returns the structured result.

This workflow keeps the destructive/write action behind an explicit user request. JSON generation and review happen in normal Claude conversation; Anki mutation happens only through the MCP tool call after confirmation.

## JSON Contract

The LLM-facing input contract is the JSON object drafted by Claude in the `flashgen` project or copied from another LLM client and consumed by the CLI today. The MCP card-creation endpoint should accept the same semantic fields, even if the transport wrapper differs.

Input JSON:

```json
{
  "japanese":        "string (optional; annotated as kanji[reading]; auto-translated from english if omitted)",
  "english":         "string (optional; auto-translated from japanese if omitted)",
  "notes":           "string (optional; use word[reading] format for furigana in definitions)",
  "tags":            ["list", "of", "tags"],
  "deck":            "string (optional; defaults to configured deck)",
  "japanese_tts":    "string (optional; plain Japanese text used for TTS; falls back to stripped japanese when omitted)",
  "japanese_prompt": "string (optional; situational prompt in Japanese, annotated as kanji[reading])",
  "english_prompt":  "string (optional; English version of the situational prompt)",
  "japanese_prompt_tts": "string (optional; plain Japanese text used for prompt TTS; falls back to stripped japanese_prompt when omitted)",
  "tts_provider":    "string (optional; openai or gemini)",
  "tts_model":       "string (optional; provider-specific TTS model id)"
}
```

At least one of `japanese` or `english` is required. `japanese_prompt` and `english_prompt` describe the situational prompt for Response cards and should be provided together or omitted together. `tts_provider` and `tts_model` should also be provided together or omitted together. The `_tts` fields are synthesis-only plain Japanese strings supplied by the LLM; if they are omitted, the engine falls back to the corresponding display field with furigana markup stripped. If both TTS provider fields are omitted, the engine defaults to Gemini TTS.

FlashGen result JSON:

```json
{
  "status":            "ok",
  "note_id":           12345,
  "deck":              "日本語-Soso",
  "model":             "Japanese Listening+Production",
  "japanese":          "...",
  "english":           "...",
  "notes":             "...",
  "tags":              ["..."],
  "tts_provider":      "gemini",
  "tts_model":         "gemini-3.1-flash-tts-preview",
  "japanese_tts":      "...",
  "audio_file":        "filename.wav",
  "local_audio_path":  "anki_audio_out/filename.wav",
  "japanese_prompt":   "... (only present for Response cards)",
  "english_prompt":    "... (only present for Response cards)",
  "japanese_prompt_tts": "... (only present for Response cards)",
  "audio_prompt_file": "filename.wav (only present for Response cards)"
}
```

The returned `japanese` and `japanese_prompt` fields contain FlashGen-normalized furigana for display. The returned `_tts` fields are the resolved plain Japanese strings actually synthesized into audio. `audio_file` and `audio_prompt_file` are the filenames stored in Anki media. `local_audio_path` is the local generation path for the primary response audio. Gemini output is stored as `.wav`; OpenAI output is stored as `.mp3`. No extra Anki note fields are required because `_tts` values are not stored separately on the note.

## Remote MCP Access and Auth

Remote MCP access must be designed for Claude first. Anthropic's remote custom connectors are configured through the user's Claude account and brokered from Anthropic's cloud infrastructure across Claude surfaces, including claude.ai, Claude Desktop, Cowork, and mobile apps. That means the production FlashGen MCP server must be reachable over public HTTPS from Anthropic infrastructure. A private Tailscale-only endpoint is useful for development testing, but it is not sufficient for Claude remote connectors.

The target production URL should be stable and user-owned, for example:

```text
https://mcp.ssaito.net/mcp
```

Claude connector registration should use this URL as a custom remote MCP connector on the owner's individual Claude account. The individual-account setup flow is:

1. Open Claude web or Claude Desktop.
2. Navigate to Customize > Connectors.
3. Click `+` and choose `Add custom connector`.
4. Enter the FlashGen remote MCP URL, preferably the Streamable HTTP endpoint at `https://mcp.ssaito.net/mcp`.
5. Optionally configure OAuth client details under Advanced settings if the FlashGen edge/auth layer requires them.
6. Click `Add`.
7. Click `Connect` if authentication is required.
8. In the `flashgen` project chat, use the `+` / Connectors or Search and tools menu to enable the FlashGen connector for that conversation.

Once the connector is added and authenticated from Claude web or Claude Desktop, it can be used from Claude mobile. Claude mobile can use remote MCP connectors already registered through the Claude account, but it cannot add a new user-specified MCP server directly from the mobile app. This makes web/desktop registration a required setup step for the iPhone workflow.

Authentication should use OAuth-compatible remote MCP auth rather than a shared static bearer token. Cloudflare is the preferred edge/auth layer because it can provide public HTTPS, DNS, certificate management, access policy, logging, and an OAuth-facing path for remote MCP clients. The first implementation should prefer Cloudflare Access or Cloudflare's MCP OAuth tooling in front of the Python service instead of hand-rolling OAuth in FastAPI.

The initial auth policy should be intentionally narrow:

- allow only the owner's account during development
- require OAuth login before tool access
- expose only the FlashGen tools needed for card validation and creation
- log tool calls without storing secret API keys or unnecessary card content
- rate-limit card creation to protect paid TTS and translation providers

MarkItDown is a useful reference MCP for understanding the connector flow, but it is not a production-equivalent hosted test by itself. Microsoft's `markitdown-mcp` supports STDIO, Streamable HTTP, and SSE, with local endpoints such as `http://127.0.0.1:3001/mcp` and `http://127.0.0.1:3001/sse`. Its official guidance says it is intended for local trusted agents, binds to localhost by default, and does not provide authentication. FlashGen should not copy the unsafe part of that shape by exposing an unauthenticated converter-style MCP publicly. If MarkItDown is used as a smoke test, it should either remain local in Claude Desktop or be placed behind a narrow authenticated wrapper before being exposed to Claude web.

ChatGPT and Gemini compatibility should be revisited after the Claude individual-account path works. Public LLM clients generally need an Internet-facing MCP server, and authenticated clients need OAuth or bearer-token compatible auth, but each provider's remote MCP surface differs. FlashGen should avoid provider-specific assumptions in the core engine and keep transport/auth concerns in the MCP/deployment layer.

## Anki Runtime

FlashGen depends on AnkiConnect being reachable. The default local URL is `http://127.0.0.1:8765`, but server deployments should treat this as configuration rather than source code.

On Lightsail, Anki and AnkiConnect will run on the server. Anki does not provide a supported headless mode, so the deployment will run Anki under `Xvfb` to provide a virtual X display. This keeps Anki's Qt runtime satisfied while allowing the service to run unattended.

The server-side Anki runtime should be isolated from the FlashGen MCP service:

- `anki-headless` container: runs Anki, AnkiConnect, `Xvfb`, and the temporary UI access tooling needed for setup or recovery.
- `flashgen-mcp` container: runs the FlashGen MCP server and talks to AnkiConnect over HTTP.
- Persistent Docker volumes: store the Anki profile, collection, add-ons, media, and login/session state so reboots do not require re-authentication.

Initial setup needs a temporary way to access the Anki UI so the owner can log in, install or verify AnkiConnect, sync, and confirm the deck/model configuration. That UI exposure should be temporary and private, preferably through SSH or Tailscale-only VNC/noVNC access. Anki should retain credentials in its profile after login, so normal reboot recovery should only need to restart the container and virtual display.

AnkiConnect must not be exposed on the public Internet. Tailscale is already installed on the server, so port `8765` should be reachable only over the Tailscale/private network path needed by FlashGen and administrative testing. The production MCP endpoint is public through Cloudflare, but the AnkiConnect control plane remains private.

The runtime strategy is:

1. Use Docker and systemd to keep both the `anki-headless` and `flashgen-mcp` containers running after reboot.
2. Keep the Anki profile and media on persistent volumes, not inside ephemeral containers.
3. Bind or firewall AnkiConnect so it is reachable only from the intended private network path, not from the Internet.
4. Add health checks for AnkiConnect, deck existence, model existence, and media writes.
5. Fail with clear structured errors when AnkiConnect is unavailable, the deck is missing, or the note type/fields are not configured.

## Lightsail Deployment Model

Lightsail is a deployment target, not a separate source tree.

Practically, that means:

- Lightsail clones this same `flashgen` repository.
- Source edits happen on the development machine and are pushed to GitHub.
- Deployment pulls a known branch or commit on Lightsail, syncs dependencies, and restarts the server process.
- Lightsail-specific configuration lives in environment variables, service files, or deployment docs, not as hand edits to tracked source files on the instance.

A typical deployment shape should be:

```bash
git clone https://github.com/srsaito/flashgen.git /opt/flashgen
cd /opt/flashgen
uv sync --frozen
uv run uvicorn flashgen_mcp.app:app --host 0.0.0.0 --port 8000
```

That direct `uvicorn` command is useful for early smoke testing. The production shape should move to source-controlled Docker and systemd assets:

```text
/opt/flashgen
  docker-compose.yml or compose.yaml
  deploy/
    anki-headless/
    flashgen-mcp/
    nginx/
    systemd/
```

Production service files should run containers built from the same repo checkout. Future systemd/nginx/Docker assets should live in this repository under `deploy/` or `docs/deployment/` so the Lightsail instance can be recreated from source-controlled instructions.

Running Codex on the Lightsail instance is appropriate for server-specific development because it can inspect the real OS, Docker, systemd, nginx, Tailscale, Anki, and Cloudflare tunnel/access behavior. Source edits made there should still be committed and pushed through GitHub so the instance is not the only record of the deployment.

## Configuration

Configuration should move steadily out of constants and into environment-driven settings that both front doors can share.

Initial settings:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `ANKI_CONNECT_URL`
- `ANKI_CONNECT_HOST`
- `ANKI_CONNECT_PORT`
- `FLASHGEN_DECK_NAME`
- `FLASHGEN_MODEL_NAME`
- `FLASHGEN_TEXT_MODEL`
- `DISPLAY`

The existing constants in `flashgen.py` can remain as defaults while the MCP service is being integrated. Translation continues to use OpenAI when a field must be filled in, while TTS can route to either Gemini or OpenAI per request.

## Error Handling Contract

Both CLI and MCP/server should return structured success and error payloads.

Success responses should match the FlashGen result JSON contract above: `status`, `note_id`, `deck`, `model`, normalized `japanese`, `english`, `notes`, `tags`, resolved `tts_provider`, resolved `tts_model`, `audio_file`, `local_audio_path`, and Response-card-only prompt/audio fields when present.

Error responses should distinguish:

- invalid input
- missing OpenAI configuration
- missing Gemini configuration
- translation failure
- TTS failure
- AnkiConnect unavailable
- deck or model missing
- duplicate or rejected Anki note

The CLI can continue printing JSON to stdout and exiting non-zero on failure. The MCP server should map the same failure categories to HTTP/MCP errors while preserving useful user-facing messages.

## Testing Strategy

Shared engine behavior should be tested once and reused by both front doors.

Near-term tests:

- furigana normalization and TTS input stripping
- `create_flashcard(...)` orchestration with OpenAI and AnkiConnect mocked
- MCP health endpoint
- MCP request validation once card-creation endpoints are added

As the `flashgen_core` package emerges, tests should move with the extracted modules instead of staying coupled to CLI parsing.

## Open Design Notes

- The MCP card-creation endpoint still needs a concrete request/response schema.
- Deployment must confirm the exact Docker networking shape for AnkiConnect: Tailscale IP, private Docker network, host networking, or a combination that keeps port `8765` off the public Internet.
- Remote MCP auth must choose between Cloudflare Access and Cloudflare's MCP OAuth tooling before public connector registration.
- The `flashgen.py` constants should become shared settings before the MCP endpoint creates real cards.
