import os
import unittest
from unittest.mock import patch

import flashgen


class TTSConfigurationTests(unittest.TestCase):
    def test_resolve_tts_config_defaults_to_gemini_3_1_preview(self):
        tts_config = flashgen.resolve_tts_config()

        self.assertEqual(tts_config.provider, "gemini")
        self.assertEqual(tts_config.model, "gemini-3.1-flash-tts-preview")
        self.assertEqual(tts_config.extension, ".wav")

    def test_create_flashcard_defaults_to_gemini_without_openai_key(self):
        captured = {}
        tts_calls = []

        def fake_generate_tts_file(tts_config, text, out_path):
            tts_calls.append(
                {
                    "provider": tts_config.provider,
                    "model": tts_config.model,
                    "suffix": out_path.suffix,
                    "text": text,
                }
            )

        def fake_add_note(**kwargs):
            captured.update(kwargs)
            return 123

        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key"}, clear=True), patch.object(
            flashgen, "check_anki_ready"
        ), patch.object(flashgen, "get_model_field_names", return_value=[]), patch.object(
            flashgen, "fill_missing_translation"
        ) as fill_missing_translation, patch.object(
            flashgen, "generate_tts_file", side_effect=fake_generate_tts_file
        ), patch.object(
            flashgen, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen, "add_note", side_effect=fake_add_note
        ):
            result = flashgen.create_flashcard(
                japanese="写真[しゃしん]を撮影[さつえい]しました。",
                english="I took a photo.",
            )

        fill_missing_translation.assert_not_called()
        self.assertEqual(result["tts_provider"], "gemini")
        self.assertEqual(result["tts_model"], flashgen.GEMINI_TTS_MODEL)
        self.assertEqual(result["audio_file"], captured["audio_filename"])
        self.assertEqual(
            tts_calls,
            [
                {
                    "provider": "gemini",
                    "model": flashgen.GEMINI_TTS_MODEL,
                    "suffix": ".wav",
                    "text": "写真を撮影しました。",
                }
            ],
        )

    def test_create_flashcard_rejects_partial_tts_configuration(self):
        with self.assertRaisesRegex(
            RuntimeError, "'tts_provider' and 'tts_model' must be provided together"
        ):
            flashgen.create_flashcard(
                japanese="写真を撮影しました。",
                english="I took a photo.",
                tts_provider="openai",
            )

    def test_create_flashcard_requires_gemini_key_for_default_tts(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is not set"):
                flashgen.create_flashcard(
                    japanese="写真を撮影しました。",
                    english="I took a photo.",
                )

    def test_create_flashcard_requires_openai_key_for_openai_tts(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is not set"):
                flashgen.create_flashcard(
                    japanese="写真を撮影しました。",
                    english="I took a photo.",
                    tts_provider="openai",
                    tts_model="gpt-4o-mini-tts",
                )

    def test_create_flashcard_requires_openai_key_for_translation(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is not set"):
                flashgen.create_flashcard(
                    english="I took a photo.",
                )

    def test_create_flashcard_uses_explicit_openai_tts_for_both_audio_files(self):
        captured = {}
        tts_calls = []

        def fake_add_note(**kwargs):
            captured.update(kwargs)
            return 123

        def fake_generate_tts_file(tts_config, text, out_path):
            tts_calls.append(
                {
                    "provider": tts_config.provider,
                    "model": tts_config.model,
                    "suffix": out_path.suffix,
                    "text": text,
                }
            )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-key"}, clear=True), patch.object(
            flashgen, "check_anki_ready"
        ), patch.object(flashgen, "get_model_field_names", return_value=[]), patch.object(
            flashgen, "fill_missing_translation", side_effect=lambda _client, japanese, english: (japanese, english)
        ), patch.object(
            flashgen, "generate_tts_file", side_effect=fake_generate_tts_file
        ), patch.object(
            flashgen, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen, "add_note", side_effect=fake_add_note
        ):
            result = flashgen.create_flashcard(
                japanese="写真[しゃしん]を撮影[さつえい]しました。",
                english="I took a photo.",
                japanese_prompt="日本[にほん]に行[い]きますか？",
                tts_provider="openai",
                tts_model="gpt-4o-mini-tts",
            )

        self.assertEqual(result["tts_provider"], "openai")
        self.assertEqual(result["tts_model"], "gpt-4o-mini-tts")
        self.assertEqual(
            tts_calls,
            [
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini-tts",
                    "suffix": ".mp3",
                    "text": "写真を撮影しました。",
                },
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini-tts",
                    "suffix": ".mp3",
                    "text": "日本に行きますか？",
                },
            ],
        )
        self.assertEqual(captured["audio_filename"], result["audio_file"])
        self.assertEqual(captured["audio_prompt_filename"], result["audio_prompt_file"])


if __name__ == "__main__":
    unittest.main()
