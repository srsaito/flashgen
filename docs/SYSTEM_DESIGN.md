# FlashGen System Design

## Architecture Decision

FlashGen is a single repository with one card-generation engine and two front doors:

- `flashgen.py` is the local CLI entrypoint. It reads formatted JSON from stdin, fills missing translations when needed, normalizes furigana markup, generates audio, stores media in Anki, and creates the Anki note.
- `src/flashgen_mcp/` is the MCP/server entrypoint. It should expose the same card-generation capability over HTTP/MCP without reimplementing card semantics.

The MCP layer should be a thin adapter over the FlashGen engine. Improvements to translation behavior, furigana handling, audio generation, duplicate checking, field mapping, and Anki note creation should benefit both the MacBook CLI workflow and the Lightsail-hosted server workflow.

## Repository Model

`flashgen` is the source of truth for both CLI and MCP/server development.

The former `flashgen-mcp` repo was intentionally folded into this repo while the MCP work was still early. Keeping one repo avoids cross-repo version skew, duplicated schemas, duplicated tests, and awkward deployment coordination. Future changes that affect both the CLI and server can land atomically in one commit.

The retained layout is:

```text
flashgen.py              # CLI entrypoint and current card-generation engine
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
2. `jpflash` pipes clipboard contents into `flashgen.py`.
3. `flashgen.py` repairs/parses JSON and calls `create_flashcard(...)`.
4. The engine fills missing translation fields, normalizes furigana, generates audio, stores media through AnkiConnect, creates the Anki note, and prints structured JSON.

MCP/server flow:

1. Client sends a structured card request to the MCP/server endpoint.
2. The MCP layer validates and normalizes request fields.
3. The MCP layer calls the same FlashGen engine used by the CLI.
4. The server returns the engine result or a structured error response.

The server must not maintain a second copy of card schema rules. The CLI and MCP request models should converge on the same field names and behavior.

## JSON Contract

The LLM-facing input contract is the JSON object copied from ChatGPT and consumed by the CLI today. The MCP card-creation endpoint should accept the same semantic fields, even if the transport wrapper differs.

Input JSON:

```json
{
  "japanese":        "string (optional; annotated as kanji[reading]; auto-translated from english if omitted)",
  "english":         "string (optional; auto-translated from japanese if omitted)",
  "notes":           "string (optional; use word[reading] format for furigana in definitions)",
  "tags":            ["list", "of", "tags"],
  "deck":            "string (optional; defaults to configured deck)",
  "japanese_prompt": "string (optional; situational prompt in Japanese, annotated as kanji[reading])",
  "english_prompt":  "string (optional; English version of the situational prompt)"
}
```

At least one of `japanese` or `english` is required. `japanese_prompt` and `english_prompt` describe the situational prompt for Response cards and should be provided together or omitted together.

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
  "audio_file":        "filename.mp3",
  "local_audio_path":  "anki_audio_out/filename.mp3",
  "japanese_prompt":   "... (only present for Response cards)",
  "english_prompt":    "... (only present for Response cards)",
  "audio_prompt_file": "filename.mp3 (only present for Response cards)"
}
```

The returned `japanese` and `japanese_prompt` fields contain FlashGen-normalized furigana. `audio_file` and `audio_prompt_file` are the filenames stored in Anki media. `local_audio_path` is the local generation path for the primary response audio.

## Anki Runtime

FlashGen depends on AnkiConnect being reachable. The default local URL is `http://127.0.0.1:8765`, but server deployments should treat this as configuration rather than source code.

The runtime strategy is:

1. Prefer a service manager to keep Anki or the AnkiConnect runtime available in deployment environments.
2. Add a runtime health/start helper only as a fallback for local development or recovery.
3. Fail with clear structured errors when AnkiConnect is unavailable, the deck is missing, or the note type/fields are not configured.

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

Production service files should run the same app from the same repo checkout, for example `flashgen_mcp.app:app`. Future systemd/nginx assets should live in this repository under `deploy/` or `docs/deployment/` so the Lightsail instance can be recreated from source-controlled instructions.

## Configuration

Configuration should move steadily out of constants and into environment-driven settings that both front doors can share.

Initial settings:

- `OPENAI_API_KEY`
- `ANKI_CONNECT_URL`
- `FLASHGEN_DECK_NAME`
- `FLASHGEN_MODEL_NAME`
- `FLASHGEN_TTS_MODEL`
- `FLASHGEN_TTS_VOICE`
- `FLASHGEN_TEXT_MODEL`

The existing constants in `flashgen.py` can remain as defaults while the MCP service is being integrated. The core refactor should make these settings explicit inputs.

## Error Handling Contract

Both CLI and MCP/server should return structured success and error payloads.

Success responses should match the FlashGen result JSON contract above: `status`, `note_id`, `deck`, `model`, normalized `japanese`, `english`, `notes`, `tags`, `audio_file`, `local_audio_path`, and Response-card-only prompt/audio fields when present.

Error responses should distinguish:

- invalid input
- missing OpenAI configuration
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
- Deployment must confirm whether AnkiConnect runs directly on Lightsail or is reached through a private tunnel or other network path.
- The `flashgen.py` constants should become shared settings before the MCP endpoint creates real cards.
