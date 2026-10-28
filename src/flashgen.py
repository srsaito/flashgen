"""FlashGen command-line entry point: `pbpaste | flashgen`.

Reads a card as JSON on stdin, repairs/parses it, hands it to the shared engine
in `flashgen_core`, and prints the result as JSON. The engine itself lives in
flashgen_core (flashgen-8ik); this module is the CLI and the public import
surface, and holds no card-generation logic.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import json_repair

from flashgen_core.config import DECK_NAME, DEFAULT_TAGS, debug_print
from flashgen_core.errors import ValidationError, classify
from flashgen_core.service import create_flashcard

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------
# Constants, pure functions and the one entry point the CLI calls. These are
# re-exported from flashgen_core, so each name here IS the same object the
# engine uses — reading or calling it through this module is exact.
#
# ⭐ WHAT IS DELIBERATELY NOT HERE: the engine's side-effecting collaborators
# (anki_invoke, store_media_file, generate_tts_file, check_anki_ready, add_note,
# fill_missing_translation, ANKI_CONNECT_URL, the collection functions, ...).
# Re-exporting a name is exact for reading but NOT for REBINDING: patching it
# here rebinds it in this namespace only, while the engine resolves it in the
# module that defines it — so the mock is silently ignored and the test makes
# real network calls instead of failing. Those names were removed so that
# `patch("flashgen.anki_invoke")` raises AttributeError at once (flashgen-76w).
# Patch a collaborator where its CALLER looks it up: flashgen_core.anki.*,
# flashgen_core.audio.*, flashgen_core.service.*. tests/test_facade.py enforces
# this; memory flashgen-test-mock-targets-after-engine-split has the mapping.
#
# create_flashcard stays because main() calls it, which also makes
# patch.object(flashgen, "create_flashcard") the CORRECT target for CLI tests.
# New code should import the flashgen_core module it needs, as flashgen_mcp does.
from flashgen_core.audio import (  # noqa: F401
    resolve_tts_config,
    safe_filename_stem,
    stable_audio_filename,
)
from flashgen_core.config import (  # noqa: F401
    CARD_TYPES,
    CUED_CARD_BACK,
    CUED_CARD_FRONT,
    CUED_MODEL_NAME,
    DEBUG,
    DEFAULT_TTS_PROVIDER,
    DIALOG_CARD_BACK,
    DIALOG_CARD_FRONT,
    DIALOG_MODEL_NAME,
    GEMINI_TTS_MODEL,
    GEMINI_TTS_VOICE,
    GENERATED_MODEL_CSS,
    GENERATED_MODEL_FIELDS,
    GENERATED_MODELS,
    MODEL_NAME,
    OPENAI_TEXT_MODEL,
    OPENAI_TTS_MODEL,
    OPENAI_TTS_VOICE,
    OUTPUT_DIR,
    TTSConfig,
    read_secret,
)
from flashgen_core.furigana import (  # noqa: F401
    ANNOTATED_KANJI_RE,
    furigana_errors,
    markup_errors,
    markup_warnings,
    normalize_furigana_annotation,
    normalize_furigana_text,
    normalize_line_breaks,
    resolve_tts_input,
    rich_text_to_html,
    sanitize_text,
    strip_emphasis_markup,
    strip_furigana_markup,
)
from flashgen_core.service import UPDATABLE_FIELDS  # noqa: F401


def read_json_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    try:
        data = json_repair.loads(raw)
    except Exception as e:
        raise ValidationError(
            "Failed to parse JSON from stdin. "
            'Expected JSON like: {"japanese":"...","english":"...","notes":"...","tags":["jp"]}'
        ) from e

    if not isinstance(data, dict):
        raise ValidationError("Input JSON must be an object.")

    return data


def main() -> None:
    try:
        data = read_json_input()
        debug_print("raw input JSON", data)

        japanese = str(data.get("japanese", "") or "")
        english = str(data.get("english", "") or "")
        notes = str(data.get("notes", "") or "")
        japanese_prompt = str(data.get("japanese_prompt", "") or "")
        english_prompt = str(data.get("english_prompt", "") or "")
        card_type = str(data.get("card_type", "standard") or "standard")
        japanese_tts = str(data.get("japanese_tts", "") or "")
        japanese_prompt_tts = str(data.get("japanese_prompt_tts", "") or "")
        deck_name = str(data.get("deck", DECK_NAME) or DECK_NAME)
        raw_tts_provider = data.get("tts_provider")
        raw_tts_model = data.get("tts_model")
        tts_provider = None if raw_tts_provider is None else str(raw_tts_provider or "")
        tts_model = None if raw_tts_model is None else str(raw_tts_model or "")

        raw_tags = data.get("tags", DEFAULT_TAGS)
        if raw_tags is None:
            tags = DEFAULT_TAGS
        elif isinstance(raw_tags, list) and all(isinstance(tag, str) for tag in raw_tags):
            tags = raw_tags
        else:
            raise ValidationError("'tags' must be a list of strings if provided.")

        debug_print(
            "parsed fields",
            {
                "japanese": japanese,
                "english": english,
                "notes": notes,
                "tags": tags,
                "japanese_prompt": japanese_prompt,
                "english_prompt": english_prompt,
                "card_type": card_type,
                "japanese_tts": japanese_tts,
                "japanese_prompt_tts": japanese_prompt_tts,
                "tts_provider": tts_provider,
                "tts_model": tts_model,
            },
        )

        result = create_flashcard(
            japanese=japanese,
            english=english,
            notes=notes,
            tags=tags,
            deck_name=deck_name,
            japanese_prompt=japanese_prompt,
            english_prompt=english_prompt,
            japanese_tts=japanese_tts,
            japanese_prompt_tts=japanese_prompt_tts,
            tts_provider=tts_provider,
            tts_model=tts_model,
            card_type=card_type,
        )

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        # Same error key and message the MCP server would report for this
        # failure — one classifier serves both front doors (flashgen-yya).
        # "status" stays alongside it so existing readers of the CLI envelope
        # keep working.
        info = classify(e)
        print(
            json.dumps(
                {
                    "status": "error",
                    **info.payload(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
