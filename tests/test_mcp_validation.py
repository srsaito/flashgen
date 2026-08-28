import pytest
from fastapi.testclient import TestClient

from flashgen_mcp.app import app

client = TestClient(app)


class TestValidateEndpoint:
    def test_valid_japanese_only_returns_200(self):
        response = client.post(
            "/validate",
            json={"japanese": "写真[しゃしん]を撮りました。"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "japanese" in body

    def test_valid_english_only_returns_200(self):
        response = client.post(
            "/validate",
            json={"english": "I took a photo."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "english" in body

    def test_valid_full_request_returns_normalized_fields(self):
        response = client.post(
            "/validate",
            json={
                "japanese": "写真[しゃしん]を撮影[さつえい]しました。",
                "english": "I took a photo.",
                "tags": ["photography"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["japanese"] == " 写真[しゃしん]を 撮影[さつえい]しました。"
        assert body["english"] == "I took a photo."
        assert body["tags"] == ["photography"]

    def test_missing_japanese_and_english_returns_422(self):
        response = client.post("/validate", json={})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        detail = str(body["detail"]).lower()
        assert "japanese" in detail or "english" in detail

    def test_empty_japanese_and_english_returns_422(self):
        response = client.post("/validate", json={"japanese": "", "english": ""})
        assert response.status_code == 422

    def test_partial_tts_config_returns_422(self):
        response = client.post(
            "/validate",
            json={"japanese": "写真を撮りました。", "tts_provider": "openai"},
        )
        assert response.status_code == 422
        body = response.json()
        detail = str(body["detail"]).lower()
        assert "tts_provider" in detail or "tts_model" in detail or "together" in detail

    def test_valid_tts_pair_is_accepted(self):
        response = client.post(
            "/validate",
            json={
                "japanese": "写真を撮りました。",
                "tts_provider": "openai",
                "tts_model": "gpt-4o-mini-tts",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tts_provider"] == "openai"
        assert body["tts_model"] == "gpt-4o-mini-tts"

    def test_japanese_prompt_is_normalized(self):
        response = client.post(
            "/validate",
            json={
                "japanese": "写真を撮りました。",
                "english": "I took a photo.",
                "japanese_prompt": "写真[しゃしん]を撮影[さつえい]しましたか？",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["japanese_prompt"] == " 写真[しゃしん]を 撮影[さつえい]しましたか？"


class TestValidateMarkupReporting:
    """GH #5 / flashgen-c9a: validate reports emphasis-markup problems."""

    def test_clean_emphasis_is_ok_with_no_markup_keys(self):
        response = client.post(
            "/validate",
            json={"japanese": "えい<b>よう</b>", "english": "nutrition"},
        )
        body = response.json()
        assert body["status"] == "ok"
        assert "markup_errors" not in body
        assert "markup_warnings" not in body

    def test_unbalanced_tag_reported_as_invalid(self):
        response = client.post(
            "/validate",
            json={"japanese": "えい<b>よう", "english": "nutrition"},
        )
        body = response.json()
        assert body["status"] == "invalid"
        assert any("unclosed <b>" in p for p in body["markup_errors"]["japanese"])

    def test_tag_splitting_furigana_unit_reported_as_invalid(self):
        response = client.post(
            "/validate",
            json={"japanese": " 漢<b>字[かんじ]</b>", "english": "kanji"},
        )
        body = response.json()
        assert body["status"] == "invalid"
        assert any(
            "splits the annotated unit" in p
            for p in body["markup_errors"]["japanese"]
        )

    def test_non_allowlisted_markup_warned_but_still_ok(self):
        response = client.post(
            "/validate",
            json={"japanese": "はい", "english": "<span>yes</span>"},
        )
        body = response.json()
        assert body["status"] == "ok"
        assert "markup_warnings" in body
        assert "english" in body["markup_warnings"]
