"""AnkiConnect: the HTTP client, note-type creation, note add/read/delete,
and the collection search surface.
"""
from __future__ import annotations

import base64
import html
import json
import re
from typing import Any

import requests

from flashgen_core.config import (
    ANKI_CONNECT_URL,
    GENERATED_MODEL_CSS,
    GENERATED_MODEL_FIELDS,
    GENERATED_MODELS,
    debug_print,
)
from flashgen_core.furigana import (
    _BR_TAG_RE,
    _NEWLINE_RUN_RE,
    _despace,
    normalize_line_breaks,
    rich_text_to_html,
    strip_emphasis_markup,
    strip_furigana_markup,
)

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

    model_name=None skips the note-type check — used by the generated-model
    paths, where ensure_generated_model() has already verified or created it.
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


def ensure_generated_model(card_type: str) -> str:
    """Create a FlashGen-owned note type via createModel if absent; return its
    model name.

    Templates live in code (single source of truth); the model syncs to
    AnkiWeb / the headless container like any other collection change. The
    legacy MODEL_NAME stays manually managed.
    """
    spec = GENERATED_MODELS[card_type]
    model_name = spec["model_name"]

    model_names = anki_invoke("modelNames")
    if not isinstance(model_names, list):
        raise RuntimeError(f"Unexpected modelNames response: {model_names!r}")
    if model_name in model_names:
        return model_name

    anki_invoke(
        "createModel",
        {
            "modelName": model_name,
            "inOrderFields": list(GENERATED_MODEL_FIELDS),
            "css": GENERATED_MODEL_CSS,
            "isCloze": False,
            "cardTemplates": [
                {
                    "Name": "Response",
                    "Front": spec["front"],
                    "Back": spec["back"],
                }
            ],
        },
    )
    return model_name


def ensure_dialog_model() -> str:
    return ensure_generated_model("dialog_response")


def ensure_cued_model() -> str:
    return ensure_generated_model("cued_response")


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
    # Fields may contain markup; an Anki search string must stay one line and
    # the stored-field match is saner without emphasis tags.
    japanese = strip_emphasis_markup(japanese)
    japanese = _NEWLINE_RUN_RE.sub(" ", normalize_line_breaks(japanese))
    query = f'note:"{model_name}" "Japanese:{japanese}"'
    result = anki_invoke("findNotes", {"query": query})
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected response from findNotes: {result!r}")
    if not all(isinstance(x, int) for x in result):
        raise RuntimeError(f"findNotes returned non-int note ids: {result!r}")
    return result


def get_notes_info(note_ids: list[int]) -> object:
    return anki_invoke("notesInfo", {"notes": note_ids})


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
        "Japanese": rich_text_to_html(japanese),
        "English": rich_text_to_html(english),
        "Notes": rich_text_to_html(notes),
        "Audio": f"[sound:{audio_filename}]",
    }
    if japanese_prompt:
        fields["Japanese Prompt"] = rich_text_to_html(japanese_prompt)
        fields["English Prompt"] = rich_text_to_html(english_prompt)
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
    # Stored fields may contain real <br>/emphasis tags: treat <br> as spacing
    # and drop emphasis so a query word can match across a tag boundary.
    # Substitute BEFORE unescaping so user-visible "&lt;br&gt;" text is not
    # mistaken for markup.
    return html.unescape(strip_emphasis_markup(_BR_TAG_RE.sub(" ", value)))


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
