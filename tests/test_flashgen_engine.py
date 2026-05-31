"""Coverage for flashgen.py engine logic — safety net before the refactor.

Covers: notes_to_html, resolve_tts_config error paths, anki_invoke error
response, check_anki_ready validation, AnkiConnect helper unexpected responses,
generate_tts_file missing API keys, fill_missing_translation, and the
add_note duplicate-note path.
"""
import pytest
from unittest.mock import MagicMock, patch

import flashgen


# ---------------------------------------------------------------------------
# notes_to_html
# ---------------------------------------------------------------------------

class TestNotesToHtml:
    def test_empty_returns_empty(self):
        assert flashgen.notes_to_html("") == ""
        assert flashgen.notes_to_html("   ") == ""

    def test_plain_text_is_returned(self):
        assert flashgen.notes_to_html("hello") == "hello"

    def test_newlines_become_br(self):
        assert flashgen.notes_to_html("line1\nline2") == "line1<br>line2"

    def test_html_chars_are_escaped(self):
        result = flashgen.notes_to_html("<b>bold</b> & more")
        assert "<b>" not in result
        assert "&amp;" in result
        assert "&lt;" in result


# ---------------------------------------------------------------------------
# resolve_tts_config
# ---------------------------------------------------------------------------

class TestResolveTtsConfig:
    def test_defaults_to_gemini(self):
        cfg = flashgen.resolve_tts_config()
        assert cfg.provider == "gemini"

    def test_openai_provider_sets_mp3_extension(self):
        cfg = flashgen.resolve_tts_config("openai", flashgen.OPENAI_TTS_MODEL)
        assert cfg.extension == ".mp3"

    def test_gemini_provider_sets_wav_extension(self):
        cfg = flashgen.resolve_tts_config("gemini", flashgen.GEMINI_TTS_MODEL)
        assert cfg.extension == ".wav"

    def test_mismatched_provider_and_model_raises(self):
        with pytest.raises(RuntimeError, match="must be provided together"):
            flashgen.resolve_tts_config("openai", "")

    def test_invalid_provider_raises(self):
        with pytest.raises(RuntimeError, match="must be one of"):
            flashgen.resolve_tts_config("anthropic", "some-model")


# ---------------------------------------------------------------------------
# anki_invoke
# ---------------------------------------------------------------------------

class TestAnkiInvoke:
    def test_connection_error_raises_runtime_error(self):
        with patch("flashgen.requests.post", side_effect=Exception("refused")):
            with pytest.raises(Exception):
                flashgen.anki_invoke("version")

    def test_anki_error_in_response_raises_runtime_error(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "deck not found", "result": None}
        mock_resp.raise_for_status.return_value = None
        with patch("flashgen.requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="AnkiConnect error"):
                flashgen.anki_invoke("deckNames")

    def test_successful_response_returns_result(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": None, "result": 6}
        mock_resp.raise_for_status.return_value = None
        with patch("flashgen.requests.post", return_value=mock_resp):
            assert flashgen.anki_invoke("version") == 6


# ---------------------------------------------------------------------------
# check_anki_ready
# ---------------------------------------------------------------------------

def _make_anki_mock(version=6, deck_names=None, model_names=None):
    """Return a side_effect list for anki_invoke: version, deckNames, modelNames."""
    return [
        version,
        deck_names if deck_names is not None else ["日本語-Soso"],
        model_names if model_names is not None else ["Japanese Listening+Production"],
    ]


class TestCheckAnkiReady:
    def test_passes_when_deck_and_model_exist(self):
        with patch("flashgen.anki_invoke", side_effect=_make_anki_mock()):
            flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")

    def test_raises_when_deck_missing(self):
        with patch("flashgen.anki_invoke", side_effect=_make_anki_mock(deck_names=["Other"])):
            with pytest.raises(RuntimeError, match="Deck '.*' not found"):
                flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")

    def test_raises_when_model_missing(self):
        with patch("flashgen.anki_invoke", side_effect=_make_anki_mock(model_names=["Other"])):
            with pytest.raises(RuntimeError, match="Note type '.*' not found"):
                flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")

    def test_raises_on_bad_version_response(self):
        with patch("flashgen.anki_invoke", side_effect=["not-an-int"]):
            with pytest.raises(RuntimeError, match="Unexpected AnkiConnect version"):
                flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")

    def test_raises_on_bad_deck_names_response(self):
        with patch("flashgen.anki_invoke", side_effect=[6, "not-a-list"]):
            with pytest.raises(RuntimeError, match="Unexpected deckNames"):
                flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")

    def test_raises_on_bad_model_names_response(self):
        with patch("flashgen.anki_invoke", side_effect=[6, ["日本語-Soso"], "not-a-list"]):
            with pytest.raises(RuntimeError, match="Unexpected modelNames"):
                flashgen.check_anki_ready("日本語-Soso", "Japanese Listening+Production")


# ---------------------------------------------------------------------------
# AnkiConnect helper unexpected-response guards
# ---------------------------------------------------------------------------

class TestAnkiHelperGuards:
    def test_get_model_field_names_bad_response_raises(self):
        with patch("flashgen.anki_invoke", return_value="not-a-list"):
            with pytest.raises(RuntimeError, match="Unexpected response from modelFieldNames"):
                flashgen.get_model_field_names("MyModel")

    def test_can_add_note_bad_response_raises(self):
        with patch("flashgen.anki_invoke", return_value=["not-a-bool"]):
            with pytest.raises(RuntimeError, match="Unexpected response from canAddNotes"):
                flashgen.can_add_note({"deckName": "x", "modelName": "y", "fields": {}})

    def test_find_existing_notes_bad_response_raises(self):
        with patch("flashgen.anki_invoke", return_value=["not-an-int", 2]):
            with pytest.raises(RuntimeError, match="non-int note ids"):
                flashgen.find_existing_notes("MyModel", "日本語テキスト")


# ---------------------------------------------------------------------------
# generate_tts_file — missing API key paths
# ---------------------------------------------------------------------------

class TestGenerateTtsFileMissingKeys:
    def test_openai_missing_api_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = flashgen.resolve_tts_config("openai", flashgen.OPENAI_TTS_MODEL)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            flashgen.generate_tts_file(cfg, "テスト", tmp_path / "out.mp3")

    def test_gemini_missing_api_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        cfg = flashgen.resolve_tts_config("gemini", flashgen.GEMINI_TTS_MODEL)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            flashgen.generate_tts_file(cfg, "テスト", tmp_path / "out.wav")


# ---------------------------------------------------------------------------
# fill_missing_translation
# ---------------------------------------------------------------------------

class TestFillMissingTranslation:
    def test_both_empty_raises(self):
        client = MagicMock()
        with pytest.raises(RuntimeError, match="Both.*empty"):
            flashgen.fill_missing_translation(client, "", "")

    def test_both_present_returns_unchanged(self):
        client = MagicMock()
        jp, en = flashgen.fill_missing_translation(client, "日本語", "Japanese")
        assert jp == "日本語"
        assert en == "Japanese"
        client.responses.create.assert_not_called()

    def test_missing_english_calls_translation(self):
        client = MagicMock()
        client.responses.create.return_value.output_text = "I took a photo."
        jp, en = flashgen.fill_missing_translation(client, "写真を撮りました。", "")
        assert jp == "写真を撮りました。"
        assert en == "I took a photo."

    def test_missing_japanese_calls_translation(self):
        client = MagicMock()
        client.responses.create.return_value.output_text = "写真を撮りました。"
        jp, en = flashgen.fill_missing_translation(client, "", "I took a photo.")
        assert jp == "写真を撮りました。"
        assert en == "I took a photo."

    def test_empty_translation_response_raises(self):
        client = MagicMock()
        client.responses.create.return_value.output_text = "   "
        with pytest.raises(RuntimeError, match="empty output"):
            flashgen.fill_missing_translation(client, "日本語", "")


# ---------------------------------------------------------------------------
# add_note — duplicate note path
# ---------------------------------------------------------------------------

class TestAddNoteDuplicate:
    def test_duplicate_note_raises(self):
        with patch("flashgen.can_add_note", return_value=False), \
             patch("flashgen.find_existing_notes", return_value=[12345]), \
             patch("flashgen.get_notes_info", return_value=[{"noteId": 12345}]):
            with pytest.raises(RuntimeError, match="cannot be added"):
                flashgen.add_note(
                    deck_name="日本語-Soso",
                    model_name="Japanese Listening+Production",
                    japanese="写真を撮りました。",
                    english="I took a photo.",
                    notes="",
                    audio_filename="test.mp3",
                    tags=["jp"],
                )
