"""Orchestration: the two entry points the CLI and the MCP server share,
create_flashcard and update_note, plus translation fill-in.
"""
from __future__ import annotations

import html
from typing import Any

from openai import OpenAI

from flashgen_core import anki, audio
from flashgen_core.config import (
    CARD_TYPES,
    DECK_NAME,
    DEFAULT_TAGS,
    GENERATED_MODELS,
    MODEL_NAME,
    OPENAI_TEXT_MODEL,
    OUTPUT_DIR,
    TTSConfig,
    debug_print,
    read_secret,
)
from flashgen_core.errors import MissingApiKey, ValidationError
from flashgen_core.furigana import (
    _reject_bad_markup,
    normalize_furigana_text,
    normalize_line_breaks,
    resolve_tts_input,
    rich_text_to_html,
)

# Request-level field names accepted by update_note, mapped like create.
UPDATABLE_FIELDS = {
    "japanese",
    "english",
    "notes",
    "japanese_prompt",
    "english_prompt",
    "japanese_tts",
    "japanese_prompt_tts",
}


def fill_missing_translation(
    client: OpenAI,
    japanese: str,
    english: str,
) -> tuple[str, str]:
    japanese = japanese.strip()
    english = english.strip()

    if not japanese and not english:
        raise ValidationError(
            "Both 'japanese' and 'english' are empty. Provide at least one."
        )

    if japanese and english:
        return japanese, english

    if japanese and not english:
        prompt = (
            "Translate the following Japanese sentence into natural English.\n"
            "Return only the translation text, with no quotes and no explanation.\n\n"
            f"Japanese: {japanese}"
        )
    else:
        prompt = (
            "Translate the following English sentence into natural Japanese.\n"
            "Return only the translation text, with no quotes and no explanation.\n\n"
            f"English: {english}"
        )

    response = client.responses.create(
        model=OPENAI_TEXT_MODEL,
        input=prompt,
    )

    translated = response.output_text.strip()
    if not translated:
        raise RuntimeError("Translation model returned empty output.")

    if japanese:
        return japanese, translated
    return translated, english


def create_flashcard(
    *,
    japanese: str = "",
    english: str = "",
    notes: str = "",
    tags: list[str] | None = None,
    deck_name: str = DECK_NAME,
    model_name: str = MODEL_NAME,
    japanese_prompt: str = "",
    english_prompt: str = "",
    japanese_tts: str = "",
    japanese_prompt_tts: str = "",
    tts_provider: str | None = None,
    tts_model: str | None = None,
    card_type: str = "standard",
) -> dict[str, Any]:
    if card_type not in CARD_TYPES:
        raise ValidationError(f"'card_type' must be one of: {', '.join(CARD_TYPES)}.")

    _reject_bad_markup(
        japanese=japanese,
        english=english,
        notes=notes,
        japanese_prompt=japanese_prompt,
        english_prompt=english_prompt,
    )

    final_tags = tags if tags is not None else DEFAULT_TAGS
    tts_config = audio.resolve_tts_config(tts_provider, tts_model)

    if card_type in GENERATED_MODELS:
        if not japanese_prompt.strip():
            raise ValidationError(
                f"card_type '{card_type}' requires a non-empty 'japanese_prompt' "
                f"— {GENERATED_MODELS[card_type]['prompt_required_because']}."
            )
        # The card_type selects the note type, so the model_name kwarg does not
        # apply. ensure_* runs before the readiness check, so a missing note
        # type is created rather than a hard error; check_anki_ready then only
        # has the deck left to verify.
        model_name = anki.ensure_generated_model(card_type)
        anki.check_anki_ready(deck_name)
    else:
        anki.check_anki_ready(deck_name, model_name)
    field_names = anki.get_model_field_names(model_name)
    debug_print("model fields", field_names)

    if not japanese.strip() or not english.strip():
        api_key = read_secret("OPENAI_API_KEY")
        if not api_key:
            raise MissingApiKey("OPENAI_API_KEY is not set.")
        client = OpenAI(api_key=api_key)
        japanese, english = fill_missing_translation(client, japanese, english)

    # Normalize breaks at intake, before the TTS inputs are derived below, so
    # literal <br>/"\n" from a caller never reach TTS text or audio filenames.
    japanese = normalize_furigana_text(normalize_line_breaks(japanese))
    japanese_prompt = normalize_furigana_text(normalize_line_breaks(japanese_prompt))
    # Notes is rendered with {{furigana:Notes}} too, so it needs the same
    # leading-space normalization (omitted until 2026-09-11).
    notes = normalize_furigana_text(normalize_line_breaks(notes))
    # The English fields need no furigana pass, but they do need the same break
    # normalization so the result dict below reports breaks the same way for
    # every text field. Without it a caller's literal "\n" survives in the echo
    # while the Japanese fields echo real newlines (flashgen-adv). This does not
    # change what is stored: rich_text_to_html normalizes again internally and
    # normalize_line_breaks is idempotent, so it stays the only producer of HTML.
    english = normalize_line_breaks(english)
    english_prompt = normalize_line_breaks(english_prompt)
    japanese_tts = resolve_tts_input(japanese, japanese_tts)
    japanese_prompt_tts = resolve_tts_input(japanese_prompt, japanese_prompt_tts)

    debug_print(
            "fields after fill_missing_translation",
            {
                "japanese": japanese,
                "english": english,
                "notes": notes,
                "tags": final_tags,
                "japanese_tts": japanese_tts,
                "japanese_prompt_tts": japanese_prompt_tts,
                "tts_provider": tts_config.provider,
                "tts_model": tts_config.model,
            },
        )

    audio_filename = audio.stable_audio_filename(japanese_tts, tts_config.extension)
    local_audio_path = OUTPUT_DIR / audio_filename
    audio.generate_tts_file(tts_config, japanese_tts, local_audio_path)

    stored_audio_name = anki.store_media_file(local_audio_path, audio_filename)

    audio_prompt_filename = ""
    if japanese_prompt:
        audio_prompt_filename = audio.stable_audio_filename(
            japanese_prompt_tts, tts_config.extension
        )
        local_audio_prompt_path = OUTPUT_DIR / audio_prompt_filename
        audio.generate_tts_file(tts_config, japanese_prompt_tts, local_audio_prompt_path)
        audio_prompt_filename = anki.store_media_file(local_audio_prompt_path, audio_prompt_filename)

    note_id = anki.add_note(
        deck_name=deck_name,
        model_name=model_name,
        japanese=japanese,
        english=english,
        notes=notes,
        audio_filename=stored_audio_name,
        tags=final_tags,
        japanese_prompt=japanese_prompt,
        english_prompt=english_prompt,
        audio_prompt_filename=audio_prompt_filename,
    )

    result: dict[str, Any] = {
        "status": "ok",
        "note_id": note_id,
        "deck": deck_name,
        "model": model_name,
        "card_type": card_type,
        "japanese": japanese,
        "english": english,
        "notes": notes,
        "tags": final_tags,
        "audio_file": stored_audio_name,
        "local_audio_path": str(local_audio_path),
        "tts_provider": tts_config.provider,
        "tts_model": tts_config.model,
        "japanese_tts": japanese_tts,
    }
    if japanese_prompt:
        result["japanese_prompt"] = japanese_prompt
        result["english_prompt"] = english_prompt
        result["japanese_prompt_tts"] = japanese_prompt_tts
        result["audio_prompt_file"] = audio_prompt_filename
    return result


def _regenerate_audio(display_text: str, tts_text: str, tts_config: TTSConfig) -> str:
    """Generate + store TTS for updated text; returns the [sound:...] field value."""
    resolved = resolve_tts_input(display_text, tts_text)
    if not resolved:
        return ""
    filename = audio.stable_audio_filename(resolved, tts_config.extension)
    local_path = OUTPUT_DIR / filename
    audio.generate_tts_file(tts_config, resolved, local_path)
    stored = anki.store_media_file(local_path, filename)
    return f"[sound:{stored}]"


def update_note(
    note_id: int,
    fields: dict[str, str] | None = None,
    tags: list[str] | None = None,
    tts_provider: str | None = None,
    tts_model: str | None = None,
) -> dict[str, Any]:
    fields = fields or {}
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValidationError(
            f"Unknown field(s): {', '.join(sorted(unknown))}. "
            f"Updatable fields: {', '.join(sorted(UPDATABLE_FIELDS))}."
        )
    if not fields and tags is None:
        raise ValidationError("Provide 'fields' and/or 'tags' to update.")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        raise ValidationError("'tags' must be a list of strings.")

    _reject_bad_markup(
        **{k: v for k, v in fields.items() if not k.endswith("_tts")}
    )

    current = anki.get_note(note_id)  # raises if the note doesn't exist

    anki_fields: dict[str, str] = {}
    japanese = None
    if "japanese" in fields:
        japanese = normalize_furigana_text(normalize_line_breaks(fields["japanese"]))
        anki_fields["Japanese"] = rich_text_to_html(japanese)
    if "english" in fields:
        anki_fields["English"] = rich_text_to_html(fields["english"])
    if "notes" in fields:
        anki_fields["Notes"] = rich_text_to_html(
            normalize_furigana_text(normalize_line_breaks(fields["notes"]))
        )
    japanese_prompt = None
    if "japanese_prompt" in fields:
        japanese_prompt = normalize_furigana_text(
            normalize_line_breaks(fields["japanese_prompt"])
        )
        anki_fields["Japanese Prompt"] = rich_text_to_html(japanese_prompt)
    if "english_prompt" in fields:
        anki_fields["English Prompt"] = rich_text_to_html(fields["english_prompt"])

    # Changing Japanese text (or its TTS override) invalidates the stored audio,
    # so regenerate — including Audio Prompt for prompt changes (dialog cards are
    # prompt-audio-first; stale prompt audio silently breaks the card front).
    regen_main = "japanese" in fields or "japanese_tts" in fields
    regen_prompt = "japanese_prompt" in fields or "japanese_prompt_tts" in fields
    if regen_main or regen_prompt:
        tts_config = audio.resolve_tts_config(tts_provider, tts_model)
    if regen_main:
        # Recovering display text from the stored HTML field leaves real <br>
        # tags in place; resolve_tts_input normalizes them away before TTS.
        display = (
            japanese
            if japanese is not None
            else html.unescape(current["fields"].get("Japanese", ""))
        )
        anki_fields["Audio"] = _regenerate_audio(
            display, fields.get("japanese_tts", ""), tts_config
        )
    if regen_prompt:
        display = (
            japanese_prompt
            if japanese_prompt is not None
            else html.unescape(current["fields"].get("Japanese Prompt", ""))
        )
        anki_fields["Audio Prompt"] = _regenerate_audio(
            display, fields.get("japanese_prompt_tts", ""), tts_config
        )

    if anki_fields:
        anki.anki_invoke("updateNoteFields", {"note": {"id": note_id, "fields": anki_fields}})

    if tags is not None:
        old_tags = set(current["tags"])
        new_tags = set(tags)
        to_remove = sorted(old_tags - new_tags)
        to_add = sorted(new_tags - old_tags)
        if to_remove:
            anki.anki_invoke("removeTags", {"notes": [note_id], "tags": " ".join(to_remove)})
        if to_add:
            anki.anki_invoke("addTags", {"notes": [note_id], "tags": " ".join(to_add)})

    result = anki.get_note(note_id)
    result["status"] = "ok"
    return result
