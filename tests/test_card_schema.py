import pytest
from pydantic import ValidationError

from flashgen_mcp.schema import CardRequest, CardResult


class TestCardRequestValidation:
    def test_accepts_japanese_only(self):
        req = CardRequest(japanese="写真[しゃしん]を撮りました。")
        assert req.japanese == "写真[しゃしん]を撮りました。"
        assert req.english == ""

    def test_accepts_english_only(self):
        req = CardRequest(english="I took a photo.")
        assert req.english == "I took a photo."
        assert req.japanese == ""

    def test_accepts_both_fields(self):
        req = CardRequest(japanese="写真を撮りました。", english="I took a photo.")
        assert req.japanese == "写真を撮りました。"
        assert req.english == "I took a photo."

    def test_rejects_neither_field(self):
        with pytest.raises(ValidationError, match="at least one of"):
            CardRequest()

    def test_rejects_both_empty_strings(self):
        with pytest.raises(ValidationError, match="at least one of"):
            CardRequest(japanese="", english="")

    def test_rejects_tts_provider_without_tts_model(self):
        with pytest.raises(ValidationError, match="together"):
            CardRequest(japanese="写真を撮りました。", tts_provider="openai")

    def test_rejects_tts_model_without_tts_provider(self):
        with pytest.raises(ValidationError, match="together"):
            CardRequest(japanese="写真を撮りました。", tts_model="gpt-4o-mini-tts")

    def test_accepts_tts_provider_and_model_together(self):
        req = CardRequest(
            japanese="写真を撮りました。",
            tts_provider="openai",
            tts_model="gpt-4o-mini-tts",
        )
        assert req.tts_provider == "openai"
        assert req.tts_model == "gpt-4o-mini-tts"

    def test_accepts_all_optional_fields(self):
        req = CardRequest(
            japanese="写真[しゃしん]を撮影[さつえい]しました。",
            english="I took a photo.",
            notes="撮影 means photography",
            tags=["photography", "日本語"],
            deck="MyDeck",
            japanese_tts="写真を撮影しました。",
            japanese_prompt="写真を撮りましたか？",
            english_prompt="Did you take a photo?",
            japanese_prompt_tts="写真を撮りましたか？",
            tts_provider="gemini",
            tts_model="gemini-3.1-flash-tts-preview",
        )
        assert req.notes == "撮影 means photography"
        assert req.tags == ["photography", "日本語"]
        assert req.deck == "MyDeck"


class TestCardResultShape:
    def test_result_has_required_fields(self):
        result = CardResult(
            status="ok",
            note_id=12345,
            deck="日本語-Soso",
            model="Japanese Listening+Production",
            japanese=" 写[しゃ] 真[しん]を撮りました。",
            english="I took a photo.",
            notes="",
            tags=["auto"],
            tts_provider="gemini",
            tts_model="gemini-3.1-flash-tts-preview",
            japanese_tts="写真を撮りました。",
            audio_file="abc123.wav",
            local_audio_path="anki_audio_out/abc123.wav",
        )
        assert result.status == "ok"
        assert result.note_id == 12345

    def test_result_prompt_fields_are_optional(self):
        result = CardResult(
            status="ok",
            note_id=12345,
            deck="日本語-Soso",
            model="Japanese Listening+Production",
            japanese=" 写[しゃ] 真[しん]を撮りました。",
            english="I took a photo.",
            notes="",
            tags=["auto"],
            tts_provider="gemini",
            tts_model="gemini-3.1-flash-tts-preview",
            japanese_tts="写真を撮りました。",
            audio_file="abc123.wav",
            local_audio_path="anki_audio_out/abc123.wav",
        )
        assert result.japanese_prompt is None
        assert result.english_prompt is None
        assert result.japanese_prompt_tts is None
        assert result.audio_prompt_file is None

    def test_result_accepts_prompt_fields(self):
        result = CardResult(
            status="ok",
            note_id=12345,
            deck="日本語-Soso",
            model="Japanese Listening+Production",
            japanese=" 日本[にほん]に 行[い]きますか？",
            english="Are you going to Japan?",
            notes="",
            tags=["auto"],
            tts_provider="gemini",
            tts_model="gemini-3.1-flash-tts-preview",
            japanese_tts="日本に行きますか？",
            audio_file="abc123.wav",
            local_audio_path="anki_audio_out/abc123.wav",
            japanese_prompt=" 三河[みかわ]から 来[き]ました。",
            english_prompt="I came from Mikawa.",
            japanese_prompt_tts="三河から来ました。",
            audio_prompt_file="prompt456.wav",
        )
        assert result.japanese_prompt == " 三河[みかわ]から 来[き]ました。"
        assert result.audio_prompt_file == "prompt456.wav"
