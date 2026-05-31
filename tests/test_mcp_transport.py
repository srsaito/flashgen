"""Phase 4 TDD: failing tests for /mcp Streamable HTTP transport."""
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from flashgen_mcp.app import app

client = TestClient(app)

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def jsonrpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


class TestMCPServerInfo:
    def test_get_mcp_returns_server_info_with_tool_names(self):
        resp = client.get("/mcp")
        assert resp.status_code == 200
        body = resp.json()
        assert "validate_flashcard" in str(body)
        assert "create_flashcard" in str(body)

    def test_initialize_returns_server_info_and_tools_capability(self):
        resp = client.post(
            "/mcp",
            json=jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            ),
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        result = body["result"]
        assert "serverInfo" in result
        assert result["capabilities"]["tools"] is not None

    def test_tools_list_includes_validate_and_create(self):
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        tools = body["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "validate_flashcard" in tool_names
        assert "create_flashcard" in tool_names


class TestCreateFlashcardToolDescriptor:
    def test_create_flashcard_description_contains_write_action_warning(self):
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
        body = resp.json()
        tools = {t["name"]: t for t in body["result"]["tools"]}
        desc = tools["create_flashcard"]["description"].lower()
        assert any(word in desc for word in ["write", "creates", "irreversible", "confirm", "only call"])

    def test_create_flashcard_input_schema_is_object_with_properties(self):
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
        body = resp.json()
        tools = {t["name"]: t for t in body["result"]["tools"]}
        schema = tools["create_flashcard"]["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_validate_flashcard_input_schema_is_object_with_properties(self):
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
        body = resp.json()
        tools = {t["name"]: t for t in body["result"]["tools"]}
        schema = tools["validate_flashcard"]["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema


class TestValidateFlashcardToolCall:
    def test_validate_flashcard_call_returns_ok_status(self):
        resp = client.post(
            "/mcp",
            json=jsonrpc(
                "tools/call",
                {
                    "name": "validate_flashcard",
                    "arguments": {
                        "japanese": "写真[しゃしん]を撮りました。",
                        "english": "I took a photo.",
                    },
                },
            ),
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        content = body["result"]["content"]
        assert len(content) > 0
        result = json.loads(content[0]["text"])
        assert result["status"] == "ok"

    def test_validate_flashcard_normalizes_furigana(self):
        resp = client.post(
            "/mcp",
            json=jsonrpc(
                "tools/call",
                {
                    "name": "validate_flashcard",
                    "arguments": {
                        "japanese": "写真[しゃしん]を撮影[さつえい]しました。",
                    },
                },
            ),
            headers=MCP_HEADERS,
        )
        body = resp.json()
        result = json.loads(body["result"]["content"][0]["text"])
        assert result["japanese"] == " 写[しゃ] 真[しん]を 撮[さつ] 影[えい]しました。"


class TestCreateFlashcardToolCall:
    def test_create_flashcard_routes_to_shared_engine(self):
        fake_result = {
            "status": "ok",
            "note_id": 999,
            "deck": "Japanese",
            "model": "Japanese-Card",
            "japanese": "写真を撮りました。",
            "english": "I took a photo.",
            "notes": "",
            "tags": ["jp"],
            "audio_file": "x.mp3",
            "local_audio_path": "/tmp/x.mp3",
            "tts_provider": "openai",
            "tts_model": "gpt-4o-mini-tts",
            "japanese_tts": "写真を撮りました。",
        }
        with patch("flashgen.create_flashcard", return_value=fake_result):
            resp = client.post(
                "/mcp",
                json=jsonrpc(
                    "tools/call",
                    {
                        "name": "create_flashcard",
                        "arguments": {
                            "japanese": "写真を撮りました。",
                            "english": "I took a photo.",
                        },
                    },
                ),
                headers=MCP_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        content = body["result"]["content"]
        result = json.loads(content[0]["text"])
        assert result["status"] == "ok"
        assert result["note_id"] == 999

    def test_create_flashcard_anki_error_returns_error_content(self):
        import requests as req_lib

        with patch(
            "flashgen.create_flashcard",
            side_effect=req_lib.exceptions.ConnectionError("refused"),
        ):
            resp = client.post(
                "/mcp",
                json=jsonrpc(
                    "tools/call",
                    {
                        "name": "create_flashcard",
                        "arguments": {"japanese": "写真を撮りました。", "english": "test"},
                    },
                ),
                headers=MCP_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        result = body["result"]
        assert result.get("isError") is True
        error_text = result["content"][0]["text"]
        assert "anki_unavailable" in error_text


class TestMCPBearerAuth:
    def test_post_mcp_with_token_env_and_no_auth_returns_401(self, monkeypatch):
        import flashgen_mcp.app as app_module
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "secret-test-token")
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
        assert resp.status_code == 401

    def test_post_mcp_with_token_env_and_valid_bearer_returns_200(self, monkeypatch):
        import flashgen_mcp.app as app_module
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "secret-test-token")
        auth_headers = {**MCP_HEADERS, "Authorization": "Bearer secret-test-token"}
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=auth_headers)
        assert resp.status_code == 200

    def test_post_mcp_with_token_env_and_wrong_bearer_returns_401(self, monkeypatch):
        import flashgen_mcp.app as app_module
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "secret-test-token")
        auth_headers = {**MCP_HEADERS, "Authorization": "Bearer wrong-token"}
        resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=auth_headers)
        assert resp.status_code == 401


class TestCreateFlashcardRuntimeErrors:
    def test_anki_runtime_error_returns_anki_unavailable(self):
        with patch("flashgen.create_flashcard", side_effect=RuntimeError("AnkiConnect unreachable")):
            resp = client.post(
                "/mcp",
                json=jsonrpc("tools/call", {"name": "create_flashcard", "arguments": {"japanese": "テスト", "english": "test"}}),
                headers=MCP_HEADERS,
            )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is True
        assert "anki_unavailable" in result["content"][0]["text"]

    def test_missing_api_key_runtime_error_returns_missing_api_key(self):
        with patch("flashgen.create_flashcard", side_effect=RuntimeError("OPENAI_API_KEY is not set")):
            resp = client.post(
                "/mcp",
                json=jsonrpc("tools/call", {"name": "create_flashcard", "arguments": {"japanese": "テスト", "english": "test"}}),
                headers=MCP_HEADERS,
            )
        result = resp.json()["result"]
        assert result["isError"] is True
        assert "missing_api_key" in result["content"][0]["text"]

    def test_generic_runtime_error_returns_engine_error(self):
        with patch("flashgen.create_flashcard", side_effect=RuntimeError("something unexpected")):
            resp = client.post(
                "/mcp",
                json=jsonrpc("tools/call", {"name": "create_flashcard", "arguments": {"japanese": "テスト", "english": "test"}}),
                headers=MCP_HEADERS,
            )
        result = resp.json()["result"]
        assert result["isError"] is True
        assert "engine_error" in result["content"][0]["text"]

    def test_unknown_tool_returns_rpc_error(self):
        resp = client.post(
            "/mcp",
            json=jsonrpc("tools/call", {"name": "nonexistent_tool", "arguments": {}}),
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body

    def test_unknown_method_returns_rpc_error(self):
        resp = client.post("/mcp", json=jsonrpc("unknown/method"), headers=MCP_HEADERS)
        assert resp.status_code == 200
        assert "error" in resp.json()
