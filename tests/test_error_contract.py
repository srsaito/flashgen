"""Phase 10 TDD (flashgen-oxs): the structured error contract both front doors owe.

RED BEFORE flashgen-yya. Every failure mode below must answer with
`{"error": "<key>", "message": "<human text>"}` — the same key from the CLI and
from the MCP server — and /health must name *why* it is unhealthy.

What is deliberately NOT mocked: the engine functions that PRODUCE the error
messages. Only `flashgen_core.anki.anki_invoke` (and the TTS/media side effects)
are faked, so `check_anki_ready` and `add_note` raise their real text and the
classifier is tested against the strings production actually emits — not against
strings a test invented. Mock targets follow the convention in memory
`flashgen-test-mock-targets-after-engine-split`: patch a collaborator in the
module that DEFINES it.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import flashgen
import flashgen_core.anki
from flashgen_mcp.app import app

DECK = "日本語-Soso"
MODEL = "Japanese Listening+Production"

# The legacy note type's field order (Audio before Notes) — see memory
# flashgen-generated-note-type-field-order.
MODEL_FIELDS = [
    "Japanese",
    "English",
    "Audio",
    "Notes",
    "Japanese Prompt",
    "English Prompt",
    "Audio Prompt",
]

CARD = {
    "japanese": " 写真[しゃしん]を撮りました。",
    "english": "I took a photo.",
    "deck": DECK,
}

ANKI_DOWN = RuntimeError(
    "Could not connect to AnkiConnect at http://127.0.0.1:8765. "
    "Make sure Anki is open and the AnkiConnect add-on is installed and enabled."
)


# ---------------------------------------------------------------------------
# Fake AnkiConnect
# ---------------------------------------------------------------------------
def fake_anki(
    *,
    deck_names: list[str] | None = None,
    model_names: list[str] | None = None,
    can_add: bool = True,
    existing_ids: list[int] | None = None,
):
    """An `anki_invoke` stand-in answering the actions create_flashcard makes."""
    deck_names = [DECK] if deck_names is None else deck_names
    model_names = [MODEL] if model_names is None else model_names
    existing_ids = [1700000000001] if existing_ids is None else existing_ids

    def _invoke(action: str, params: dict | None = None) -> object:
        if action == "version":
            return 6
        if action == "deckNames":
            return deck_names
        if action == "modelNames":
            return model_names
        if action == "modelFieldNames":
            return MODEL_FIELDS
        if action == "canAddNotes":
            return [can_add]
        if action == "findNotes":
            return existing_ids
        if action == "notesInfo":
            return [
                {
                    "noteId": nid,
                    "modelName": MODEL,
                    "tags": ["auto"],
                    "fields": {name: {"value": "", "order": i} for i, name in enumerate(MODEL_FIELDS)},
                }
                for nid in existing_ids
            ]
        if action == "addNote":
            return 1700000000999
        if action == "storeMediaFile":
            return "audio.mp3"
        raise AssertionError(f"unexpected AnkiConnect action in this test: {action}")

    return _invoke


def no_side_effects():
    """Patch out TTS synthesis and media upload; neither is under test here."""
    return (
        patch("flashgen_core.audio.generate_tts_file", return_value=None),
        patch("flashgen_core.anki.store_media_file", return_value="audio.mp3"),
    )


# ---------------------------------------------------------------------------
# Front doors
# ---------------------------------------------------------------------------
def cli_create(payload: dict, monkeypatch, capsys) -> dict:
    """Run `flashgen` as the CLI does and return its parsed JSON output."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as excinfo:
        flashgen.main()
    assert excinfo.value.code == 1
    return json.loads(capsys.readouterr().out)


def mcp_tool(name: str, args: dict) -> dict:
    """Call an MCP tool over /mcp and return the tool result (error payload parsed)."""
    response = TestClient(app).post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    payload = json.loads(result["content"][0]["text"])
    payload["_isError"] = result.get("isError", False)
    return payload


def http_create(payload: dict) -> tuple[int, dict]:
    response = TestClient(app).post("/create", json=payload)
    return response.status_code, response.json()


def assert_error(body: dict, key: str) -> None:
    assert body.get("error") == key, f"expected error {key!r}, got {body!r}"
    assert body.get("message"), f"error {key!r} carries no message: {body!r}"
    assert isinstance(body["message"], str)


# ---------------------------------------------------------------------------
# anki_unavailable
# ---------------------------------------------------------------------------
class TestAnkiUnavailable:
    def test_cli_reports_anki_unavailable(self, monkeypatch, capsys) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", side_effect=ANKI_DOWN):
            body = cli_create(CARD, monkeypatch, capsys)

        assert_error(body, "anki_unavailable")
        # The CLI envelope keeps its status field alongside the new error key.
        assert body.get("status") == "error"

    def test_cli_reports_anki_unavailable_on_a_real_refused_connection(
        self, monkeypatch, capsys
    ) -> None:
        # The un-wrapped failure: requests itself refuses, so anki_invoke's own
        # ConnectionError handler produces the message.
        with patch(
            "flashgen_core.anki.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            body = cli_create(CARD, monkeypatch, capsys)

        assert_error(body, "anki_unavailable")

    def test_mcp_create_tool_reports_anki_unavailable(self) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", side_effect=ANKI_DOWN):
            body = mcp_tool("create_flashcard", CARD)

        assert body["_isError"] is True
        assert_error(body, "anki_unavailable")

    def test_mcp_read_only_tools_report_anki_unavailable(self) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", side_effect=ANKI_DOWN):
            for name, args in (
                ("list_decks", {}),
                ("list_tags", {}),
                ("search_notes", {"query": "写真"}),
                ("get_note", {"note_id": 1700000000001}),
            ):
                body = mcp_tool(name, args)
                assert body["_isError"] is True
                assert_error(body, "anki_unavailable")

    def test_http_create_reports_anki_unavailable_with_503(self) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", side_effect=ANKI_DOWN):
            status, body = http_create(CARD)

        assert status == 503
        assert_error(body, "anki_unavailable")


# ---------------------------------------------------------------------------
# deck_missing
# ---------------------------------------------------------------------------
class TestDeckMissing:
    def test_cli_reports_deck_missing(self, monkeypatch, capsys) -> None:
        with patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(deck_names=["Default", "Other"]),
        ):
            body = cli_create({**CARD, "deck": "No Such Deck"}, monkeypatch, capsys)

        assert_error(body, "deck_missing")

    def test_mcp_create_tool_reports_deck_missing(self) -> None:
        with patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(deck_names=["Default", "Other"]),
        ):
            body = mcp_tool("create_flashcard", {**CARD, "deck": "No Such Deck"})

        assert body["_isError"] is True
        assert_error(body, "deck_missing")

    def test_deck_missing_message_lists_the_available_decks(self) -> None:
        # The server instructions promise this: "If create fails with
        # deck-not-found, the error lists the available decks."
        with patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(deck_names=["Default", "Other"]),
        ):
            body = mcp_tool("create_flashcard", {**CARD, "deck": "No Such Deck"})

        assert_error(body, "deck_missing")
        assert "Default" in body["message"] and "Other" in body["message"]

    def test_a_missing_note_type_is_not_reported_as_a_missing_deck(self) -> None:
        # Both failures come out of check_anki_ready with a "... not found"
        # message; the deck classifier must not swallow the note-type case.
        # Which key this one gets is flashgen-yya's to choose — fixed here is
        # only that it is its own key and that the message names the note type.
        with patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(model_names=["Basic"]),
        ):
            body = mcp_tool("create_flashcard", CARD)

        assert body["_isError"] is True
        assert body.get("error") not in ("deck_missing", "engine_error", None)
        assert MODEL in body["message"]

    def test_http_create_reports_deck_missing_with_a_client_error(self) -> None:
        with patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(deck_names=["Default", "Other"]),
        ):
            status, body = http_create({**CARD, "deck": "No Such Deck"})

        # A bad deck name is the caller's mistake, not an outage.
        assert status == 400
        assert_error(body, "deck_missing")


# ---------------------------------------------------------------------------
# duplicate_note
# ---------------------------------------------------------------------------
class TestDuplicateNote:
    def test_cli_reports_duplicate_note(self, monkeypatch, capsys) -> None:
        tts, media = no_side_effects()
        with tts, media, patch.object(
            flashgen_core.anki, "anki_invoke", side_effect=fake_anki(can_add=False)
        ):
            body = cli_create(CARD, monkeypatch, capsys)

        assert_error(body, "duplicate_note")

    def test_mcp_create_tool_reports_duplicate_note(self) -> None:
        tts, media = no_side_effects()
        with tts, media, patch.object(
            flashgen_core.anki, "anki_invoke", side_effect=fake_anki(can_add=False)
        ):
            body = mcp_tool("create_flashcard", CARD)

        assert body["_isError"] is True
        assert_error(body, "duplicate_note")

    def test_duplicate_note_is_not_misreported_as_an_anki_outage(self) -> None:
        # The live trap: add_note's message contains the word "AnkiConnect", so
        # a substring-matching classifier calls a duplicate an outage and the
        # user retries forever instead of being told the card already exists.
        tts, media = no_side_effects()
        with tts, media, patch.object(
            flashgen_core.anki, "anki_invoke", side_effect=fake_anki(can_add=False)
        ):
            body = mcp_tool("create_flashcard", CARD)

        assert body["error"] != "anki_unavailable"
        assert_error(body, "duplicate_note")

    def test_duplicate_note_message_names_the_existing_note(self) -> None:
        tts, media = no_side_effects()
        with tts, media, patch.object(
            flashgen_core.anki,
            "anki_invoke",
            side_effect=fake_anki(can_add=False, existing_ids=[1700000000042]),
        ):
            body = mcp_tool("create_flashcard", CARD)

        assert_error(body, "duplicate_note")
        assert "1700000000042" in body["message"]

    def test_http_create_reports_duplicate_note_with_409(self) -> None:
        tts, media = no_side_effects()
        with tts, media, patch.object(
            flashgen_core.anki, "anki_invoke", side_effect=fake_anki(can_add=False)
        ):
            status, body = http_create(CARD)

        assert status == 409
        assert_error(body, "duplicate_note")


# ---------------------------------------------------------------------------
# CLI / MCP parity
# ---------------------------------------------------------------------------
FAILURES = {
    "anki_unavailable": (CARD, {"side_effect": ANKI_DOWN}),
    "deck_missing": (
        {**CARD, "deck": "No Such Deck"},
        {"side_effect": fake_anki(deck_names=["Default", "Other"])},
    ),
    "duplicate_note": (CARD, {"side_effect": fake_anki(can_add=False)}),
}


@pytest.mark.parametrize("expected_key", sorted(FAILURES))
def test_both_front_doors_return_the_same_error_key(
    expected_key, monkeypatch, capsys
) -> None:
    payload, kwargs = FAILURES[expected_key]

    tts, media = no_side_effects()
    with tts, media, patch.object(flashgen_core.anki, "anki_invoke", **kwargs):
        cli_body = cli_create(payload, monkeypatch, capsys)

    tts, media = no_side_effects()
    with tts, media, patch.object(flashgen_core.anki, "anki_invoke", **kwargs):
        mcp_body = mcp_tool("create_flashcard", payload)

    assert_error(cli_body, expected_key)
    assert_error(mcp_body, expected_key)
    assert cli_body["error"] == mcp_body["error"]


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
class TestHealthEndpoint:
    def test_health_names_the_reason_when_anki_is_down(self) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", side_effect=ANKI_DOWN):
            response = TestClient(app).get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["ok"] is False
        assert body["reason"] == "anki_unavailable"

    def test_health_names_the_reason_on_a_refused_connection(self) -> None:
        with patch(
            "flashgen_core.anki.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            response = TestClient(app).get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["ok"] is False
        assert body["reason"] == "anki_unavailable"

    def test_health_is_ok_when_anki_answers(self) -> None:
        with patch.object(flashgen_core.anki, "anki_invoke", return_value=6):
            response = TestClient(app).get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert "reason" not in body
