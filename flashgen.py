from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import json_repair
import requests
from openai import OpenAI

# -----------------------------
# Configuration
# -----------------------------
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_VOICE = "alloy"
OPENAI_TEXT_MODEL = "gpt-4.1-mini"

ANKI_CONNECT_URL = "http://127.0.0.1:8765"

# Change these to match your Anki setup
DECK_NAME = "日本語-Soso"
MODEL_NAME = "Japanese Listening+Production"

OUTPUT_DIR = Path("anki_audio_out")
DEFAULT_TAGS = ["jp", "auto", "conversation"]

# Debug flag
DEBUG = False


def debug_print(label: str, value: Any) -> None:
    if not DEBUG:
        return

    print(f"DEBUG {label}:")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


def safe_filename_stem(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+", "_", text)
    cleaned = cleaned.strip("_")
    return (cleaned or "audio")[:max_len]


def stable_audio_filename(japanese: str) -> str:
    digest = hashlib.sha1(japanese.encode("utf-8")).hexdigest()[:10]
    stem = safe_filename_stem(japanese)
    return f"{stem}_{digest}.mp3"


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == "\n")
    return text


def notes_to_html(notes: str) -> str:
    if not notes.strip():
        return ""

    notes = sanitize_text(notes)
    escaped = html.escape(notes, quote=False)
    return escaped.replace("\n", "<br>")


def anki_invoke(action: str, params: dict | None = None) -> object:
    payload = {
        "action": action,
        "version": 6,
        "params": params or {},
    }

    try:
        response = requests.post(ANKI_CONNECT_URL, json=payload, timeout=30)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            "Could not connect to AnkiConnect at http://127.0.0.1:8765. "
            "Make sure Anki is open and the AnkiConnect add-on is installed and enabled."
        ) from e

    data = response.json()

    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error on '{action}': {data['error']}")

    return data.get("result")


def check_anki_ready(deck_name: str, model_name: str) -> None:
    version = anki_invoke("version")
    if not isinstance(version, int):
        raise RuntimeError(f"Unexpected AnkiConnect version response: {version!r}")

    deck_names = anki_invoke("deckNames")
    if not isinstance(deck_names, list):
        raise RuntimeError(f"Unexpected deckNames response: {deck_names!r}")
    if deck_name not in deck_names:
        raise RuntimeError(
            f"Deck '{deck_name}' not found.\n"
            f"Available decks: {deck_names}"
        )

    model_names = anki_invoke("modelNames")
    if not isinstance(model_names, list):
        raise RuntimeError(f"Unexpected modelNames response: {model_names!r}")
    if model_name not in model_names:
        raise RuntimeError(
            f"Note type '{model_name}' not found.\n"
            f"Available note types: {model_names}"
        )


def get_model_field_names(model_name: str) -> list[str]:
    result = anki_invoke("modelFieldNames", {"modelName": model_name})
    if not isinstance(result, list) or not all(isinstance(x, str) for x in result):
        raise RuntimeError(f"Unexpected response from modelFieldNames: {result!r}")
    return result


def can_add_note(note: dict[str, Any]) -> bool:
    result = anki_invoke("canAddNotes", {"notes": [note]})
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], bool):
        raise RuntimeError(f"Unexpected response from canAddNotes: {result!r}")
    return result[0]


def find_existing_notes(model_name: str, japanese: str) -> list[int]:
    query = f'note:"{model_name}" "Japanese:{japanese}"'
    result = anki_invoke("findNotes", {"query": query})
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected response from findNotes: {result!r}")
    if not all(isinstance(x, int) for x in result):
        raise RuntimeError(f"findNotes returned non-int note ids: {result!r}")
    return result


def get_notes_info(note_ids: list[int]) -> object:
    return anki_invoke("notesInfo", {"notes": note_ids})


def generate_tts_file(client: OpenAI, text: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with client.audio.speech.with_streaming_response.create(
        model=OPENAI_TTS_MODEL,
        voice=OPENAI_TTS_VOICE,
        input=text,
    ) as response:
        response.stream_to_file(out_path)


def store_media_file(local_path: Path, desired_filename: str) -> str:
    audio_b64 = base64.b64encode(local_path.read_bytes()).decode("ascii")
    stored_name = anki_invoke(
        "storeMediaFile",
        {
            "filename": desired_filename,
            "data": audio_b64,
        },
    )
    if not isinstance(stored_name, str):
        raise RuntimeError(f"Unexpected response from storeMediaFile: {stored_name!r}")
    return stored_name


def fill_missing_translation(
    client: OpenAI,
    japanese: str,
    english: str,
) -> tuple[str, str]:
    japanese = japanese.strip()
    english = english.strip()

    if not japanese and not english:
        raise RuntimeError("Both 'japanese' and 'english' are empty. Provide at least one.")

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


def add_note(
    deck_name: str,
    model_name: str,
    japanese: str,
    english: str,
    notes: str,
    audio_filename: str,
    tags: list[str],
    japanese_prompt: str = "",
    english_prompt: str = "",
    audio_prompt_filename: str = "",
) -> int:
    fields = {
        "Japanese": html.escape(sanitize_text(japanese), quote=False),
        "English": html.escape(sanitize_text(english), quote=False),
        "Notes": notes_to_html(notes),
        "Audio": f"[sound:{audio_filename}]",
    }
    if japanese_prompt:
        fields["Japanese Prompt"] = html.escape(sanitize_text(japanese_prompt), quote=False)
        fields["English Prompt"] = html.escape(sanitize_text(english_prompt), quote=False)
        fields["Audio Prompt"] = f"[sound:{audio_prompt_filename}]"

    note: dict[str, Any] = {
        "deckName": deck_name,
        "modelName": model_name,
        "fields": fields,
        "tags": tags,
        "options": {
            "allowDuplicate": False,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {
                "deckName": deck_name,
                "checkChildren": False,
                "checkAllModels": False,
            },
        },
    }

    debug_print("add_note payload", note)

    allowed = can_add_note(note)
    debug_print("canAddNotes", allowed)

    if not allowed:
        existing = find_existing_notes(model_name, japanese)
        info = get_notes_info(existing) if existing else []
        raise RuntimeError(
            "AnkiConnect says this note cannot be added.\n"
            f"Japanese field: {japanese}\n"
            f"Existing note ids: {existing}\n"
            f"Existing note info: {json.dumps(info, ensure_ascii=False, indent=2)}"
        )

    result = anki_invoke("addNote", {"note": note})
    if not isinstance(result, int):
        raise RuntimeError(f"Unexpected response from addNote: {result!r}")
    return result


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
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    final_tags = tags if tags is not None else DEFAULT_TAGS

    check_anki_ready(deck_name, model_name)
    field_names = get_model_field_names(model_name)
    debug_print("model fields", field_names)

    client = OpenAI(api_key=api_key)

    japanese, english = fill_missing_translation(client, japanese, english)

    debug_print(
        "fields after fill_missing_translation",
        {
            "japanese": japanese,
            "english": english,
            "notes": notes,
            "tags": final_tags,
        },
    )

    audio_filename = stable_audio_filename(japanese)
    local_audio_path = OUTPUT_DIR / audio_filename
    tts_input = re.sub(r"\[[^\]]+\]", "", japanese)
    generate_tts_file(client, tts_input, local_audio_path)

    stored_audio_name = store_media_file(local_audio_path, audio_filename)

    audio_prompt_filename = ""
    if japanese_prompt:
        audio_prompt_filename = stable_audio_filename(japanese_prompt)
        local_audio_prompt_path = OUTPUT_DIR / audio_prompt_filename
        tts_prompt_input = re.sub(r"\[[^\]]+\]", "", japanese_prompt)
        generate_tts_file(client, tts_prompt_input, local_audio_prompt_path)
        audio_prompt_filename = store_media_file(local_audio_prompt_path, audio_prompt_filename)

    note_id = add_note(
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
        "japanese": japanese,
        "english": english,
        "notes": notes,
        "tags": final_tags,
        "audio_file": stored_audio_name,
        "local_audio_path": str(local_audio_path),
    }
    if japanese_prompt:
        result["japanese_prompt"] = japanese_prompt
        result["english_prompt"] = english_prompt
        result["audio_prompt_file"] = audio_prompt_filename
    return result


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
            },
        )

        result = create_flashcard(
            japanese=japanese,
            english=english,
            notes=notes,
            tags=tags,
            japanese_prompt=japanese_prompt,
            english_prompt=english_prompt,
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