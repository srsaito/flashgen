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
from flashgen_core.service import create_flashcard

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------
# `import flashgen` remains the one-line way in for callers that want the whole
# engine. These names are re-exported from flashgen_core so imports written
# against the pre-split module keep working.
#
# New code should import the module it needs from flashgen_core directly, as
# flashgen_mcp.app does. Note for test authors: patching a name HERE only
# affects lookups that go through this module (i.e. main's create_flashcard).
# To intercept a collaborator inside the engine, patch it in its defining
# module — flashgen_core.anki.anki_invoke, flashgen_core.audio.generate_tts_file.
from flashgen_core.anki import (  # noqa: F401
    SEARCH_MATCH_FIELDS,
    _cards_by_note,
    _field_text,
    _find_note_ids,
    _matches_word,
    _note_entry,
    _wildcard_interleave,
    add_note,
    anki_invoke,
    can_add_note,
    check_anki_ready,
    delete_notes,
    ensure_cued_model,
    ensure_dialog_model,
    ensure_generated_model,
    find_existing_notes,
    get_model_field_names,
    get_note,
    get_notes_info,
    list_decks,
    list_tags,
    search_notes,
    store_media_file,
)
from flashgen_core.audio import (  # noqa: F401
    generate_gemini_tts_file,
    generate_openai_tts_file,
    generate_tts_file,
    resolve_tts_config,
    safe_filename_stem,
    stable_audio_filename,
    write_wave_file,
)
from flashgen_core.config import (  # noqa: F401
    ANKI_CONNECT_URL,
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
from flashgen_core.service import (  # noqa: F401
    UPDATABLE_FIELDS,
    fill_missing_translation,
    update_note,
)


def read_json_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    try:
        data = json_repair.loads(raw)
    except Exception as e:
        raise RuntimeError(
            "Failed to parse JSON from stdin. "
            'Expected JSON like: {"japanese":"...","english":"...","notes":"...","tags":["jp"]}'
        ) from e

    if not isinstance(data, dict):
        raise RuntimeError("Input JSON must be an object.")

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
            raise RuntimeError("'tags' must be a list of strings if provided.")

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
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": str(e),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
