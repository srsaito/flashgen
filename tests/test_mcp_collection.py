"""MCP-layer tests for the collection tools (GH issue #1)."""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from flashgen_mcp.app import app

client = TestClient(app)

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

READ_TOOLS = ["search_notes", "get_note", "list_decks", "list_tags"]
WRITE_TOOLS = ["delete_notes", "update_note"]


def jsonrpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def call_tool(name, arguments=None):
    resp = client.post(
        "/mcp",
        json=jsonrpc("tools/call", {"name": name, "arguments": arguments or {}}),
        headers=MCP_HEADERS,
    )
    assert resp.status_code == 200
    return resp.json()["result"]


def tool_text(result):
    return json.loads(result["content"][0]["text"])


def list_tools():
    resp = client.post("/mcp", json=jsonrpc("tools/list"), headers=MCP_HEADERS)
    return {t["name"]: t for t in resp.json()["result"]["tools"]}


class TestToolDescriptors:
    def test_all_collection_tools_listed(self):
        tools = list_tools()
        for name in READ_TOOLS + WRITE_TOOLS:
            assert name in tools, name

    def test_read_tools_marked_read_only(self):
        tools = list_tools()
        for name in READ_TOOLS:
            assert tools[name]["annotations"]["readOnlyHint"] is True, name

    def test_write_tools_marked_destructive_not_read_only(self):
        tools = list_tools()
        for name in WRITE_TOOLS:
            annotations = tools[name]["annotations"]
            assert annotations["readOnlyHint"] is False, name
            assert annotations["destructiveHint"] is True, name

    def test_write_tool_descriptions_warn_irreversible(self):
        tools = list_tools()
        for name in WRITE_TOOLS:
            desc = tools[name]["description"].lower()
            assert "write action" in desc, name
            assert "irreversible" in desc, name

    def test_search_notes_description_documents_anki_syntax(self):
        desc = list_tools()["search_notes"]["description"]
        for token in ("deck:", "tag:", "nid:", "quoted"):
            assert token in desc, token

    def test_search_notes_schema_has_expected_properties(self):
        schema = list_tools()["search_notes"]["inputSchema"]
        assert schema["type"] == "object"
        assert set(schema["properties"]) == {"query", "deck", "limit", "match_field"}
        assert schema["properties"]["match_field"]["enum"] == [
            "japanese_tts", "japanese", "english", "any",
        ]


class TestSearchNotesCall:
    def test_arguments_forwarded_and_result_returned(self):
        fake = {"count": 1, "truncated": False, "query": "q", "notes": [{"note_id": 1}]}
        with patch("flashgen.search_notes", return_value=fake) as mock_search:
            result = call_tool(
                "search_notes",
                {"query": "掃除機", "deck": "日本語-Soso", "limit": 5, "match_field": "any"},
            )
        assert tool_text(result)["count"] == 1
        mock_search.assert_called_once_with(
            query="掃除機", deck="日本語-Soso", limit=5, match_field="any"
        )

    def test_missing_query_and_deck_is_validation_error(self):
        result = call_tool("search_notes", {})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "validation_error"

    def test_anki_unavailable_maps_to_structured_error(self):
        err = RuntimeError("Could not connect to AnkiConnect at http://x")
        with patch("flashgen.search_notes", side_effect=err):
            result = call_tool("search_notes", {"query": "猫"})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "anki_unavailable"


class TestGetNoteCall:
    def test_forwards_note_id(self):
        with patch("flashgen.get_note", return_value={"note_id": 42}) as mock_get:
            result = call_tool("get_note", {"note_id": 42})
        assert tool_text(result)["note_id"] == 42
        mock_get.assert_called_once_with(42)

    def test_missing_note_id_is_validation_error(self):
        result = call_tool("get_note", {})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "validation_error"

    def test_unknown_note_id_is_engine_error(self):
        with patch("flashgen.get_note", side_effect=RuntimeError("Note 999 not found.")):
            result = call_tool("get_note", {"note_id": 999})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "engine_error"


class TestListCalls:
    def test_list_decks(self):
        with patch("flashgen.list_decks", return_value=["Default", "日本語-Soso"]):
            result = call_tool("list_decks")
        assert tool_text(result) == ["Default", "日本語-Soso"]

    def test_list_tags(self):
        with patch("flashgen.list_tags", return_value=["jp", "auto"]):
            result = call_tool("list_tags")
        assert tool_text(result) == ["jp", "auto"]


class TestDeleteNotesCall:
    def test_forwards_ids(self):
        fake = {"status": "ok", "deleted": [1], "missing": []}
        with patch("flashgen.delete_notes", return_value=fake) as mock_delete:
            result = call_tool("delete_notes", {"note_ids": [1]})
        assert tool_text(result)["deleted"] == [1]
        mock_delete.assert_called_once_with([1])

    def test_empty_ids_is_validation_error(self):
        result = call_tool("delete_notes", {"note_ids": []})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "validation_error"


class TestUpdateNoteCall:
    def test_forwards_fields_tags_and_tts(self):
        with patch("flashgen.update_note", return_value={"status": "ok"}) as mock_update:
            call_tool(
                "update_note",
                {
                    "note_id": 5,
                    "fields": {"japanese": "猫"},
                    "tags": ["jp"],
                    "tts_provider": "openai",
                    "tts_model": "gpt-4o-mini-tts",
                },
            )
        mock_update.assert_called_once_with(
            5,
            fields={"japanese": "猫"},
            tags=["jp"],
            tts_provider="openai",
            tts_model="gpt-4o-mini-tts",
        )

    def test_no_fields_or_tags_is_validation_error(self):
        result = call_tool("update_note", {"note_id": 5})
        assert result["isError"] is True
        assert tool_text(result)["error"] == "validation_error"

    def test_tts_provider_without_model_is_validation_error(self):
        result = call_tool(
            "update_note",
            {"note_id": 5, "fields": {"japanese": "猫"}, "tts_provider": "openai"},
        )
        assert result["isError"] is True
        assert tool_text(result)["error"] == "validation_error"
