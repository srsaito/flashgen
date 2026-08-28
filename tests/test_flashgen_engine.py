"""Coverage for flashgen.py engine logic — safety net before the refactor.

Covers: rich_text_to_html, resolve_tts_config error paths, anki_invoke error
response, check_anki_ready validation, AnkiConnect helper unexpected responses,
generate_tts_file missing API keys, fill_missing_translation, and the
add_note duplicate-note path.
"""
import pytest
from unittest.mock import MagicMock, patch

import flashgen


# ---------------------------------------------------------------------------
# rich_text_to_html (formerly notes_to_html — now applied to all display
# fields with line breaks (GH #4 / flashgen-qgg) and an inline-emphasis tag
# allowlist (GH #5 / flashgen-c9a))
# ---------------------------------------------------------------------------

class TestRichTextToHtml:
    def test_empty_returns_empty(self):
        assert flashgen.rich_text_to_html("") == ""
        assert flashgen.rich_text_to_html("   ") == ""

    def test_plain_text_is_returned(self):
        assert flashgen.rich_text_to_html("hello") == "hello"

    def test_newlines_become_br(self):
        assert flashgen.rich_text_to_html("line1\nline2") == "line1<br>line2"

    def test_non_allowlisted_html_is_escaped(self):
        result = flashgen.rich_text_to_html("<span>styled</span> & more")
        assert "<span>" not in result
        assert "&amp;" in result
        assert "&lt;span&gt;" in result

    def test_literal_br_tags_become_real_br(self):
        # MCP clients send line breaks as literal <br> tags. These must render
        # as breaks, not escape into the visible text "&lt;br&gt;" (flashgen-3c6).
        assert flashgen.rich_text_to_html("a<br>b") == "a<br>b"
        assert flashgen.rich_text_to_html("a<br/>b") == "a<br>b"
        assert flashgen.rich_text_to_html("a<br />b") == "a<br>b"
        assert flashgen.rich_text_to_html("a<BR>b") == "a<br>b"

    def test_literal_backslash_n_becomes_br(self):
        # A literal two-char backslash-n (not a real newline) must also convert.
        assert flashgen.rich_text_to_html("a\\nb") == "a<br>b"
        assert flashgen.rich_text_to_html("a\\r\\nb") == "a<br>b"

    def test_crlf_becomes_single_br(self):
        assert flashgen.rich_text_to_html("a\r\nb") == "a<br>b"

    def test_html_chars_still_escaped_with_br_input(self):
        # Genuine HTML-special chars in the definition stay escaped even when
        # the caller uses <br> for breaks.
        result = flashgen.rich_text_to_html("cost < 5 & up<br>line2")
        assert result == "cost &lt; 5 &amp; up<br>line2"

    def test_furigana_annotation_survives_around_breaks(self):
        # The leading space of an annotated unit after a break must survive so
        # {{furigana:...}} still recognizes the unit.
        result = flashgen.rich_text_to_html("はい。<br> 元気[げんき]です。")
        assert result == "はい。<br> 元気[げんき]です。"


# ---------------------------------------------------------------------------
# Inline emphasis allowlist (GH #5 / flashgen-c9a)
# ---------------------------------------------------------------------------

class TestEmphasisRendering:
    def test_allowlisted_tags_render(self):
        assert flashgen.rich_text_to_html("えい<b>よう</b>") == "えい<b>よう</b>"
        assert flashgen.rich_text_to_html("<strong>x</strong>") == "<strong>x</strong>"
        assert flashgen.rich_text_to_html("<i>x</i> <em>y</em>") == "<i>x</i> <em>y</em>"
        assert flashgen.rich_text_to_html("<u>x</u>") == "<u>x</u>"

    def test_tags_canonicalized_to_lowercase(self):
        assert flashgen.rich_text_to_html("<B>x</B>") == "<b>x</b>"

    def test_math_angle_brackets_stay_literal(self):
        # "< b >" with interior whitespace is not a tag — comparison text in an
        # English gloss must not be swallowed as markup.
        result = flashgen.rich_text_to_html("a < b > c")
        assert result == "a &lt; b &gt; c"
        assert flashgen.markup_errors("a < b > c") == []

    def test_tag_with_attribute_is_escaped_not_stripped(self):
        result = flashgen.rich_text_to_html('<b class="x">y</b>')
        assert result.startswith("&lt;b class=")
        assert "y</b>" in result  # the bare closing tag is still allowlisted

    def test_emphasis_composes_with_breaks_and_escaping(self):
        result = flashgen.rich_text_to_html("cost < 5 & <b>up</b>\nline2")
        assert result == "cost &lt; 5 &amp; <b>up</b><br>line2"

    def test_emphasis_wrapping_whole_furigana_unit_survives(self):
        result = flashgen.rich_text_to_html("<b> 栄養[えいよう]</b>です")
        assert result == "<b> 栄養[えいよう]</b>です"


class TestMarkupErrors:
    def test_clean_text_has_no_errors(self):
        assert flashgen.markup_errors("えい<b>よう</b>と 営業[えいぎょう]") == []
        assert flashgen.markup_errors("") == []

    def test_unclosed_tag_reported(self):
        problems = flashgen.markup_errors("<b>bold")
        assert any("unclosed <b>" in p for p in problems)

    def test_stray_closing_tag_reported(self):
        problems = flashgen.markup_errors("bold</b>")
        assert any("unbalanced </b>" in p for p in problems)

    def test_misnested_tags_reported(self):
        assert flashgen.markup_errors("<b><i>x</b></i>") != []

    def test_tag_inside_annotated_unit_rejected(self):
        problems = flashgen.markup_errors(" 漢<b>字[かんじ]</b>")
        assert any("splits the annotated unit" in p for p in problems)

    def test_tag_between_kanji_and_reading_rejected(self):
        problems = flashgen.markup_errors(" 漢字<b>[かんじ]</b>")
        assert any("splits the annotated unit" in p for p in problems)

    def test_tag_wrapping_whole_unit_accepted(self):
        assert flashgen.markup_errors("<b> 漢字[かんじ]</b>") == []
        # Boundary in the unit's leading spaces is also fine.
        assert flashgen.markup_errors(" <b> 漢字[かんじ]</b>") == []
        assert flashgen.markup_errors("<b>漢字[かんじ]</b>") == []

    def test_non_allowlisted_markup_is_not_an_error(self):
        assert flashgen.markup_errors("<span>x</span>") == []


class TestMarkupWarnings:
    def test_non_allowlisted_tag_warned(self):
        warnings = flashgen.markup_warnings("<span>x</span>")
        assert len(warnings) == 2  # open + close
        assert "literal text" in warnings[0]

    def test_attributed_allowlisted_tag_warned(self):
        warnings = flashgen.markup_warnings('<b class="x">y</b>')
        assert len(warnings) == 1

    def test_clean_emphasis_and_br_not_warned(self):
        assert flashgen.markup_warnings("a<br><b>x</b>") == []


# ---------------------------------------------------------------------------
# resolve_tts_input — breaks must never reach TTS text (GH #4 / flashgen-qgg)
# ---------------------------------------------------------------------------

class TestResolveTtsInputBreaks:
    def test_newline_in_display_becomes_space(self):
        assert flashgen.resolve_tts_input("こんにちは\n元気？") == "こんにちは 元気？"

    def test_literal_br_in_display_becomes_space(self):
        assert flashgen.resolve_tts_input("こんにちは<br>元気？") == "こんにちは 元気？"

    def test_br_in_explicit_tts_override_becomes_space(self):
        assert flashgen.resolve_tts_input("表示", "こんにちは<br/>元気？") == "こんにちは 元気？"

    def test_break_with_furigana_display(self):
        assert (
            flashgen.resolve_tts_input("はい。<br> 元気[げんき]です。")
            == "はい。 元気です。"
        )


# ---------------------------------------------------------------------------
# create_flashcard break intake (GH #4 / flashgen-qgg)
# ---------------------------------------------------------------------------

class TestCreateFlashcardBreakIntake:
    def test_breaks_render_in_fields_but_never_reach_tts_or_filename(self):
        calls = []

        def invoke(action, params=None):
            calls.append((action, params))
            return {
                "version": 6,
                "deckNames": [flashgen.DECK_NAME],
                "modelNames": [flashgen.MODEL_NAME],
                "modelFieldNames": ["Japanese", "English", "Notes", "Audio"],
                "canAddNotes": [True],
                "addNote": 111,
            }[action]

        with patch("flashgen.anki_invoke", side_effect=invoke), \
             patch("flashgen.generate_tts_file") as mock_tts, \
             patch("flashgen.store_media_file", side_effect=lambda path, name: name):
            result = flashgen.create_flashcard(
                japanese="はい。<br> 元気[げんき]です。",
                english="Yes.\nI'm fine.",
            )

        tts_text = mock_tts.call_args.args[1]
        assert tts_text == "はい。 元気です。"
        assert "<br" not in result["audio_file"]
        assert "\n" not in result["audio_file"]

        note = next(p for a, p in calls if a == "addNote")["note"]
        assert note["fields"]["Japanese"] == "はい。<br> 元気[げんき]です。"
        assert note["fields"]["English"] == "Yes.<br>I'm fine."

    def test_emphasis_renders_but_never_reaches_tts_or_filename(self):
        calls = []

        def invoke(action, params=None):
            calls.append((action, params))
            return {
                "version": 6,
                "deckNames": [flashgen.DECK_NAME],
                "modelNames": [flashgen.MODEL_NAME],
                "modelFieldNames": ["Japanese", "English", "Notes", "Audio"],
                "canAddNotes": [True],
                "addNote": 112,
            }[action]

        with patch("flashgen.anki_invoke", side_effect=invoke), \
             patch("flashgen.generate_tts_file") as mock_tts, \
             patch("flashgen.store_media_file", side_effect=lambda path, name: name):
            result = flashgen.create_flashcard(
                japanese="えい<b> 養[よう]</b>",
                english="<i>nutrition</i>",
            )

        assert mock_tts.call_args.args[1] == "えい養"
        assert "b" != result["audio_file"][:1]
        assert "<" not in result["audio_file"]
        note = next(p for a, p in calls if a == "addNote")["note"]
        assert note["fields"]["Japanese"] == "えい<b> 養[よう]</b>"
        assert note["fields"]["English"] == "<i>nutrition</i>"

    def test_unbalanced_emphasis_rejected_before_any_anki_call(self):
        with patch("flashgen.anki_invoke") as mock_invoke:
            with pytest.raises(RuntimeError, match="Invalid markup in 'japanese'"):
                flashgen.create_flashcard(japanese="えい<b>よう", english="x")
        mock_invoke.assert_not_called()

    def test_mid_unit_emphasis_rejected(self):
        with pytest.raises(RuntimeError, match="splits the annotated unit"):
            flashgen.create_flashcard(japanese=" 漢<b>字[かんじ]</b>", english="x")


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
