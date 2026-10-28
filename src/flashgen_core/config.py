"""Constants, note-type definitions, secret reading, debug output.

Every value a deployment might need to change reads from the environment with
the old hardcoded value as its default (flashgen-yya), so the CLI and the MCP
server are configured the same way — same variable names, same defaults, one
module. `.env.example` documents them.

Values are read at IMPORT, so setting an env var later changes nothing in a
running process, and other modules that imported a name hold their own copy.
A test needing different values loads a separate copy of this module rather
than reloading it — see load_config() in tests/test_logging_and_config.py for
why reloading breaks the facade's identity invariant.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "")
    return value.strip() or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"{name}={raw!r} is not an integer — using {default}", file=sys.stderr)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


# -----------------------------
# Configuration
# -----------------------------
DEFAULT_TTS_PROVIDER = _env_str("FLASHGEN_TTS_PROVIDER", "gemini")
OPENAI_TTS_MODEL = _env_str("FLASHGEN_OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = _env_str("FLASHGEN_OPENAI_TTS_VOICE", "onyx")
GEMINI_TTS_MODEL = _env_str("FLASHGEN_GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
GEMINI_TTS_VOICE = _env_str("FLASHGEN_GEMINI_TTS_VOICE", "Algenib")
OPENAI_TEXT_MODEL = _env_str("FLASHGEN_OPENAI_TEXT_MODEL", "gpt-4.1-mini")

ANKI_CONNECT_URL = _env_str("ANKI_CONNECT_URL", "http://127.0.0.1:8765")
# Seconds to wait on one AnkiConnect call. A full-sync or a large findNotes can
# be slow, so this is a ceiling on hanging, not a latency budget.
ANKI_CONNECT_TIMEOUT = _env_int("ANKI_CONNECT_TIMEOUT", 30)

# Logging: one structured line per MCP tool call (flashgen_mcp.app). Only the
# standard level names are accepted — a typo must not stop the server booting,
# so anything else falls back to INFO with a complaint on stderr.
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")
LOG_LEVEL = _env_str("FLASHGEN_LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in _LOG_LEVELS:
    print(
        f"FLASHGEN_LOG_LEVEL={LOG_LEVEL!r} is not one of {_LOG_LEVELS} — using INFO",
        file=sys.stderr,
    )
    LOG_LEVEL = "INFO"

# Public base URL the MCP OAuth metadata advertises. Must match how clients
# reach the server (the Cloudflare tunnel hostname in production).
MCP_ISSUER = _env_str("FLASHGEN_MCP_ISSUER", "https://mcp.ssaito.net")

# search_notes result shaping, both overridable because they trade the caller's
# context budget against recall.
SEARCH_FIELD_VALUE_MAX = _env_int("FLASHGEN_SEARCH_FIELD_VALUE_MAX", 400)
SEARCH_CANDIDATE_CAP = _env_int("FLASHGEN_SEARCH_CANDIDATE_CAP", 500)


def read_secret(name: str) -> str:
    """Read a secret, preferring a Docker/Compose secret file over a raw env var.

    If <NAME>_FILE is set (e.g. OPENAI_API_KEY_FILE=/run/secrets/openai_api_key),
    return that file's contents (trailing whitespace stripped — secret files
    conventionally end in a newline). Otherwise fall back to the <NAME> env var.
    Reading from a file keeps the value out of the container environment and
    `docker inspect`.
    """
    path = os.environ.get(f"{name}_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            print(
                f"could not read {name}_FILE ({path}): {exc!r} — falling back to {name} env",
                file=sys.stderr,
            )
    return os.environ.get(name, "")

# Change these to match your Anki setup, or set the env vars.
DECK_NAME = _env_str("FLASHGEN_DECK", "日本語-Soso")
MODEL_NAME = _env_str("FLASHGEN_MODEL", "Japanese Listening+Production")

# Single-card 場面-response note type, created programmatically via AnkiConnect
# createModel (docs/SPEC-dialog-response.md). Same fields as MODEL_NAME so
# add_note()'s field mapping is shared; exactly one card template.
DIALOG_MODEL_NAME = _env_str("FLASHGEN_DIALOG_MODEL", "Japanese Dialog Response")

# Single-card cued-response note type (docs/SPEC-cued-response.md): the
# Response card of MODEL_NAME on its own, so a prompt→response note no longer
# generates the Listening and Production cards alongside it. Deliberately NOT
# called prompt_response — that name already denotes the 3-card usage of
# MODEL_NAME with the prompt fields filled.
CUED_MODEL_NAME = _env_str("FLASHGEN_CUED_MODEL", "Japanese Cued Response")

CARD_TYPES = ("standard", "dialog_response", "cued_response")

# Shared by every FlashGen-owned note type. Same field NAMES as the legacy
# MODEL_NAME, so add_note()'s mapping and the whole collection read/write
# surface work on all three without special cases — those address fields by
# name. The order here differs from the legacy model, which was created by hand
# with Audio and Notes transposed; order only sets the editor's field layout,
# so the two generated models stay consistent with each other instead.
GENERATED_MODEL_FIELDS = [
    "Japanese",
    "English",
    "Notes",
    "Audio",
    "Japanese Prompt",
    "English Prompt",
    "Audio Prompt",
]

# Front is audio-only by design: the learner must parse the prompt by ear and
# produce the appropriate response. No prompt or answer text may appear here.
DIALOG_CARD_FRONT = """\
<div>どう答えますか？</div>
<br>
<div>{{Audio Prompt}}</div>
"""

# Back reveals the prompt text first (self-check: did you actually hear it?),
# then the response with its audio, gloss, and notes.
DIALOG_CARD_BACK = """\
{{FrontSide}}

<hr id=answer>

<b>きっかけ</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese Prompt}}</div>
{{#English Prompt}}<div>{{English Prompt}}</div>{{/English Prompt}}
<br>
<b>回答</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<div>{{Audio}}</div>
<br>
<div>{{English}}</div>
{{#Notes}}<div class="notes">{{furigana:Notes}}</div>{{/Notes}}
"""

# Front carries the two English cues and the prompt audio, and NO Japanese
# text: the learner reads what situation to respond to, hears the Japanese
# prompt, and produces the answer. Both English fields are free-form cues —
# English Prompt may be an instruction rather than a translation, and English
# may be a list of numbered cases — so neither is a learning target here and
# neither ever reaches TTS.
CUED_CARD_FRONT = """\
<b>きっかけ</b>：{{English Prompt}}<br>
<b>回答</b>：{{English}}<br>
<br>
<div>{{Audio Prompt}}</div>
"""

# Back reveals the prompt text first (did you hear it right?), then the answer
# with its audio and notes. The English cues are already on {{FrontSide}}.
CUED_CARD_BACK = """\
{{FrontSide}}

<hr id=answer>

<b>きっかけ</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese Prompt}}</div>
<br>
<b>回答</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<div>{{Audio}}</div>
{{#Notes}}<div class="notes">{{furigana:Notes}}</div>{{/Notes}}
"""

GENERATED_MODEL_CSS = """\
.card {
  font-family: arial;
  font-size: 20px;
  text-align: left;
  color: black;
  background-color: white;
}
.notes {
  margin-top: 12px;
  font-size: 18px;
  color: #444;
}
.nightMode .notes {
  color: #bbb;
}
"""

# The card_types whose note type FlashGen owns and creates on demand, each
# mapped to its model name and its single card template. "standard" is absent:
# it targets the legacy MODEL_NAME, which stays manually managed in Anki.
# Every type here requires japanese_prompt — without the prompt audio there is
# no front — and carries its own reason for the error message.
GENERATED_MODELS = {
    "dialog_response": {
        "model_name": DIALOG_MODEL_NAME,
        "front": DIALOG_CARD_FRONT,
        "back": DIALOG_CARD_BACK,
        "prompt_required_because": (
            "the prompt audio is the entire front of the card"
        ),
    },
    "cued_response": {
        "model_name": CUED_MODEL_NAME,
        "front": CUED_CARD_FRONT,
        "back": CUED_CARD_BACK,
        "prompt_required_because": (
            "the English cues plus the prompt audio are the entire front of "
            "the card"
        ),
    },
}

OUTPUT_DIR = Path(_env_str("FLASHGEN_OUTPUT_DIR", "anki_audio_out"))
DEFAULT_TAGS = _env_list("FLASHGEN_DEFAULT_TAGS", ["jp", "auto", "conversation"])

# Debug flag — verbose payload dumps on stdout.
DEBUG = _env_bool("FLASHGEN_DEBUG", False)


@dataclass(frozen=True)
class TTSConfig:
    provider: str
    model: str
    voice: str
    extension: str


def effective_config() -> dict[str, Any]:
    """The resolved configuration, for logging at startup. No secrets here —
    every value is a name, a URL, a path or a number, never a credential."""
    return {
        "anki_connect_url": ANKI_CONNECT_URL,
        "anki_connect_timeout": ANKI_CONNECT_TIMEOUT,
        "deck": DECK_NAME,
        "model": MODEL_NAME,
        "dialog_model": DIALOG_MODEL_NAME,
        "cued_model": CUED_MODEL_NAME,
        "output_dir": str(OUTPUT_DIR),
        "default_tags": DEFAULT_TAGS,
        "tts_provider": DEFAULT_TTS_PROVIDER,
        "openai_tts_model": OPENAI_TTS_MODEL,
        "gemini_tts_model": GEMINI_TTS_MODEL,
        "openai_text_model": OPENAI_TEXT_MODEL,
        "log_level": LOG_LEVEL,
        "mcp_issuer": MCP_ISSUER,
        "debug": DEBUG,
    }


def debug_print(label: str, value: Any) -> None:
    if not DEBUG:
        return

    print(f"DEBUG {label}:")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)
