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
OPENAI_TTS_VOICE = "onyx"
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TTS_VOICE = "Algenib"
OPENAI_TEXT_MODEL = "gpt-4.1-mini"

ANKI_CONNECT_URL = os.environ.get("ANKI_CONNECT_URL", "http://127.0.0.1:8765")


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

# Change these to match your Anki setup
DECK_NAME = "日本語-Soso"
MODEL_NAME = "Japanese Listening+Production"

# Single-card 場面-response note type, created programmatically via AnkiConnect
# createModel (docs/SPEC-dialog-response.md). Same fields as MODEL_NAME so
# add_note()'s field mapping is shared; exactly one card template.
DIALOG_MODEL_NAME = "Japanese Dialog Response"

CARD_TYPES = ("standard", "dialog_response")

DIALOG_MODEL_FIELDS = [
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

DIALOG_MODEL_CSS = """\
.card {
  font-family: arial;
  font-size: 20px;
  text-align: center;
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

OUTPUT_DIR = Path("anki_audio_out")
DEFAULT_TAGS = ["jp", "auto", "conversation"]

# Debug flag
DEBUG = False

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


def normalize_furigana_annotation(kanji: str, reading: str) -> str:
    """Emit a furigana annotation as a single whole unit: ` kanji[reading]`.

    We deliberately do NOT re-derive per-kanji readings. Splitting a compound's
    reading across its kanji is reading-unsafe — pronunciation and morpheme
    boundaries don't line up with character counts (e.g. 試着室[しちゃくしつ]
    sliced evenly yields 試[しち] 着[ゃく] 室[しつ], where 着[ゃく] even begins
    with a standalone small ゃ). Keeping the bracket over the whole kanji run is
    always reading-safe and respects whatever grouping the LLM chose, which owns
    furigana insertion. The leading space is the Anki furigana separator; the
    caller's regex collapses any pre-existing spacing into this one.
    """
    return f" {kanji}[{reading}]"


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


def resolve_tts_input(display_text: str, tts_text: str = "") -> str:
    candidate = tts_text if tts_text.strip() else strip_furigana_markup(display_text)
    candidate = unicodedata.normalize("NFC", sanitize_text(candidate))
    return strip_furigana_markup(candidate).strip()


# Line-break representations a caller may supply in the notes field. Anki
# fields are HTML: only <br> renders a break, so every break form must funnel
# to a real newline BEFORE html.escape (otherwise a literal <br> escapes into
# the visible text "&lt;br&gt;"), then back to <br> after escaping.
_BR_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def notes_to_html(notes: str) -> str:
    if not notes.strip():
        return ""

    notes = sanitize_text(notes)
    # Normalize all break representations to a real newline up front. Callers
    # vary: the local CLI sends real newlines, MCP clients send literal <br>
    # tags or escaped "\n" sequences. Doing this before escaping keeps genuine
    # HTML-special chars in definitions safe while still rendering breaks.
    notes = _BR_TAG_RE.sub("\n", notes)
    notes = notes.replace("\r\n", "\n").replace("\r", "\n")
    notes = notes.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
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
            f"Could not connect to AnkiConnect at {ANKI_CONNECT_URL}. "
            "Make sure Anki is open and the AnkiConnect add-on is installed and enabled."
        ) from e

    data = response.json()

    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error on '{action}': {data['error']}")

    return data.get("result")


def check_anki_ready(deck_name: str, model_name: str | None = None) -> None:
    """Verify AnkiConnect is reachable and the deck (and optionally model) exist.

    model_name=None skips the note-type check — used by the dialog path, where
    ensure_dialog_model() has already verified or created the model.
    """
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

    if model_name is None:
        return

    model_names = anki_invoke("modelNames")
    if not isinstance(model_names, list):
        raise RuntimeError(f"Unexpected modelNames response: {model_names!r}")
    if model_name not in model_names:
        raise RuntimeError(
            f"Note type '{model_name}' not found.\n"
            f"Available note types: {model_names}"
        )


def ensure_dialog_model() -> None:
    """Create the Japanese Dialog Response note type via createModel if absent.

    Templates live in code (single source of truth); the model syncs to
    AnkiWeb / the headless container like any other collection change. The
    legacy MODEL_NAME stays manually managed.
    """
    model_names = anki_invoke("modelNames")
    if not isinstance(model_names, list):
        raise RuntimeError(f"Unexpected modelNames response: {model_names!r}")
    if DIALOG_MODEL_NAME in model_names:
        return

    anki_invoke(
        "createModel",
        {
            "modelName": DIALOG_MODEL_NAME,
            "inOrderFields": list(DIALOG_MODEL_FIELDS),
            "css": DIALOG_MODEL_CSS,
            "isCloze": False,
            "cardTemplates": [
                {
                    "Name": "Response",
                    "Front": DIALOG_CARD_FRONT,
                    "Back": DIALOG_CARD_BACK,
                }
            ],
        },
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
        api_key = read_secret("OPENAI_API_KEY")
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
        api_key = read_secret("GEMINI_API_KEY")
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
    japanese_tts: str = "",
    japanese_prompt_tts: str = "",
    tts_provider: str | None = None,
    tts_model: str | None = None,
    card_type: str = "standard",
) -> dict[str, Any]:
    if card_type not in CARD_TYPES:
        raise RuntimeError(f"'card_type' must be one of: {', '.join(CARD_TYPES)}.")

    final_tags = tags if tags is not None else DEFAULT_TAGS
    tts_config = resolve_tts_config(tts_provider, tts_model)

    if card_type == "dialog_response":
        if not japanese_prompt.strip():
            raise RuntimeError(
                "card_type 'dialog_response' requires a non-empty 'japanese_prompt' "
                "— the prompt audio is the entire front of the card."
            )
        model_name = DIALOG_MODEL_NAME
        ensure_dialog_model()
        check_anki_ready(deck_name)
    else:
        check_anki_ready(deck_name, model_name)
    field_names = get_model_field_names(model_name)
    debug_print("model fields", field_names)

    if not japanese.strip() or not english.strip():
        api_key = read_secret("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        client = OpenAI(api_key=api_key)
        japanese, english = fill_missing_translation(client, japanese, english)

    japanese = normalize_furigana_text(japanese)
    japanese_prompt = normalize_furigana_text(japanese_prompt)
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

    audio_filename = stable_audio_filename(japanese_tts, tts_config.extension)
    local_audio_path = OUTPUT_DIR / audio_filename
    generate_tts_file(tts_config, japanese_tts, local_audio_path)

    stored_audio_name = store_media_file(local_audio_path, audio_filename)

    audio_prompt_filename = ""
    if japanese_prompt:
        audio_prompt_filename = stable_audio_filename(
            japanese_prompt_tts, tts_config.extension
        )
        local_audio_prompt_path = OUTPUT_DIR / audio_prompt_filename
        generate_tts_file(tts_config, japanese_prompt_tts, local_audio_prompt_path)
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


# -----------------------------
# Collection read/write (MCP search/get/list + delete/update)
# -----------------------------

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

SEARCH_MATCH_FIELDS = ("japanese_tts", "japanese", "english", "any")

# search_notes truncates field values in results so a broad query can't dump
# whole notes into the caller's context; get_note returns full values.
_FIELD_VALUE_MAX = 400

# Post-filtering needs notesInfo per candidate, so cap how many candidates a
# single search will fetch before filtering.
_SEARCH_CANDIDATE_CAP = 500


def list_decks() -> list[str]:
    result = anki_invoke("deckNames")
    if not isinstance(result, list) or not all(isinstance(x, str) for x in result):
        raise RuntimeError(f"Unexpected response from deckNames: {result!r}")
    return result


def list_tags() -> list[str]:
    result = anki_invoke("getTags")
    if not isinstance(result, list) or not all(isinstance(x, str) for x in result):
        raise RuntimeError(f"Unexpected response from getTags: {result!r}")
    return result


def _find_note_ids(query: str) -> list[int]:
    result = anki_invoke("findNotes", {"query": query})
    if not isinstance(result, list) or not all(isinstance(x, int) for x in result):
        raise RuntimeError(f"Unexpected response from findNotes: {result!r}")
    return result


def _cards_by_note(note_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Map note id → {card_ids, deck} for a batch of notes (2 AnkiConnect calls)."""
    if not note_ids:
        return {}
    nid_query = "nid:" + ",".join(str(nid) for nid in note_ids)
    card_ids = anki_invoke("findCards", {"query": nid_query})
    if not isinstance(card_ids, list):
        raise RuntimeError(f"Unexpected response from findCards: {card_ids!r}")
    card_infos = anki_invoke("cardsInfo", {"cards": card_ids})
    if not isinstance(card_infos, list):
        raise RuntimeError(f"Unexpected response from cardsInfo: {card_infos!r}")
    by_note: dict[int, dict[str, Any]] = {}
    for card in card_infos:
        if not isinstance(card, dict):
            continue
        nid = card.get("note")
        entry = by_note.setdefault(nid, {"card_ids": [], "deck": card.get("deckName", "")})
        entry["card_ids"].append(card.get("cardId"))
    return by_note


def _wildcard_interleave(word: str) -> str:
    """Anki query matching `word`'s characters in order with anything between.

    Furigana markup splices ` [reading]` between kanji ( 掃[そう] 除[じ] 機[き]),
    so a literal substring search for 掃除機 misses; interleaved wildcards
    (*掃*除*機*) tolerate the annotations. Over-matching is corrected by the
    Python post-filter.
    """
    return "*" + "*".join(word) + "*"


# The furigana convention also puts spaces before UNANNOTATED units
# (その 報[ほう] 告[こく]は ある…), and strip_furigana_markup only consumes the
# spaces attached to annotated runs — so stripped field text keeps interior
# spaces the query never has (flashgen-lhg). Ignore all spacing when matching.
_JA_SPACE_RE = re.compile(r"[\u0020\u00a0\u3000]+")


def _despace(text: str) -> str:
    return _JA_SPACE_RE.sub("", text)


def _note_entry(
    info: dict[str, Any],
    cards: dict[str, Any] | None,
    *,
    full_fields: bool,
) -> dict[str, Any]:
    fields = {}
    for name, cell in (info.get("fields") or {}).items():
        value = cell.get("value", "") if isinstance(cell, dict) else str(cell)
        if not full_fields and len(value) > _FIELD_VALUE_MAX:
            value = value[:_FIELD_VALUE_MAX] + "…"
        fields[name] = value
    cards = cards or {}
    card_ids = cards.get("card_ids", [])
    return {
        "note_id": info.get("noteId"),
        "deck": cards.get("deck", ""),
        "model": info.get("modelName", ""),
        "tags": info.get("tags", []),
        "card_ids": card_ids,
        "card_count": len(card_ids),
        "fields": fields,
    }


def _field_text(info: dict[str, Any], field_name: str) -> str:
    cell = (info.get("fields") or {}).get(field_name)
    value = cell.get("value", "") if isinstance(cell, dict) else ""
    return html.unescape(value)


def _matches_word(info: dict[str, Any], word: str, match_field: str) -> bool:
    if match_field == "japanese_tts":
        return _despace(word) in _despace(
            strip_furigana_markup(_field_text(info, "Japanese"))
        )
    if match_field == "japanese":
        return word in _field_text(info, "Japanese")
    if match_field == "english":
        return word.lower() in _field_text(info, "English").lower()
    # any
    japanese = _despace(strip_furigana_markup(_field_text(info, "Japanese")))
    if _despace(word) in japanese:
        return True
    haystack = " ".join((_field_text(info, "English"), _field_text(info, "Notes")))
    return word.lower() in haystack.lower()


def search_notes(
    query: str = "",
    deck: str | None = None,
    limit: int = 25,
    match_field: str = "japanese_tts",
) -> dict[str, Any]:
    if match_field not in SEARCH_MATCH_FIELDS:
        raise RuntimeError(
            f"'match_field' must be one of: {', '.join(SEARCH_MATCH_FIELDS)}."
        )
    if not isinstance(limit, int) or limit < 1:
        raise RuntimeError("'limit' must be a positive integer.")

    query = (query or "").strip()
    if not query and not deck:
        raise RuntimeError("Provide 'query' and/or 'deck'.")

    # A query containing Anki operators (deck:, tag:, nid:, field:...), quoted
    # phrases, or wildcards is passed through as-is; bare words get per-field
    # matching with a Python post-filter.
    is_raw = any(ch in query for ch in ':"*')
    post_words: list[str] = []
    if not query:
        anki_query = ""
    elif is_raw:
        anki_query = query
    else:
        post_words = query.split()
        terms = []
        for word in post_words:
            if match_field == "japanese_tts" or match_field == "japanese":
                terms.append(f'"Japanese:{_wildcard_interleave(word)}"')
            elif match_field == "english":
                terms.append(f'"English:*{word}*"')
            else:  # any — candidate = word anywhere, or split across furigana
                terms.append(f'("{word}" OR "Japanese:{_wildcard_interleave(word)}")')
        anki_query = " ".join(terms)
    if deck:
        deck_term = f'deck:"{deck}"'
        anki_query = f"{anki_query} {deck_term}".strip()

    note_ids = _find_note_ids(anki_query)
    truncated = len(note_ids) > _SEARCH_CANDIDATE_CAP
    note_ids = note_ids[:_SEARCH_CANDIDATE_CAP]

    infos = get_notes_info(note_ids) if note_ids else []
    if not isinstance(infos, list):
        raise RuntimeError(f"Unexpected response from notesInfo: {infos!r}")
    matched = [
        info
        for info in infos
        if isinstance(info, dict) and info.get("noteId") is not None
        and all(_matches_word(info, w, match_field) for w in post_words)
    ]

    truncated = truncated or len(matched) > limit
    matched = matched[:limit]
    cards = _cards_by_note([info["noteId"] for info in matched])
    return {
        "count": len(matched),
        "truncated": truncated,
        "query": anki_query,
        "notes": [
            _note_entry(info, cards.get(info["noteId"]), full_fields=False)
            for info in matched
        ],
    }


def get_note(note_id: int) -> dict[str, Any]:
    infos = get_notes_info([note_id])
    if (
        not isinstance(infos, list)
        or not infos
        or not isinstance(infos[0], dict)
        or infos[0].get("noteId") is None
    ):
        raise RuntimeError(f"Note {note_id} not found.")
    info = infos[0]
    cards = _cards_by_note([note_id])
    return _note_entry(info, cards.get(note_id), full_fields=True)


def delete_notes(note_ids: list[int]) -> dict[str, Any]:
    if not isinstance(note_ids, list) or not note_ids or not all(
        isinstance(x, int) for x in note_ids
    ):
        raise RuntimeError("'note_ids' must be a non-empty list of integers.")
    infos = get_notes_info(note_ids)
    if not isinstance(infos, list):
        raise RuntimeError(f"Unexpected response from notesInfo: {infos!r}")
    found = [
        info["noteId"]
        for info in infos
        if isinstance(info, dict) and info.get("noteId") is not None
    ]
    missing = [nid for nid in note_ids if nid not in found]
    if found:
        anki_invoke("deleteNotes", {"notes": found})
    return {"status": "ok", "deleted": found, "missing": missing}


def _regenerate_audio(display_text: str, tts_text: str, tts_config: TTSConfig) -> str:
    """Generate + store TTS for updated text; returns the [sound:...] field value."""
    resolved = resolve_tts_input(display_text, tts_text)
    if not resolved:
        return ""
    filename = stable_audio_filename(resolved, tts_config.extension)
    local_path = OUTPUT_DIR / filename
    generate_tts_file(tts_config, resolved, local_path)
    stored = store_media_file(local_path, filename)
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
        raise RuntimeError(
            f"Unknown field(s): {', '.join(sorted(unknown))}. "
            f"Updatable fields: {', '.join(sorted(UPDATABLE_FIELDS))}."
        )
    if not fields and tags is None:
        raise RuntimeError("Provide 'fields' and/or 'tags' to update.")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        raise RuntimeError("'tags' must be a list of strings.")

    current = get_note(note_id)  # raises if the note doesn't exist

    anki_fields: dict[str, str] = {}
    japanese = None
    if "japanese" in fields:
        japanese = normalize_furigana_text(fields["japanese"])
        anki_fields["Japanese"] = html.escape(sanitize_text(japanese), quote=False)
    if "english" in fields:
        anki_fields["English"] = html.escape(sanitize_text(fields["english"]), quote=False)
    if "notes" in fields:
        anki_fields["Notes"] = notes_to_html(fields["notes"])
    japanese_prompt = None
    if "japanese_prompt" in fields:
        japanese_prompt = normalize_furigana_text(fields["japanese_prompt"])
        anki_fields["Japanese Prompt"] = html.escape(
            sanitize_text(japanese_prompt), quote=False
        )
    if "english_prompt" in fields:
        anki_fields["English Prompt"] = html.escape(
            sanitize_text(fields["english_prompt"]), quote=False
        )

    # Changing Japanese text (or its TTS override) invalidates the stored audio,
    # so regenerate — including Audio Prompt for prompt changes (dialog cards are
    # prompt-audio-first; stale prompt audio silently breaks the card front).
    regen_main = "japanese" in fields or "japanese_tts" in fields
    regen_prompt = "japanese_prompt" in fields or "japanese_prompt_tts" in fields
    if regen_main or regen_prompt:
        tts_config = resolve_tts_config(tts_provider, tts_model)
    if regen_main:
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
        anki_invoke("updateNoteFields", {"note": {"id": note_id, "fields": anki_fields}})

    if tags is not None:
        old_tags = set(current["tags"])
        new_tags = set(tags)
        to_remove = sorted(old_tags - new_tags)
        to_add = sorted(new_tags - old_tags)
        if to_remove:
            anki_invoke("removeTags", {"notes": [note_id], "tags": " ".join(to_remove)})
        if to_add:
            anki_invoke("addTags", {"notes": [note_id], "tags": " ".join(to_add)})

    result = get_note(note_id)
    result["status"] = "ok"
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
