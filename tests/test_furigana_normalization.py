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


# --- Notes is rendered with {{furigana:Notes}} too (2026-09-11) -------------
# Until this date only Japanese / Japanese Prompt were normalized, so a unit
# written without its leading space kept the preceding character inside the
# ruby base: "remodeling（和製[わせい]" put わせい over "remodeling（和製".

def test_notes_style_text_gets_the_leading_space():
    from flashgen import normalize_furigana_text as n
    assert n("remodeling（和製[わせい]") == "remodeling（ 和製[わせい]"
    assert n("<b>辞書形[じしょけい]") == "<b> 辞書形[じしょけい]"
    assert n("1.0・未訂正[みていせい]") == "1.0・ 未訂正[みていせい]"
    assert n("N用[よう]") == "N 用[よう]"


def test_furigana_errors_flags_bracketed_timestamps():
    from flashgen import furigana_errors, normalize_furigana_text
    # The brackets are the furigana syntax, so a timestamp in them becomes ruby
    # over whatever precedes it — the backtick here, "m15" if it were dropped.
    assert furigana_errors(normalize_furigana_text("録画[ろくが] m15 `[13:42]`"))
    assert furigana_errors(normalize_furigana_text("録画[ろくが] m15 [13:42]"))


def test_furigana_errors_passes_well_formed_text():
    from flashgen import furigana_errors, normalize_furigana_text
    for ok in (
        " 震[しん] 源[げん]が 海[かい] 底[てい]です",
        " 取[と]り 消[け]し 線[せん]",
        "m03 13:42 訂正[ていせい]",
        "plain english, no brackets",
        "",
    ):
        assert furigana_errors(normalize_furigana_text(ok)) == []
