"""Phase 3 TDD: failing tests for POST /create endpoint."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from flashgen_mcp.app import app

client = TestClient(app)

VALID_PAYLOAD = {
    "japanese": "写真[しゃしん]を撮りました。",
    "english": "I took a photo.",
}


class TestCreateEndpointSuccess:
    def test_valid_request_calls_create_flashcard_and_returns_result(self):
        fake_result = {
            "status": "ok",
            "note_id": 1234567890,
            "deck": "Japanese",
            "model": "Japanese-Card",
            "japanese": " 写[しゃ] 真[しん]を撮りました。",
            "english": "I took a photo.",
            "notes": "",
            "tags": ["jp"],
            "audio_file": "abc123.mp3",
            "local_audio_path": "/tmp/abc123.mp3",
            "tts_provider": "openai",
            "tts_model": "gpt-4o-mini-tts",
            "japanese_tts": " 写[しゃ] 真[しん]を撮りました。",
        }
        with patch("flashgen.create_flashcard", return_value=fake_result) as mock_create:
            response = client.post("/create", json=VALID_PAYLOAD)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["note_id"] == 1234567890
        assert body["deck"] == "Japanese"
        mock_create.assert_called_once()

    def test_create_passes_all_fields_to_engine(self):
        payload = {
            "japanese": "写真を撮りました。",
            "english": "I took a photo.",
            "notes": "casual speech",
            "tags": ["photography", "jp"],
            "tts_provider": "openai",
            "tts_model": "gpt-4o-mini-tts",
        }
        fake_result = {
            "status": "ok",
            "note_id": 42,
            "deck": "Japanese",
            "model": "Japanese-Card",
            "japanese": "写真を撮りました。",
            "english": "I took a photo.",
            "notes": "casual speech",
            "tags": ["photography", "jp"],
            "audio_file": "x.mp3",
            "local_audio_path": "/tmp/x.mp3",
            "tts_provider": "openai",
            "tts_model": "gpt-4o-mini-tts",
            "japanese_tts": "写真を撮りました。",
        }
        with patch("flashgen.create_flashcard", return_value=fake_result) as mock_create:
            response = client.post("/create", json=payload)

        assert response.status_code == 200
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["notes"] == "casual speech"
        assert call_kwargs["tags"] == ["photography", "jp"]
        assert call_kwargs["tts_provider"] == "openai"
        assert call_kwargs["tts_model"] == "gpt-4o-mini-tts"


class TestCreateEndpointAnkiUnavailable:
    def test_anki_connect_unreachable_returns_structured_error_not_500(self):
        import requests as req_lib

        with patch(
            "flashgen.create_flashcard",
            side_effect=req_lib.exceptions.ConnectionError("refused"),
        ):
            response = client.post("/create", json=VALID_PAYLOAD)

        assert response.status_code != 500
        body = response.json()
        assert "error" in body
        assert body["error"] == "anki_unavailable"
        assert "message" in body

    def test_anki_connect_runtime_error_returns_structured_error(self):
        with patch(
            "flashgen.create_flashcard",
            side_effect=RuntimeError("Could not connect to AnkiConnect"),
        ):
            response = client.post("/create", json=VALID_PAYLOAD)

        assert response.status_code != 500
        body = response.json()
        assert "error" in body
        assert body["error"] == "anki_unavailable"


class TestCreateEndpointMissingTTSKey:
    def test_missing_openai_key_returns_structured_error_naming_the_key(self):
        with patch(
            "flashgen.create_flashcard",
            side_effect=RuntimeError("OPENAI_API_KEY is not set."),
        ):
            response = client.post("/create", json=VALID_PAYLOAD)

        assert response.status_code in (400, 422, 503)
        body = response.json()
        assert "error" in body
        assert body["error"] == "missing_api_key"
        assert "message" in body
        assert "OPENAI_API_KEY" in body["message"]

    def test_missing_gemini_key_returns_structured_error_naming_the_key(self):
        with patch(
            "flashgen.create_flashcard",
            side_effect=RuntimeError("GEMINI_API_KEY is not set."),
        ):
            response = client.post("/create", json=VALID_PAYLOAD)

        assert response.status_code in (400, 422, 503)
        body = response.json()
        assert "error" in body
        assert body["error"] == "missing_api_key"
        assert "GEMINI_API_KEY" in body["message"]
