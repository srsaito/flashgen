from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json_repair
import requests
from openai import OpenAI

# -----------------------------
# Configuration
# -----------------------------
DEFAULT_TTS_PROVIDER = "gemini"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_VOICE = "alloy"
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_VOICE = "Kore"
OPENAI_TEXT_MODEL = "gpt-4.1-mini"

ANKI_CONNECT_URL = "http://127.0.0.1:8765"

# Change these to match your Anki setup
DECK_NAME = "日本語-Soso"
MODEL_NAME = "Japanese Listening+Production"

OUTPUT_DIR = Path("anki_audio_out")
DEFAULT_TAGS = ["jp", "auto", "conversation"]

# Debug flag
DEBUG = False

KANJI_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+$")
ANNOTATED_KANJI_RE = re.compile(
    r"[\u0020\u3000]*([\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+)\[([^\]]+)\]"
)


@dataclass(frozen=True)
class TTSConfig:
    provider: str
    model: str
    voice: str
    extension: str


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


def stable_audio_filename(japanese: str, extension: str = ".mp3") -> str:
    digest = hashlib.sha1(japanese.encode("utf-8")).hexdigest()[:10]
    stem = safe_filename_stem(japanese)
    return f"{stem}_{digest}{extension}"


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == "\n")
    return text


def is_kanji_text(text: str) -> bool:
    return bool(KANJI_RE.fullmatch(text))


def split_evenly(text: str, chunk_count: int) -> list[str] | None:
    if chunk_count <= 0 or len(text) % chunk_count != 0:
        return None

    chunk_size = len(text) // chunk_count
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def normalize_furigana_annotation(kanji: str, reading: str) -> str:
    if not is_kanji_text(kanji) or len(kanji) == 1:
        return f" {kanji}[{reading}]"

    reading_chunks = split_evenly(reading, len(kanji))
    if reading_chunks is None:
        return f" {kanji}[{reading}]"

    return "".join(
        f" {kanji_char}[{reading_chunk}]"
        for kanji_char, reading_chunk in zip(kanji, reading_chunks)
    )


def normalize_furigana_text(text: str) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    return ANNOTATED_KANJI_RE.sub(
        lambda match: normalize_furigana_annotation(match.group(1), match.group(2)),
        text,
    )


def strip_furigana_markup(text: str) -> str:
    if not text:
        return ""

    return ANNOTATED_KANJI_RE.sub(lambda match: match.group(1), text)


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


def resolve_tts_config(
    tts_provider: str | None = None,
    tts_model: str | None = None,
) -> TTSConfig:
    provider = (tts_provider or "").strip()
    model = (tts_model or "").strip()

    if bool(provider) != bool(model):
        raise RuntimeError("'tts_provider' and 'tts_model' must be provided together.")

    if not provider and not model:
        provider = DEFAULT_TTS_PROVIDER
        model = GEMINI_TTS_MODEL

    provider = provider.lower()
    if provider == "openai":
        extension = ".mp3"
        voice = OPENAI_TTS_VOICE
    elif provider == "gemini":
        extension = ".wav"
        voice = GEMINI_TTS_VOICE
    else:
        raise RuntimeError("'tts_provider' must be one of: openai, gemini.")

    if not model:
        raise RuntimeError("'tts_model' must be a non-empty string.")

    return TTSConfig(
        provider=provider,
        model=model,
        voice=voice,
        extension=extension,
    )


def write_wave_file(
    out_path: Path,
    pcm_data: bytes,
    *,
    channels: int = 1,
    rate: int = 24000,
    sample_width: int = 2,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm_data)


def generate_openai_tts_file(
    client: OpenAI,
    tts_config: TTSConfig,
    text: str,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with client.audio.speech.with_streaming_response.create(
        model=tts_config.model,
        voice=tts_config.voice,
        input=text,
    ) as response:
        response.stream_to_file(out_path)


def generate_gemini_tts_file(
    api_key: str,
    tts_config: TTSConfig,
    text: str,
    out_path: Path,
) -> None:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "Gemini TTS requires the 'google-genai' package to be installed."
        ) from e

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=tts_config.model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=tts_config.voice
                    )
                )
            ),
        ),
    )

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("Gemini TTS returned no candidates.")

    parts = getattr(candidates[0].content, "parts", None) or []
    if not parts or getattr(parts[0], "inline_data", None) is None:
        raise RuntimeError("Gemini TTS returned no audio payload.")

    inline_data = parts[0].inline_data
    audio_data = inline_data.data
    if isinstance(audio_data, str):
        audio_bytes = base64.b64decode(audio_data)
    else:
        audio_bytes = audio_data

    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise RuntimeError("Gemini TTS returned empty audio data.")

    write_wave_file(out_path, audio_bytes)


def generate_tts_file(tts_config: TTSConfig, text: str, out_path: Path) -> None:
    if tts_config.provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        generate_openai_tts_file(
            OpenAI(api_key=api_key),
            tts_config,
            text,
            out_path,
        )
        return

    if tts_config.provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        generate_gemini_tts_file(api_key, tts_config, text, out_path)
        return

    raise RuntimeError(f"Unsupported TTS provider: {tts_config.provider}")


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
    tts_provider: str | None = None,
    tts_model: str | None = None,
) -> dict[str, Any]:
    final_tags = tags if tags is not None else DEFAULT_TAGS
    tts_config = resolve_tts_config(tts_provider, tts_model)

    check_anki_ready(deck_name, model_name)
    field_names = get_model_field_names(model_name)
    debug_print("model fields", field_names)

    if not japanese.strip() or not english.strip():
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        client = OpenAI(api_key=api_key)
        japanese, english = fill_missing_translation(client, japanese, english)

    japanese = normalize_furigana_text(japanese)
    japanese_prompt = normalize_furigana_text(japanese_prompt)

    debug_print(
            "fields after fill_missing_translation",
            {
                "japanese": japanese,
                "english": english,
                "notes": notes,
                "tags": final_tags,
                "tts_provider": tts_config.provider,
                "tts_model": tts_config.model,
            },
        )

    audio_filename = stable_audio_filename(japanese, tts_config.extension)
    local_audio_path = OUTPUT_DIR / audio_filename
    tts_input = strip_furigana_markup(japanese)
    generate_tts_file(tts_config, tts_input, local_audio_path)

    stored_audio_name = store_media_file(local_audio_path, audio_filename)

    audio_prompt_filename = ""
    if japanese_prompt:
        audio_prompt_filename = stable_audio_filename(
            japanese_prompt, tts_config.extension
        )
        local_audio_prompt_path = OUTPUT_DIR / audio_prompt_filename
        tts_prompt_input = strip_furigana_markup(japanese_prompt)
        generate_tts_file(tts_config, tts_prompt_input, local_audio_prompt_path)
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
        "tts_provider": tts_config.provider,
        "tts_model": tts_config.model,
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
            tts_provider=tts_provider,
            tts_model=tts_model,
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
