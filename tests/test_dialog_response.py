"""Acceptance tests for the Japanese Dialog Response note type.

Spec: docs/SPEC-dialog-response.md — single-card 場面 response note
(audio-only prompt on the front; prompt text + response on the back).

LOOP CONTRACT: this file is the success criteria for the dialog-response
development loop. The implementing agent MUST NOT edit this file; the loop
is done when `uv run pytest` is green without changes here (verify with
`git diff tests/test_dialog_response.py`). Written TDD-first — every test
below is expected to FAIL until the feature exists.
"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import flashgen
from flashgen_mcp.app import app
from flashgen_mcp.schema import CardRequest

client = TestClient(app)

DIALOG_MODEL = "Japanese Dialog Response"
EXPECTED_FIELDS = [
    "Japanese",
    "English",
    "Notes",
    "Audio",
    "Japanese Prompt",
    "English Prompt",
    "Audio Prompt",
]

DIALOG_PAYLOAD = {
    "card_type": "dialog_response",
    "japanese": "お先[さき]に失礼[しつれい]します。",
    "english": "Excuse me for leaving ahead of you.",
    "japanese_prompt": "今日[きょう]はもう帰[かえ]りますか？",
    "english_prompt": "Are you heading home already?",
    "notes": "Standard phrase when leaving work before colleagues.",
}


# ---------------------------------------------------------------------------
# Mocked-Anki harness for engine tests
# ---------------------------------------------------------------------------

def _fake_anki(calls, model_names):
    """Dispatcher standing in for flashgen.anki_invoke; records every call."""

    def invoke(action, params=None, *args, **kwargs):
        calls.append((action, params))
        responses = {
            "version": 6,
            "deckNames": [flashgen.DECK_NAME],
            "modelNames": list(model_names),
            "modelFieldNames": list(EXPECTED_FIELDS),
            "createModel": {"id": 1},
            "canAddNotes": [True],
            "addNote": 1234567890,
        }
        if action not in responses:
            raise AssertionError(f"unexpected AnkiConnect action: {action!r}")
        return responses[action]

    return invoke


def _run_engine_create(calls, model_names, **overrides):
    """Run create_flashcard with Anki + TTS mocked out; return the result."""
    kwargs = dict(
        card_type="dialog_response",
        japanese="お先に失礼します。",
        english="Excuse me for leaving ahead of you.",
        japanese_prompt="今日はもう帰りますか？",
        english_prompt="Are you heading home already?",
    )
    kwargs.update(overrides)
    with patch("flashgen.anki_invoke", side_effect=_fake_anki(calls, model_names)), \
         patch("flashgen.generate_tts_file"), \
         patch("flashgen.store_media_file", side_effect=lambda path, name: name):
        return flashgen.create_flashcard(**kwargs)


def _calls_for(calls, action):
    return [params for (name, params) in calls if name == action]


# ---------------------------------------------------------------------------
# 1. Request schema: card_type
# ---------------------------------------------------------------------------

class TestCardTypeSchema:
    def test_card_type_defaults_to_standard(self):
        req = CardRequest(japanese="写真を撮りました。")
        assert req.card_type == "standard"

    def test_dialog_response_accepted_with_prompt(self):
        req = CardRequest(**DIALOG_PAYLOAD)
        assert req.card_type == "dialog_response"

    def test_unknown_card_type_rejected(self):
        with pytest.raises(ValidationError):
            CardRequest(japanese="日本語", card_type="triple_deluxe")

    def test_dialog_response_requires_japanese_prompt(self):
        payload = {k: v for k, v in DIALOG_PAYLOAD.items() if k != "japanese_prompt"}
        with pytest.raises(ValidationError, match="japanese_prompt"):
            CardRequest(**payload)

    def test_standard_still_fine_without_prompt(self):
        req = CardRequest(japanese="写真を撮りました。", english="I took a photo.")
        assert req.card_type == "standard"
        assert req.japanese_prompt == ""


# ---------------------------------------------------------------------------
# 2. Model definition: created via AnkiConnect createModel, exactly one card
# ---------------------------------------------------------------------------

class TestDialogModelDefinition:
    def test_dialog_model_name_constant(self):
        assert flashgen.DIALOG_MODEL_NAME == DIALOG_MODEL

    def test_missing_model_is_created_with_exactly_one_template(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        creates = _calls_for(calls, "createModel")
        assert len(creates) == 1, "expected createModel when dialog model is absent"
        params = creates[0]
        assert params["modelName"] == DIALOG_MODEL
        assert params["inOrderFields"] == EXPECTED_FIELDS
        templates = params["cardTemplates"]
        assert len(templates) == 1, (
            f"dialog note type must have exactly ONE card template, got {len(templates)}"
        )

    def test_front_is_audio_only_no_prompt_or_answer_text(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        template = _calls_for(calls, "createModel")[0]["cardTemplates"][0]
        front = template["Front"]
        assert "{{Audio Prompt}}" in front, "front must play the prompt audio"
        for leaked in (
            "{{Japanese Prompt}}",
            "{{furigana:Japanese Prompt}}",
            "{{English Prompt}}",
            "{{Japanese}}",
            "{{furigana:Japanese}}",
            "{{English}}",
        ):
            assert leaked not in front, f"front must not reveal text: {leaked}"

    def test_back_reveals_prompt_text_response_and_response_audio(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        template = _calls_for(calls, "createModel")[0]["cardTemplates"][0]
        back = template["Back"]
        assert "{{furigana:Japanese Prompt}}" in back, "back must show what was said (self-check)"
        assert "{{furigana:Japanese}}" in back, "back must show the response"
        assert "{{Audio}}" in back, "back must play the response audio"

    def test_existing_model_is_not_recreated(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME, DIALOG_MODEL])
        assert not _calls_for(calls, "createModel"), (
            "createModel must not run when the dialog model already exists"
        )


# ---------------------------------------------------------------------------
# 3. Engine: create_flashcard(card_type="dialog_response")
# ---------------------------------------------------------------------------

class TestEngineDialogCreate:
    def test_note_added_with_dialog_model_and_prompt_fields(self):
        calls = []
        result = _run_engine_create(
            calls, model_names=[flashgen.MODEL_NAME, DIALOG_MODEL]
        )

        adds = _calls_for(calls, "addNote")
        assert len(adds) == 1
        note = adds[0]["note"]
        assert note["modelName"] == DIALOG_MODEL
        fields = note["fields"]
        assert fields["Japanese Prompt"].strip()
        assert fields["Audio Prompt"].startswith("[sound:")
        assert fields["Audio"].startswith("[sound:")

        assert result["status"] == "ok"
        assert result["model"] == DIALOG_MODEL
        assert result["card_type"] == "dialog_response"

    def test_dialog_without_japanese_prompt_raises(self):
        calls = []
        with pytest.raises(Exception, match="japanese_prompt"):
            _run_engine_create(
                calls,
                model_names=[flashgen.MODEL_NAME, DIALOG_MODEL],
                japanese_prompt="",
                english_prompt="",
            )

    def test_standard_card_type_keeps_legacy_model(self):
        calls = []
        result = _run_engine_create(
            calls,
            model_names=[flashgen.MODEL_NAME, DIALOG_MODEL],
            card_type="standard",
        )
        note = _calls_for(calls, "addNote")[0]["note"]
        assert note["modelName"] == flashgen.MODEL_NAME
        assert result["model"] == flashgen.MODEL_NAME


# ---------------------------------------------------------------------------
# 4. MCP server surface
# ---------------------------------------------------------------------------

class TestMcpDialogSurface:
    def test_tool_input_schema_exposes_card_type_enum(self):
        from flashgen_mcp.app import _CARD_INPUT_SCHEMA

        prop = _CARD_INPUT_SCHEMA["properties"]["card_type"]
        assert set(prop["enum"]) == {"standard", "dialog_response"}

    def test_validate_accepts_dialog_payload(self):
        response = client.post("/validate", json=DIALOG_PAYLOAD)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["card_type"] == "dialog_response"
        assert body["japanese_prompt"].strip()

    def test_validate_rejects_dialog_without_prompt(self):
        payload = {k: v for k, v in DIALOG_PAYLOAD.items() if k != "japanese_prompt"}
        response = client.post("/validate", json=payload)
        assert response.status_code in (400, 422)

    def test_create_forwards_card_type_to_engine(self):
        fake_result = {
            "status": "ok",
            "note_id": 42,
            "deck": flashgen.DECK_NAME,
            "model": DIALOG_MODEL,
            "card_type": "dialog_response",
            "japanese": DIALOG_PAYLOAD["japanese"],
            "english": DIALOG_PAYLOAD["english"],
            "notes": DIALOG_PAYLOAD["notes"],
            "tags": ["jp"],
            "tts_provider": "gemini",
            "tts_model": "gemini-3.1-flash-tts-preview",
            "japanese_tts": "お先に失礼します。",
            "audio_file": "x.wav",
            "local_audio_path": "/tmp/x.wav",
            "japanese_prompt": DIALOG_PAYLOAD["japanese_prompt"],
            "english_prompt": DIALOG_PAYLOAD["english_prompt"],
            "japanese_prompt_tts": "今日はもう帰りますか？",
            "audio_prompt_file": "y.wav",
        }
        with patch("flashgen.create_flashcard", return_value=fake_result) as mock_create:
            response = client.post("/create", json=DIALOG_PAYLOAD)

        assert response.status_code == 200
        assert mock_create.call_args.kwargs["card_type"] == "dialog_response"
        assert response.json()["model"] == DIALOG_MODEL


# ---------------------------------------------------------------------------
# 5. Integration: exactly ONE card materializes in Anki
#
# Gated like tests/test_anki_runtime.py — requires a live AnkiConnect and a
# real TTS key. Run inside the docker-compose environment or against a local
# Anki. Notes are created in a dedicated test deck and deleted afterwards.
# ---------------------------------------------------------------------------

_ANKI_CONNECT_URL = os.environ.get("ANKI_CONNECT_URL", "")
_HAS_TTS_KEY = bool(
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GEMINI_API_KEY_FILE")
    or os.environ.get("OPENAI_API_KEY")
    or os.environ.get("OPENAI_API_KEY_FILE")
)
_TEST_DECK = "FlashGen-DialogResponse-Test"

_needs_live_anki = pytest.mark.skipif(
    not (_ANKI_CONNECT_URL and _HAS_TTS_KEY),
    reason="requires ANKI_CONNECT_URL and a TTS API key",
)


@_needs_live_anki
class TestDialogCardCountLive:
    @pytest.fixture()
    def test_deck(self):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            flashgen.anki_invoke("createDeck", {"deck": _TEST_DECK})
            created_notes = []
            yield created_notes
            if created_notes:
                flashgen.anki_invoke("deleteNotes", {"notes": created_notes})

    def test_dialog_note_yields_exactly_one_card(self, test_deck):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            result = flashgen.create_flashcard(
                card_type="dialog_response",
                deck_name=_TEST_DECK,
                japanese="お先に失礼します。",
                english="Excuse me for leaving ahead of you.",
                japanese_prompt="今日はもう帰りますか？",
                english_prompt="Are you heading home already?",
                tags=["jp", "test", "dialog-response-acceptance"],
            )
            test_deck.append(result["note_id"])

            card_ids = flashgen.anki_invoke(
                "findCards", {"query": f"nid:{result['note_id']}"}
            )
            assert len(card_ids) == 1, (
                f"dialog note must produce exactly 1 card, got {len(card_ids)}"
            )

            (info,) = flashgen.anki_invoke(
                "notesInfo", {"notes": [result["note_id"]]}
            )
            assert info["modelName"] == DIALOG_MODEL

    def test_standard_note_with_prompt_still_yields_three_cards(self, test_deck):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            result = flashgen.create_flashcard(
                deck_name=_TEST_DECK,
                japanese="お疲れ様でした。",
                english="Thank you for your hard work.",
                japanese_prompt="今日の会議、大変でしたね。",
                english_prompt="That meeting today was rough, wasn't it?",
                tags=["jp", "test", "dialog-response-acceptance"],
            )
            test_deck.append(result["note_id"])

            card_ids = flashgen.anki_invoke(
                "findCards", {"query": f"nid:{result['note_id']}"}
            )
            assert len(card_ids) == 3, (
                f"legacy note with prompt must still produce 3 cards, got {len(card_ids)}"
            )
