import os
import unittest
from unittest.mock import patch

import flashgen


class FuriganaNormalizationTests(unittest.TestCase):
    def test_normalizes_spaces_and_keeps_compounds_whole(self):
        text = "スピーチコンテスト中[ちゅう]、　写真[しゃしん]の撮影[さつえい]"

        result = flashgen.normalize_furigana_text(text)

        # Spacing is normalized (full-width space -> single ASCII space; a space
        # inserted before each annotated run) but compounds stay whole — no
        # per-kanji re-derivation.
        self.assertEqual(
            result,
            "スピーチコンテスト 中[ちゅう]、 写真[しゃしん]の 撮影[さつえい]",
        )

    def test_keeps_compound_reading_whole_instead_of_even_splitting(self):
        # Headline case: an even split (試[しち] 着[ゃく] 室[しつ]) is reading-
        # unsafe and even produces a standalone small ゃ. Keep the whole run.
        self.assertEqual(
            flashgen.normalize_furigana_text("試着室[しちゃくしつ]"),
            " 試着室[しちゃくしつ]",
        )

    def test_preserves_llm_per_kanji_grouping(self):
        # When the LLM already annotates per kanji, that grouping is respected —
        # only the spacing is normalized.
        self.assertEqual(
            flashgen.normalize_furigana_text("試[し]着[ちゃく]室[しつ]"),
            " 試[し] 着[ちゃく] 室[しつ]",
        )

    def test_fixes_annotation_space_without_touching_compounds(self):
        text = "今日[きょう]は映画[えいが]を見[み]ます。"

        result = flashgen.normalize_furigana_text(text)

        self.assertEqual(result, " 今日[きょう]は 映画[えいが]を 見[み]ます。")

    def test_create_flashcard_normalizes_shared_cli_and_server_fields(self):
        captured = {}
        tts_inputs = []

        def fake_add_note(**kwargs):
            captured.update(kwargs)
            return 123

        def fake_generate_tts_file(_client, text, _path):
            tts_inputs.append(text)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            flashgen, "check_anki_ready"
        ), patch.object(flashgen, "get_model_field_names", return_value=[]), patch.object(
            flashgen, "OpenAI"
        ), patch.object(
            flashgen,
            "fill_missing_translation",
            side_effect=lambda _client, japanese, english: (japanese, english),
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
            )

        self.assertEqual(captured["japanese"], " 写真[しゃしん]を 撮影[さつえい]しました。")
        self.assertEqual(captured["japanese_prompt"], " 日本[にほん]に 行[い]きますか？")
        self.assertEqual(result["japanese"], captured["japanese"])
        self.assertEqual(result["japanese_prompt"], captured["japanese_prompt"])
        self.assertEqual(tts_inputs, ["写真を撮影しました。", "日本に行きますか？"])


if __name__ == "__main__":
    unittest.main()
