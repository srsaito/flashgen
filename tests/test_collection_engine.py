"""Engine tests for collection read/write functions (GH issue #1)."""
import pytest
from unittest.mock import patch

import flashgen


def note_info(note_id, japanese="", english="", notes="", model="Japanese Listening+Production", tags=None):
    fields = {
        "Japanese": {"value": japanese, "order": 0},
        "English": {"value": english, "order": 1},
        "Notes": {"value": notes, "order": 2},
        "Audio": {"value": "[sound:x.wav]", "order": 3},
    }
    return {
        "noteId": note_id,
        "modelName": model,
        "tags": tags if tags is not None else ["jp"],
        "fields": fields,
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


def default_cards(note_ids):
    """findCards/cardsInfo responses giving each note one card in deck 日本語-Soso."""
    return {
        "findCards": [nid + 1 for nid in note_ids],
        "cardsInfo": [
            {"cardId": nid + 1, "note": nid, "deckName": "日本語-Soso"}
            for nid in note_ids
        ],
    }


class TestSearchNotesQueryBuilding:
    def test_raw_anki_query_passed_through(self):
        fake = FakeAnki({"findNotes": [], "notesInfo": []})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query='tag:jp "Japanese:*語*"')
        assert fake.params_of("findNotes")[0]["query"] == 'tag:jp "Japanese:*語*"'
        assert result["count"] == 0
        assert result["truncated"] is False

    def test_deck_is_anded_into_query(self):
        fake = FakeAnki({"findNotes": [], "notesInfo": []})
        with patch("flashgen.anki_invoke", fake):
            flashgen.search_notes(query="tag:jp", deck="日本語-Soso")
        assert fake.params_of("findNotes")[0]["query"] == 'tag:jp deck:"日本語-Soso"'

    def test_bare_word_builds_wildcard_interleaved_japanese_query(self):
        fake = FakeAnki({"findNotes": [], "notesInfo": []})
        with patch("flashgen.anki_invoke", fake):
            flashgen.search_notes(query="掃除機")
        assert fake.params_of("findNotes")[0]["query"] == '"Japanese:*掃*除*機*"'

    def test_empty_query_with_deck_lists_deck(self):
        fake = FakeAnki({"findNotes": [], "notesInfo": []})
        with patch("flashgen.anki_invoke", fake):
            flashgen.search_notes(query="", deck="日本語-Soso")
        assert fake.params_of("findNotes")[0]["query"] == 'deck:"日本語-Soso"'

    def test_empty_query_without_deck_rejected(self):
        with pytest.raises(RuntimeError, match="query"):
            flashgen.search_notes(query="")

    def test_bad_match_field_rejected(self):
        with pytest.raises(RuntimeError, match="match_field"):
            flashgen.search_notes(query="x", match_field="bogus")


class TestSearchNotesFuriganaMatching:
    """The acceptance-criterion gotcha: 掃除機 must find ' 掃[そう] 除[じ] 機[き]'."""

    def test_plain_word_matches_furigana_annotated_field(self):
        annotated = " 掃[そう] 除[じ] 機[き]は 軽[かる]くて 動[うご]かしやすいですよ"
        infos = [note_info(1, japanese=annotated)]
        fake = FakeAnki({"findNotes": [1], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="掃除機")
        assert result["count"] == 1
        assert result["notes"][0]["note_id"] == 1

    def test_wildcard_overmatch_filtered_out(self):
        # Characters appear in order but not contiguously once furigana is stripped.
        infos = [
            note_info(1, japanese=" 掃[そう] 除[じ] 機[き]を買った"),
            note_info(2, japanese="掃いて、除いて、機を織る"),
        ]
        fake = FakeAnki({"findNotes": [1, 2], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="掃除機")
        assert [n["note_id"] for n in result["notes"]] == [1]

    def test_match_field_english_is_case_insensitive(self):
        infos = [
            note_info(1, english="This Vacuum cleaner is light."),
            note_info(2, english="No match here."),
        ]
        fake = FakeAnki({"findNotes": [1, 2], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="vacuum", match_field="english")
        assert [n["note_id"] for n in result["notes"]] == [1]
        assert fake.params_of("findNotes")[0]["query"] == '"English:*vacuum*"'

    def test_match_field_japanese_matches_raw_markup(self):
        infos = [note_info(1, japanese=" 掃[そう] 除[じ] 機[き]")]
        fake = FakeAnki({"findNotes": [1], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            # Raw field contains the annotation, not the contiguous word.
            result = flashgen.search_notes(query="掃除機", match_field="japanese")
        assert result["count"] == 0


class TestSearchNotesResults:
    def test_limit_and_truncated(self):
        infos = [note_info(i, japanese=f"猫{i}") for i in range(1, 4)]
        fake = FakeAnki({"findNotes": [1, 2, 3], "notesInfo": infos, **default_cards([1, 2])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="猫", limit=2)
        assert result["count"] == 2
        assert result["truncated"] is True

    def test_note_entries_include_model_deck_cards(self):
        infos = [note_info(1, japanese="猫", model="Japanese Dialog Response")]
        fake = FakeAnki({"findNotes": [1], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="猫")
        entry = result["notes"][0]
        assert entry["model"] == "Japanese Dialog Response"
        assert entry["deck"] == "日本語-Soso"
        assert entry["card_ids"] == [2]
        assert entry["card_count"] == 1
        assert entry["tags"] == ["jp"]

    def test_long_field_values_truncated_in_search(self):
        infos = [note_info(1, japanese="猫", notes="x" * 1000)]
        fake = FakeAnki({"findNotes": [1], "notesInfo": infos, **default_cards([1])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.search_notes(query="猫")
        notes_value = result["notes"][0]["fields"]["Notes"]
        assert len(notes_value) == 401 and notes_value.endswith("…")


class TestGetNote:
    def test_returns_full_fields_and_cards(self):
        infos = [note_info(7, japanese="猫", notes="y" * 1000)]
        fake = FakeAnki({"notesInfo": infos, **default_cards([7])})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.get_note(7)
        assert result["note_id"] == 7
        assert result["fields"]["Notes"] == "y" * 1000  # not truncated
        assert result["card_ids"] == [8]

    def test_missing_note_raises_clear_error(self):
        fake = FakeAnki({"notesInfo": [{}]})
        with patch("flashgen.anki_invoke", fake):
            with pytest.raises(RuntimeError, match="Note 999 not found"):
                flashgen.get_note(999)


class TestDeleteNotes:
    def test_deletes_found_and_reports_missing(self):
        fake = FakeAnki({"notesInfo": [note_info(1), {}], "deleteNotes": None})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.delete_notes([1, 999])
        assert result == {"status": "ok", "deleted": [1], "missing": [999]}
        assert fake.params_of("deleteNotes")[0] == {"notes": [1]}

    def test_all_missing_skips_delete_call(self):
        fake = FakeAnki({"notesInfo": [{}]})
        with patch("flashgen.anki_invoke", fake):
            result = flashgen.delete_notes([999])
        assert result["deleted"] == []
        assert result["missing"] == [999]
        assert fake.params_of("deleteNotes") == []

    def test_empty_or_non_int_rejected(self):
        with pytest.raises(RuntimeError):
            flashgen.delete_notes([])
        with pytest.raises(RuntimeError):
            flashgen.delete_notes(["abc"])


def update_fake(note_id=5, **info_kwargs):
    infos = [note_info(note_id, **info_kwargs)]
    return FakeAnki({
        "notesInfo": infos,
        "updateNoteFields": None,
        "addTags": None,
        "removeTags": None,
        **default_cards([note_id]),
    })


class TestUpdateNote:
    def test_unknown_field_rejected(self):
        with pytest.raises(RuntimeError, match="Unknown field"):
            flashgen.update_note(5, fields={"bogus": "x"})

    def test_nothing_to_update_rejected(self):
        with pytest.raises(RuntimeError, match="update"):
            flashgen.update_note(5)

    def test_text_only_update_does_not_touch_tts(self):
        fake = update_fake(japanese="猫")
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file") as mock_tts:
            result = flashgen.update_note(5, fields={"english": "A cat & a dog", "notes": "line1\nline2"})
        mock_tts.assert_not_called()
        sent = fake.params_of("updateNoteFields")[0]["note"]
        assert sent["id"] == 5
        assert sent["fields"]["English"] == "A cat &amp; a dog"
        assert sent["fields"]["Notes"] == "line1<br>line2"
        assert "Audio" not in sent["fields"]
        assert result["status"] == "ok"

    def test_japanese_change_regenerates_audio(self):
        fake = update_fake(japanese="犬")
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file") as mock_tts, \
             patch("flashgen.store_media_file", return_value="neko.wav") as mock_store:
            flashgen.update_note(5, fields={"japanese": "猫[ねこ]がいる"})
        mock_tts.assert_called_once()
        assert mock_tts.call_args.args[1] == "猫がいる"  # furigana stripped for TTS
        mock_store.assert_called_once()
        sent = fake.params_of("updateNoteFields")[0]["note"]["fields"]
        assert sent["Japanese"] == " 猫[ねこ]がいる"  # normalized annotation
        assert sent["Audio"] == "[sound:neko.wav]"

    def test_japanese_tts_only_change_regenerates_audio(self):
        fake = update_fake(japanese="猫[ねこ]がいる")
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file") as mock_tts, \
             patch("flashgen.store_media_file", return_value="v.wav"):
            flashgen.update_note(5, fields={"japanese_tts": "ネコがいる"})
        assert mock_tts.call_args.args[1] == "ネコがいる"
        sent = fake.params_of("updateNoteFields")[0]["note"]["fields"]
        assert sent["Audio"] == "[sound:v.wav]"
        assert "Japanese" not in sent

    def test_prompt_change_regenerates_audio_prompt(self):
        fake = update_fake(japanese="猫")
        with patch("flashgen.anki_invoke", fake), \
             patch("flashgen.generate_tts_file") as mock_tts, \
             patch("flashgen.store_media_file", return_value="p.wav"):
            flashgen.update_note(5, fields={"japanese_prompt": "何[なに]をしますか"})
        mock_tts.assert_called_once()
        sent = fake.params_of("updateNoteFields")[0]["note"]["fields"]
        assert sent["Japanese Prompt"] == " 何[なに]をしますか"
        assert sent["Audio Prompt"] == "[sound:p.wav]"
        assert "Audio" not in sent

    def test_tags_replaced_with_diff(self):
        fake = update_fake(tags=["jp", "old"])
        with patch("flashgen.anki_invoke", fake):
            flashgen.update_note(5, tags=["jp", "new"])
        assert fake.params_of("removeTags")[0] == {"notes": [5], "tags": "old"}
        assert fake.params_of("addTags")[0] == {"notes": [5], "tags": "new"}
        assert fake.params_of("updateNoteFields") == []

    def test_missing_note_raises(self):
        fake = FakeAnki({"notesInfo": [{}]})
        with patch("flashgen.anki_invoke", fake):
            with pytest.raises(RuntimeError, match="not found"):
                flashgen.update_note(999, fields={"english": "x"})
