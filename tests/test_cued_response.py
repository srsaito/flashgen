"""Acceptance tests for the Japanese Cued Response note type.

Spec: docs/SPEC-cued-response.md — a one-card note type whose front shows two
free-form English cues and plays the prompt audio, and whose back shows the
Japanese prompt and the answer. GitHub issue #6; beads flashgen-7mj.

This is the Response card of `Japanese Listening+Production` on its own, so a
prompt->response note no longer drags in the Listening and Production cards.

LOOP CONTRACT: this file is the success criteria for the cued-response work.
The implementing agent MUST NOT edit this file; the work is done when
`uv run pytest` is green without changes here (verify with
`git diff tests/test_cued_response.py`). Written TDD-first — every test below
is expected to FAIL until the feature exists.
"""
import html
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import flashgen
from flashgen_mcp.app import app
from flashgen_mcp.schema import CardRequest

client = TestClient(app)

CUED_MODEL = "Japanese Cued Response"
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

# Motivating case 1 from the spec: one prompt, three cued answers. Both English
# fields are cues, not translations — english_prompt is an instruction here.
CUED_PAYLOAD = {
    "card_type": "cued_response",
    "japanese": (
        "1. 大丈夫[だいじょうぶ]です。\n"
        "2. なんとか 大丈夫[だいじょうぶ]です。\n"
        "3. なんとかなりました。"
    ),
    "english": (
        "1. Fine, no problem.\n"
        "2. Getting by, with some strain.\n"
        "3. It worked out in the end."
    ),
    "japanese_prompt": " 台風[たいふう]すごかったね。 大丈夫[だいじょうぶ]？",
    "english_prompt": (
        "That typhoon was something. Are you OK? "
        "(Answer in each of the three situations below.)"
    ),
    "notes": "なんとか: somehow, just barely.",
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


def _engine_kwargs(**overrides):
    kwargs = dict(
        card_type="cued_response",
        japanese=CUED_PAYLOAD["japanese"],
        english=CUED_PAYLOAD["english"],
        notes=CUED_PAYLOAD["notes"],
        japanese_prompt=CUED_PAYLOAD["japanese_prompt"],
        english_prompt=CUED_PAYLOAD["english_prompt"],
    )
    kwargs.update(overrides)
    return kwargs


def _run_engine_create(calls, model_names, tts_texts=None, **overrides):
    """Run create_flashcard with Anki + TTS mocked out; return the result.

    tts_texts, when given a list, collects every string handed to TTS.
    """

    def fake_tts(config, text, out_path):
        if tts_texts is not None:
            tts_texts.append(text)

    with patch("flashgen.anki_invoke", side_effect=_fake_anki(calls, model_names)), \
         patch("flashgen.generate_tts_file", side_effect=fake_tts), \
         patch("flashgen.store_media_file", side_effect=lambda path, name: name):
        return flashgen.create_flashcard(**_engine_kwargs(**overrides))


def _calls_for(calls, action):
    return [params for (name, params) in calls if name == action]


def _created_template(calls):
    return _calls_for(calls, "createModel")[0]["cardTemplates"][0]


# ---------------------------------------------------------------------------
# 1. Request schema: the new card_type value
# ---------------------------------------------------------------------------

class TestCuedCardTypeSchema:
    def test_cued_response_accepted_with_prompt(self):
        req = CardRequest(**CUED_PAYLOAD)
        assert req.card_type == "cued_response"

    def test_cued_response_requires_japanese_prompt(self):
        payload = {k: v for k, v in CUED_PAYLOAD.items() if k != "japanese_prompt"}
        with pytest.raises(ValidationError, match="japanese_prompt"):
            CardRequest(**payload)

    def test_blank_japanese_prompt_also_rejected(self):
        payload = dict(CUED_PAYLOAD, japanese_prompt="   ")
        with pytest.raises(ValidationError, match="japanese_prompt"):
            CardRequest(**payload)

    def test_unknown_card_type_still_rejected(self):
        with pytest.raises(ValidationError):
            CardRequest(japanese="日本語", card_type="prompt_response")

    def test_existing_card_types_unaffected(self):
        assert CardRequest(japanese="写真を撮りました。").card_type == "standard"
        assert (
            CardRequest(
                card_type="dialog_response",
                japanese="お先に失礼します。",
                japanese_prompt="もう帰りますか？",
                english_prompt="Heading home already?",
            ).card_type
            == "dialog_response"
        )


# ---------------------------------------------------------------------------
# 2. Model definition: created via createModel, exactly one card
# ---------------------------------------------------------------------------

class TestCuedModelDefinition:
    def test_cued_model_name_constant(self):
        assert flashgen.CUED_MODEL_NAME == CUED_MODEL

    def test_cued_response_is_a_known_card_type(self):
        assert "cued_response" in flashgen.CARD_TYPES

    def test_missing_model_is_created_with_exactly_one_template(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        creates = _calls_for(calls, "createModel")
        assert len(creates) == 1, "expected createModel when the cued model is absent"
        params = creates[0]
        assert params["modelName"] == CUED_MODEL
        assert params["inOrderFields"] == EXPECTED_FIELDS, (
            "fields must match the other models exactly, in order, so the "
            "collection read/write paths need no special cases"
        )
        assert params["isCloze"] is False
        templates = params["cardTemplates"]
        assert len(templates) == 1, (
            f"cued note type must have exactly ONE card template, got {len(templates)}"
        )

    def test_front_shows_both_english_cues_and_plays_prompt_audio(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        front = _created_template(calls)["Front"]
        assert "{{Audio Prompt}}" in front, "front must play the prompt audio"
        assert "{{English Prompt}}" in front, "front must show the prompt cue"
        assert "{{English}}" in front, "front must show the answer cue"

    def test_front_shows_no_japanese_text(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        front = _created_template(calls)["Front"]
        for leaked in (
            "{{Japanese Prompt}}",
            "{{furigana:Japanese Prompt}}",
            "{{Japanese}}",
            "{{furigana:Japanese}}",
            "{{Notes}}",
            "{{furigana:Notes}}",
        ):
            assert leaked not in front, f"front must not reveal Japanese text: {leaked}"

    def test_back_reveals_prompt_text_answer_and_answer_audio(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        back = _created_template(calls)["Back"]
        assert "{{FrontSide}}" in back, "back must carry the front's cues"
        assert "{{furigana:Japanese Prompt}}" in back, "back must show the prompt text"
        assert "{{furigana:Japanese}}" in back, "back must show the answer"
        assert "{{Audio}}" in back, "back must play the answer audio"
        assert "{{furigana:Notes}}" in back, "back must show notes when present"

    def test_back_shows_japanese_prompt_before_the_answer(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME])

        back = _created_template(calls)["Back"]
        assert back.index("{{furigana:Japanese Prompt}}") < back.index(
            "{{furigana:Japanese}}"
        ), "the prompt is revealed first, then the answer"

    def test_existing_model_is_not_recreated(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME, CUED_MODEL])
        assert not _calls_for(calls, "createModel"), (
            "createModel must not run when the cued model already exists"
        )

    def test_dialog_model_is_left_alone(self):
        """The dialog type keeps its audio-only front; many notes rely on it."""
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME, DIALOG_MODEL])

        creates = _calls_for(calls, "createModel")
        assert [p["modelName"] for p in creates] == [CUED_MODEL], (
            "creating a cued note must not touch the dialog model"
        )
        dialog_front = flashgen.DIALOG_CARD_FRONT
        assert "{{English Prompt}}" not in dialog_front
        assert "{{English}}" not in dialog_front

    def test_cued_and_dialog_templates_are_distinct(self):
        assert flashgen.CUED_MODEL_NAME != flashgen.DIALOG_MODEL_NAME
        assert flashgen.CUED_CARD_FRONT != flashgen.DIALOG_CARD_FRONT


# ---------------------------------------------------------------------------
# 3. Engine: create_flashcard(card_type="cued_response")
# ---------------------------------------------------------------------------

class TestEngineCuedCreate:
    def test_note_added_with_cued_model_and_all_fields(self):
        calls = []
        result = _run_engine_create(
            calls, model_names=[flashgen.MODEL_NAME, CUED_MODEL]
        )

        adds = _calls_for(calls, "addNote")
        assert len(adds) == 1
        note = adds[0]["note"]
        assert note["modelName"] == CUED_MODEL
        fields = note["fields"]
        assert fields["Japanese Prompt"].strip()
        assert fields["English Prompt"].strip()
        assert fields["Japanese"].strip()
        assert fields["English"].strip()
        assert fields["Notes"].strip()
        assert fields["Audio"].startswith("[sound:")
        assert fields["Audio Prompt"].startswith("[sound:")

        assert result["status"] == "ok"
        assert result["model"] == CUED_MODEL
        assert result["card_type"] == "cued_response"
        assert result["audio_prompt_file"]

    def test_cued_without_japanese_prompt_raises(self):
        calls = []
        with pytest.raises(Exception, match="japanese_prompt"):
            _run_engine_create(
                calls,
                model_names=[flashgen.MODEL_NAME, CUED_MODEL],
                japanese_prompt="",
                english_prompt="",
            )
        assert not _calls_for(calls, "addNote"), "no note on a rejected request"

    def test_standard_card_type_keeps_legacy_model(self):
        calls = []
        result = _run_engine_create(
            calls,
            model_names=[flashgen.MODEL_NAME, CUED_MODEL],
            card_type="standard",
        )
        note = _calls_for(calls, "addNote")[0]["note"]
        assert note["modelName"] == flashgen.MODEL_NAME
        assert result["model"] == flashgen.MODEL_NAME

    def test_dialog_card_type_keeps_dialog_model(self):
        calls = []
        result = _run_engine_create(
            calls,
            model_names=[flashgen.MODEL_NAME, DIALOG_MODEL, CUED_MODEL],
            card_type="dialog_response",
        )
        note = _calls_for(calls, "addNote")[0]["note"]
        assert note["modelName"] == DIALOG_MODEL
        assert result["model"] == DIALOG_MODEL

    def test_unknown_card_type_rejected_by_engine(self):
        calls = []
        with pytest.raises(Exception, match="card_type"):
            _run_engine_create(
                calls,
                model_names=[flashgen.MODEL_NAME, CUED_MODEL],
                card_type="prompt_response",
            )

    def test_missing_deck_error_lists_available_decks(self):
        calls = []
        with pytest.raises(Exception, match="Available decks"):
            _run_engine_create(
                calls,
                model_names=[flashgen.MODEL_NAME, CUED_MODEL],
                deck_name="No Such Deck",
            )

    def test_failed_tts_leaves_no_half_created_note(self):
        """A TTS 429 must not produce a note with a missing audio field."""
        calls = []
        with patch(
            "flashgen.anki_invoke",
            side_effect=_fake_anki(calls, [flashgen.MODEL_NAME, CUED_MODEL]),
        ), patch(
            "flashgen.generate_tts_file",
            side_effect=RuntimeError("429 rate limit exceeded"),
        ), patch("flashgen.store_media_file", side_effect=lambda path, name: name):
            with pytest.raises(RuntimeError, match="429"):
                flashgen.create_flashcard(**_engine_kwargs())

        assert not _calls_for(calls, "addNote"), (
            "a failed TTS call must not leave a note behind"
        )

    def test_line_breaks_in_cue_fields_render_as_br(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME, CUED_MODEL])

        fields = _calls_for(calls, "addNote")[0]["note"]["fields"]
        assert "<br>" in fields["English"], "the numbered answer cues need breaks"
        assert "<br>" in fields["Japanese"], "the numbered answers need breaks"


# ---------------------------------------------------------------------------
# 4. Validation: every check the existing types get, this type gets too
# ---------------------------------------------------------------------------

class TestCuedValidation:
    def test_furigana_normalized_in_all_three_rendered_fields(self):
        """japanese, japanese_prompt and notes are all rendered through
        {{furigana:...}}, so all three need the leading-space normalization."""
        payload = dict(
            CUED_PAYLOAD,
            japanese="これは写真[しゃしん]です。",
            japanese_prompt="（写真[しゃしん]ですか？",
            notes="1.写真[しゃしん]: photo",
        )
        response = client.post("/validate", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["japanese"] == "これは 写真[しゃしん]です。"
        assert body["japanese_prompt"] == "（ 写真[しゃしん]ですか？"
        assert body["notes"] == "1. 写真[しゃしん]: photo"
        assert body["card_type"] == "cued_response"

    def test_stray_bracket_in_notes_is_an_error(self):
        payload = dict(CUED_PAYLOAD, notes="なんとか[なんとか]: somehow")
        response = client.post("/validate", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "invalid"
        assert "notes" in body["markup_errors"]
        assert any("ruby" in msg for msg in body["markup_errors"]["notes"])

    def test_stray_bracket_in_japanese_prompt_is_an_error(self):
        payload = dict(CUED_PAYLOAD, japanese_prompt="OK[オーケー]ですか？")
        response = client.post("/validate", json=payload)
        body = response.json()
        assert body["status"] == "invalid"
        assert "japanese_prompt" in body["markup_errors"]

    def test_emphasis_splitting_a_furigana_unit_is_an_error(self):
        payload = dict(CUED_PAYLOAD, japanese=" 大<b>丈夫[だいじょうぶ]</b>です。")
        response = client.post("/validate", json=payload)
        body = response.json()
        assert body["status"] == "invalid"
        assert any(
            "annotated unit" in msg for msg in body["markup_errors"]["japanese"]
        )

    def test_unbalanced_emphasis_in_an_english_cue_is_an_error(self):
        payload = dict(CUED_PAYLOAD, english_prompt="<b>Answer three ways.")
        response = client.post("/validate", json=payload)
        body = response.json()
        assert body["status"] == "invalid"
        assert "english_prompt" in body["markup_errors"]

    def test_non_allowlisted_markup_is_a_warning_not_an_error(self):
        payload = dict(CUED_PAYLOAD, english="<span>Fine, no problem.</span>")
        response = client.post("/validate", json=payload)
        body = response.json()
        assert body["status"] == "ok"
        assert "english" in body["markup_warnings"]

    def test_whole_unit_emphasis_is_accepted(self):
        payload = dict(CUED_PAYLOAD, japanese="<b> 大丈夫[だいじょうぶ]</b>です。")
        response = client.post("/validate", json=payload)
        body = response.json()
        assert body["status"] == "ok"

    def test_engine_rejects_unit_splitting_emphasis_before_touching_anki(self):
        calls = []
        with pytest.raises(Exception, match="annotated unit"):
            _run_engine_create(
                calls,
                model_names=[flashgen.MODEL_NAME, CUED_MODEL],
                japanese=" 大<b>丈夫[だいじょうぶ]</b>です。",
            )
        assert not _calls_for(calls, "addNote")

    def test_tts_receives_no_furigana_markup_emphasis_or_breaks(self):
        tts_texts = []
        _run_engine_create(
            [],
            model_names=[flashgen.MODEL_NAME, CUED_MODEL],
            tts_texts=tts_texts,
            japanese="<b> 大丈夫[だいじょうぶ]</b>です。\n 次[つぎ]。",
        )
        assert tts_texts, "TTS must be generated for both the answer and the prompt"
        for text in tts_texts:
            assert "[" not in text and "]" not in text, f"furigana reached TTS: {text!r}"
            assert "<" not in text and ">" not in text, f"markup reached TTS: {text!r}"
            assert "\n" not in text, f"line break reached TTS: {text!r}"

    def test_neither_english_field_ever_reaches_tts(self):
        """english_prompt may be an instruction, english a list of cues —
        both are display-only and must never be spoken."""
        tts_texts = []
        _run_engine_create(
            [],
            model_names=[flashgen.MODEL_NAME, CUED_MODEL],
            tts_texts=tts_texts,
        )
        joined = " ".join(tts_texts)
        for leaked in ("typhoon", "Fine, no problem", "Answer in each"):
            assert leaked not in joined, f"English cue reached TTS: {leaked!r}"

    def test_tts_overrides_are_used_verbatim(self):
        tts_texts = []
        _run_engine_create(
            [],
            model_names=[flashgen.MODEL_NAME, CUED_MODEL],
            tts_texts=tts_texts,
            japanese_tts="だいじょうぶです",
            japanese_prompt_tts="たいふうすごかったね",
        )
        assert "だいじょうぶです" in tts_texts
        assert "たいふうすごかったね" in tts_texts

    def test_prompt_fields_must_be_given_together(self):
        """japanese_prompt/english_prompt are both-or-neither, and for this
        card_type both are required — the front shows the English one."""
        payload = {k: v for k, v in CUED_PAYLOAD.items() if k != "japanese_prompt"}
        response = client.post("/validate", json=payload)
        assert response.status_code in (400, 422)

    def test_default_tags_include_auto(self):
        calls = []
        _run_engine_create(calls, model_names=[flashgen.MODEL_NAME, CUED_MODEL])
        tags = _calls_for(calls, "addNote")[0]["note"]["tags"]
        assert "auto" in tags

    def test_caller_tags_are_passed_through(self):
        calls = []
        _run_engine_create(
            calls,
            model_names=[flashgen.MODEL_NAME, CUED_MODEL],
            tags=["jp", "auto", "cued-response"],
        )
        tags = _calls_for(calls, "addNote")[0]["note"]["tags"]
        assert tags == ["jp", "auto", "cued-response"]


# ---------------------------------------------------------------------------
# 5. MCP server surface
# ---------------------------------------------------------------------------

class TestMcpCuedSurface:
    def test_tool_input_schema_exposes_the_new_enum_value(self):
        from flashgen_mcp.app import _CARD_INPUT_SCHEMA

        prop = _CARD_INPUT_SCHEMA["properties"]["card_type"]
        assert set(prop["enum"]) == {"standard", "dialog_response", "cued_response"}

    def test_card_type_description_distinguishes_cued_from_prompt_response(self):
        from flashgen_mcp.app import _CARD_INPUT_SCHEMA

        description = _CARD_INPUT_SCHEMA["properties"]["card_type"]["description"]
        assert "cued_response" in description
        assert "prompt_response" not in description, (
            "'prompt-response' names the 3-card standard scenario; the new "
            "card_type must not be called that"
        )

    def test_server_instructions_no_longer_say_three_scenarios(self):
        from flashgen_mcp.app import _SERVER_INSTRUCTIONS

        assert "three card scenarios" not in _SERVER_INSTRUCTIONS

    def test_validate_accepts_cued_payload(self):
        response = client.post("/validate", json=CUED_PAYLOAD)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["card_type"] == "cued_response"
        assert body["japanese_prompt"].strip()
        assert body["english_prompt"].strip()

    def test_validate_rejects_cued_without_prompt(self):
        payload = {k: v for k, v in CUED_PAYLOAD.items() if k != "japanese_prompt"}
        response = client.post("/validate", json=payload)
        assert response.status_code in (400, 422)

    def test_create_forwards_card_type_to_engine(self):
        fake_result = {
            "status": "ok",
            "note_id": 42,
            "deck": flashgen.DECK_NAME,
            "model": CUED_MODEL,
            "card_type": "cued_response",
            "japanese": CUED_PAYLOAD["japanese"],
            "english": CUED_PAYLOAD["english"],
            "notes": CUED_PAYLOAD["notes"],
            "tags": ["jp", "auto"],
            "tts_provider": "gemini",
            "tts_model": "gemini-3.1-flash-tts-preview",
            "japanese_tts": "大丈夫です。",
            "audio_file": "x.wav",
            "local_audio_path": "/tmp/x.wav",
            "japanese_prompt": CUED_PAYLOAD["japanese_prompt"],
            "english_prompt": CUED_PAYLOAD["english_prompt"],
            "japanese_prompt_tts": "台風すごかったね。大丈夫？",
            "audio_prompt_file": "y.wav",
        }
        with patch("flashgen.create_flashcard", return_value=fake_result) as mock_create:
            response = client.post("/create", json=CUED_PAYLOAD)

        assert response.status_code == 200
        assert mock_create.call_args.kwargs["card_type"] == "cued_response"
        assert response.json()["model"] == CUED_MODEL

    def test_mcp_tools_list_carries_the_new_enum_value(self):
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        create = next(t for t in tools if t["name"] == "create_flashcard")
        enum = create["inputSchema"]["properties"]["card_type"]["enum"]
        assert "cued_response" in enum


# ---------------------------------------------------------------------------
# 6. Collection read/write: the shared paths need no special cases
# ---------------------------------------------------------------------------

def _cued_note_info(note_id, japanese, english="", notes="", prompt=""):
    """notesInfo payload for a cued note — all seven fields, like Anki sends."""
    values = {
        "Japanese": japanese,
        "English": english,
        "Notes": notes,
        "Audio": "[sound:answer.wav]",
        "Japanese Prompt": prompt,
        "English Prompt": "Are you OK?",
        "Audio Prompt": "[sound:prompt.wav]",
    }
    return {
        "noteId": note_id,
        "modelName": CUED_MODEL,
        "tags": ["jp", "auto"],
        "fields": {
            name: {"value": value, "order": order}
            for order, (name, value) in enumerate(values.items())
        },
    }


class FakeAnki:
    """Routes anki_invoke calls by action and records them."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, action, params=None):
        params = params or {}
        self.calls.append((action, params))
        handler = self.responses[action]
        return handler(params) if callable(handler) else handler

    def params_of(self, action):
        return [p for a, p in self.calls if a == action]


class TestCuedCollectionPaths:
    def test_search_finds_cued_notes_by_furigana_stripped_match(self):
        info = _cued_note_info(
            100,
            japanese=" 大丈夫[だいじょうぶ]です。",
            english="Fine, no problem.",
            prompt=" 台風[たいふう]すごかったね。",
        )
        fake = FakeAnki(
            {
                "findNotes": [100],
                "notesInfo": [info],
                "findCards": [101],
                "cardsInfo": [{"cardId": 101, "note": 100, "deckName": flashgen.DECK_NAME}],
            }
        )
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="大丈夫")

        assert result["count"] == 1
        entry = result["notes"][0]
        assert entry["model"] == CUED_MODEL
        assert entry["card_count"] == 1, "a cued note has exactly one card"
        assert entry["fields"]["English Prompt"] == "Are you OK?"

    def test_get_note_returns_all_seven_fields(self):
        info = _cued_note_info(100, japanese=" 大丈夫[だいじょうぶ]です。")
        fake = FakeAnki(
            {
                "notesInfo": [info],
                "findCards": [101],
                "cardsInfo": [{"cardId": 101, "note": 100, "deckName": flashgen.DECK_NAME}],
            }
        )
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.get_note(100)

        assert set(result["fields"]) == set(EXPECTED_FIELDS)
        assert result["model"] == CUED_MODEL

    def test_update_regenerates_both_audio_fields_on_text_change(self):
        info = _cued_note_info(
            100,
            japanese=" 大丈夫[だいじょうぶ]です。",
            prompt=" 台風[たいふう]すごかったね。",
        )
        fake = FakeAnki(
            {
                "notesInfo": [info],
                "findCards": [101],
                "cardsInfo": [{"cardId": 101, "note": 100, "deckName": flashgen.DECK_NAME}],
                "updateNoteFields": None,
            }
        )
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file"), \
             patch("flashgen.store_media_file", side_effect=lambda path, name: name):
            flashgen.update_note(
                100,
                fields={
                    "japanese": "なんとかなりました。",
                    "japanese_prompt": " 地震[じしん]すごかったね。",
                },
            )

        updated = fake.params_of("updateNoteFields")[0]["note"]["fields"]
        assert updated["Audio"].startswith("[sound:")
        assert updated["Audio Prompt"].startswith("[sound:")
        assert updated["Japanese"] == "なんとかなりました。"
        assert "地震[じしん]" in html.unescape(updated["Japanese Prompt"])

    def test_update_normalizes_furigana_in_the_cue_path(self):
        info = _cued_note_info(100, japanese=" 大丈夫[だいじょうぶ]です。")
        fake = FakeAnki(
            {
                "notesInfo": [info],
                "findCards": [101],
                "cardsInfo": [{"cardId": 101, "note": 100, "deckName": flashgen.DECK_NAME}],
                "updateNoteFields": None,
            }
        )
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file"), \
             patch("flashgen.store_media_file", side_effect=lambda path, name: name):
            flashgen.update_note(100, fields={"notes": "1.写真[しゃしん]: photo"})

        updated = fake.params_of("updateNoteFields")[0]["note"]["fields"]
        assert html.unescape(updated["Notes"]) == "1. 写真[しゃしん]: photo"


# ---------------------------------------------------------------------------
# 7. Integration: exactly ONE card materializes in Anki
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
_TEST_DECK = "FlashGen-CuedResponse-Test"

_needs_live_anki = pytest.mark.skipif(
    not (_ANKI_CONNECT_URL and _HAS_TTS_KEY),
    reason="requires ANKI_CONNECT_URL and a TTS API key",
)


@_needs_live_anki
class TestCuedCardCountLive:
    @pytest.fixture()
    def test_deck(self):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            flashgen.anki_invoke("createDeck", {"deck": _TEST_DECK})
            created_notes = []
            yield created_notes
            if created_notes:
                flashgen.anki_invoke("deleteNotes", {"notes": created_notes})

    def test_cued_note_yields_exactly_one_card(self, test_deck):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            result = flashgen.create_flashcard(
                card_type="cued_response",
                deck_name=_TEST_DECK,
                japanese=CUED_PAYLOAD["japanese"],
                english=CUED_PAYLOAD["english"],
                japanese_prompt=CUED_PAYLOAD["japanese_prompt"],
                english_prompt=CUED_PAYLOAD["english_prompt"],
                notes=CUED_PAYLOAD["notes"],
                tags=["jp", "auto", "test", "cued-response-acceptance"],
            )
            test_deck.append(result["note_id"])

            card_ids = flashgen.anki_invoke(
                "findCards", {"query": f"nid:{result['note_id']}"}
            )
            assert len(card_ids) == 1, (
                f"cued note must produce exactly 1 card, got {len(card_ids)}"
            )

            (info,) = flashgen.anki_invoke("notesInfo", {"notes": [result["note_id"]]})
            assert info["modelName"] == CUED_MODEL

    def test_standard_note_with_prompt_still_yields_three_cards(self, test_deck):
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            result = flashgen.create_flashcard(
                deck_name=_TEST_DECK,
                japanese="お疲れ様でした。",
                english="Thank you for your hard work.",
                japanese_prompt="今日の会議、大変でしたね。",
                english_prompt="That meeting today was rough, wasn't it?",
                tags=["jp", "auto", "test", "cued-response-acceptance"],
            )
            test_deck.append(result["note_id"])

            card_ids = flashgen.anki_invoke(
                "findCards", {"query": f"nid:{result['note_id']}"}
            )
            assert len(card_ids) == 3, (
                f"standard note with a prompt must still produce 3 cards, "
                f"got {len(card_ids)}"
            )
