import io
import json
import os
import unittest
from unittest.mock import patch

import flashgen
import flashgen_core.anki
import flashgen_core.service
import flashgen_core.audio
import flashgen_core.errors


class TTSConfigurationTests(unittest.TestCase):
    def test_resolve_tts_config_defaults_to_gemini_3_8(self):
        tts_config = flashgen.resolve_tts_config()

        self.assertEqual(tts_config.provider, "gemini")
        self.assertEqual(tts_config.model, "gemini-3.8-flash-tts")
        self.assertEqual(tts_config.voice, "Algenib")
        self.assertEqual(tts_config.extension, ".wav")

    def test_resolve_tts_config_uses_openai_male_sounding_default_voice(self):
        tts_config = flashgen.resolve_tts_config(
            tts_provider="openai",
            tts_model="gpt-4o-mini-tts",
        )

        self.assertEqual(tts_config.provider, "openai")
        self.assertEqual(tts_config.model, "gpt-4o-mini-tts")
        self.assertEqual(tts_config.voice, "onyx")
        self.assertEqual(tts_config.extension, ".mp3")

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
            flashgen_core.anki, "check_anki_ready"
        ), patch.object(flashgen_core.anki, "get_model_field_names", return_value=[]), patch.object(
            flashgen_core.service, "fill_missing_translation"
        ) as fill_missing_translation, patch.object(
            flashgen_core.audio, "generate_tts_file", side_effect=fake_generate_tts_file
        ), patch.object(
            flashgen_core.anki, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen_core.anki, "add_note", side_effect=fake_add_note
        ):
            result = flashgen.create_flashcard(
                japanese="写真[しゃしん]を撮影[さつえい]しました。",
                english="I took a photo.",
            )

        fill_missing_translation.assert_not_called()
        self.assertEqual(result["tts_provider"], "gemini")
        self.assertEqual(result["tts_model"], flashgen.GEMINI_TTS_MODEL)
        self.assertEqual(result["audio_file"], captured["audio_filename"])
        self.assertEqual(result["japanese_tts"], "写真を撮影しました。")
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

    def test_create_flashcard_uses_tts_proxy_fields_for_both_audio_files(self):
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

        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key"}, clear=True), patch.object(
            flashgen_core.anki, "check_anki_ready"
        ), patch.object(flashgen_core.anki, "get_model_field_names", return_value=[]), patch.object(
            flashgen_core.service, "fill_missing_translation", side_effect=lambda _client, japanese, english: (japanese, english)
        ), patch.object(
            flashgen_core.audio, "generate_tts_file", side_effect=fake_generate_tts_file
        ), patch.object(
            flashgen_core.anki, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen_core.anki, "add_note", side_effect=fake_add_note
        ):
            result = flashgen.create_flashcard(
                japanese="杜若[かきつばた]の花[はな]に見[み]惚[ほ]れている。",
                japanese_tts="かきつばたの花に見[み]ほれている。",
                english="I am captivated by the iris blossoms.",
                japanese_prompt="三河[みかわ]国[のくに]八橋[やつはし]を訪[おとず]ねました。",
                japanese_prompt_tts="三河の国八橋を訪ねました。",
            )

        self.assertEqual(
            tts_calls,
            [
                {
                    "provider": "gemini",
                    "model": flashgen.GEMINI_TTS_MODEL,
                    "suffix": ".wav",
                    "text": "かきつばたの花に見ほれている。",
                },
                {
                    "provider": "gemini",
                    "model": flashgen.GEMINI_TTS_MODEL,
                    "suffix": ".wav",
                    "text": "三河の国八橋を訪ねました。",
                },
            ],
        )
        self.assertEqual(result["japanese_tts"], "かきつばたの花に見ほれている。")
        self.assertEqual(result["japanese_prompt_tts"], "三河の国八橋を訪ねました。")
        self.assertEqual(captured["audio_filename"], result["audio_file"])
        self.assertEqual(captured["audio_prompt_filename"], result["audio_prompt_file"])

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
            flashgen_core.anki, "check_anki_ready"
        ), patch.object(flashgen_core.anki, "get_model_field_names", return_value=[]), patch.object(
            flashgen_core.service, "fill_missing_translation", side_effect=lambda _client, japanese, english: (japanese, english)
        ), patch.object(
            flashgen_core.audio, "generate_tts_file", side_effect=fake_generate_tts_file
        ), patch.object(
            flashgen_core.anki, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen_core.anki, "add_note", side_effect=fake_add_note
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

    def test_audio_filename_changes_when_tts_proxy_changes(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key"}, clear=True), patch.object(
            flashgen_core.anki, "check_anki_ready"
        ), patch.object(flashgen_core.anki, "get_model_field_names", return_value=[]), patch.object(
            flashgen_core.service, "fill_missing_translation", side_effect=lambda _client, japanese, english: (japanese, english)
        ), patch.object(
            flashgen_core.audio, "generate_tts_file"
        ), patch.object(
            flashgen_core.anki, "store_media_file", side_effect=lambda _path, filename: filename
        ), patch.object(
            flashgen_core.anki, "add_note", return_value=123
        ):
            first = flashgen.create_flashcard(
                japanese="杜若[かきつばた]です。",
                japanese_tts="かきつばたです。",
                english="It is a rabbit-ear iris.",
            )
            second = flashgen.create_flashcard(
                japanese="杜若[かきつばた]です。",
                japanese_tts="とじゃくです。",
                english="It is a rabbit-ear iris.",
            )

        self.assertNotEqual(first["audio_file"], second["audio_file"])
        self.assertNotEqual(first["local_audio_path"], second["local_audio_path"])

    def test_main_accepts_tts_proxy_fields_and_prints_them(self):
        payload = {
            "japanese": "杜若[かきつばた]です。",
            "english": "It is a rabbit-ear iris.",
            "notes": "",
            "tags": ["auto"],
            "japanese_tts": "かきつばたです。",
            "japanese_prompt": "三河[みかわ]国[のくに]八橋[やつはし]です。",
            "english_prompt": "This is Yatsuhashi in Mikawa Province.",
            "japanese_prompt_tts": "三河の国八橋です。",
        }
        result_payload = {
            "status": "ok",
            "note_id": 123,
            "deck": flashgen.DECK_NAME,
            "model": flashgen.MODEL_NAME,
            "japanese": payload["japanese"],
            "english": payload["english"],
            "notes": payload["notes"],
            "tags": payload["tags"],
            "audio_file": "sample.wav",
            "local_audio_path": "anki_audio_out/sample.wav",
            "tts_provider": "gemini",
            "tts_model": flashgen.GEMINI_TTS_MODEL,
            "japanese_tts": payload["japanese_tts"],
            "japanese_prompt": payload["japanese_prompt"],
            "english_prompt": payload["english_prompt"],
            "japanese_prompt_tts": payload["japanese_prompt_tts"],
            "audio_prompt_file": "prompt.wav",
        }

        with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout, patch.object(
            flashgen, "create_flashcard", return_value=result_payload
        ) as create_flashcard:
            flashgen.main()

        create_flashcard.assert_called_once_with(
            japanese=payload["japanese"],
            english=payload["english"],
            notes=payload["notes"],
            tags=payload["tags"],
            deck_name=flashgen.DECK_NAME,
            japanese_prompt=payload["japanese_prompt"],
            english_prompt=payload["english_prompt"],
            japanese_tts=payload["japanese_tts"],
            japanese_prompt_tts=payload["japanese_prompt_tts"],
            tts_provider=None,
            tts_model=None,
            card_type="standard",
        )
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["japanese_tts"], payload["japanese_tts"])
        self.assertEqual(rendered["japanese_prompt_tts"], payload["japanese_prompt_tts"])


class RetiredGeminiModelTests(unittest.TestCase):
    """Google shuts these down on 2026-12-18 (flashgen-glm)."""

    def test_each_retired_model_is_rejected_with_the_replacement_named(self):
        for model in (
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
        ):
            with self.subTest(model=model):
                with self.assertRaises(flashgen_core.errors.ValidationError) as ctx:
                    flashgen_core.audio.resolve_tts_config(
                        tts_provider="gemini", tts_model=model
                    )
                self.assertIn("retired", str(ctx.exception))
                self.assertIn("gemini-3.8-flash-tts", str(ctx.exception))

    def test_current_models_still_pass_through(self):
        for model in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"):
            with self.subTest(model=model):
                cfg = flashgen_core.audio.resolve_tts_config(
                    tts_provider="gemini", tts_model=model
                )
                self.assertEqual(cfg.model, model)


class GeminiAudioPayloadTests(unittest.TestCase):
    """3.1 preview returned raw PCM; 3.8 returns a whole WAV file."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pcm = bytes(range(256)) * 40  # 10,240 bytes = 5,120 16-bit frames

    def _read(self, path):
        import wave
        with wave.open(str(path), "rb") as w:
            return w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())

    def _wav_bytes(self, rate=24000):
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(self.pcm)
        return buf.getvalue()

    def test_raw_pcm_is_wrapped_once_at_the_mime_rate(self):
        from pathlib import Path
        out = Path(self.tmp.name) / "pcm.wav"
        flashgen_core.audio.write_gemini_audio(
            out, self.pcm, "audio/l16; rate=24000; channels=1"
        )
        rate, channels, frames = self._read(out)
        self.assertEqual((rate, channels), (24000, 1))
        self.assertEqual(frames, self.pcm)

    def test_wav_payload_is_written_as_is_not_double_wrapped(self):
        from pathlib import Path
        out = Path(self.tmp.name) / "wav.wav"
        payload = self._wav_bytes()
        flashgen_core.audio.write_gemini_audio(out, payload, "audio/wav")
        self.assertEqual(out.read_bytes(), payload)
        _, _, frames = self._read(out)
        self.assertEqual(frames, self.pcm)
        self.assertNotIn(b"RIFF", frames)

    def test_wav_payload_is_detected_even_if_mislabelled(self):
        from pathlib import Path
        out = Path(self.tmp.name) / "mislabelled.wav"
        payload = self._wav_bytes(rate=16000)
        flashgen_core.audio.write_gemini_audio(out, payload, "audio/l16; rate=24000")
        rate, _, frames = self._read(out)
        self.assertEqual(rate, 16000)
        self.assertEqual(frames, self.pcm)

    def test_truncated_wav_payload_fails_loudly(self):
        from pathlib import Path
        out = Path(self.tmp.name) / "bad.wav"
        with self.assertRaises(RuntimeError):
            flashgen_core.audio.write_gemini_audio(out, b"RIFF\x00\x00", "audio/wav")
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
