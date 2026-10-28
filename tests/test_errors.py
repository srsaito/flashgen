"""Unit tests for the shared error classifier (flashgen-yya).

The contract itself is tested at the front doors in test_error_contract.py;
this covers the classifier's own rules, above all the PRECEDENCE that keeps a
duplicate from being reported as an outage.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from flashgen_core import anki, errors


class TestTypedErrorsWin:
    def test_a_typed_error_reports_its_own_code(self) -> None:
        for exc, code in (
            (errors.AnkiUnavailable("down"), "anki_unavailable"),
            (errors.DeckMissing("no deck"), "deck_missing"),
            (errors.NoteTypeMissing("no note type"), "note_type_missing"),
            (errors.DuplicateNote("already there"), "duplicate_note"),
            (errors.MissingApiKey("OPENAI_API_KEY is not set."), "missing_api_key"),
            (errors.ValidationError("bad input"), "validation_error"),
            (errors.FlashGenError("something"), "engine_error"),
        ):
            assert errors.classify(exc).code == code

    def test_the_type_beats_the_message(self) -> None:
        # add_note's real duplicate message begins with the word AnkiConnect;
        # the substring rules must never get a look at a typed error.
        exc = errors.DuplicateNote(
            "AnkiConnect says this note cannot be added.\nExisting note ids: [1]"
        )
        assert errors.classify(exc).code == "duplicate_note"

    def test_every_typed_error_is_a_runtime_error(self) -> None:
        # Existing `except RuntimeError` call sites must keep catching these.
        assert issubclass(errors.FlashGenError, RuntimeError)
        assert isinstance(errors.DeckMissing("x"), RuntimeError)


class TestUntypedFallbacks:
    def test_requests_failures_are_an_outage(self) -> None:
        for exc in (
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.Timeout("too slow"),
            requests.exceptions.HTTPError("500"),
        ):
            assert errors.classify(exc).code == "anki_unavailable"

    def test_a_plain_runtime_error_naming_anki_is_an_outage(self) -> None:
        # Kept for RuntimeErrors raised outside the engine's typed paths.
        assert (
            errors.classify(RuntimeError("Could not connect to AnkiConnect at x")).code
            == "anki_unavailable"
        )

    def test_a_missing_key_is_detected_before_the_anki_rules(self) -> None:
        exc = RuntimeError("OPENAI_API_KEY is not set. AnkiConnect is fine.")
        assert errors.classify(exc).code == "missing_api_key"

    def test_an_unexplained_runtime_error_is_an_engine_error(self) -> None:
        assert errors.classify(RuntimeError("something unexpected")).code == "engine_error"

    def test_a_non_runtime_exception_takes_the_caller_supplied_default(self) -> None:
        # The MCP door passes validation_error: a pydantic argument failure is
        # the caller's input, not an engine fault.
        exc = ValueError("2 validation errors for CardRequest")
        assert errors.classify(exc).code == "engine_error"
        assert errors.classify(exc, default=errors.VALIDATION_ERROR).code == "validation_error"

    def test_an_empty_message_falls_back_to_the_exception_class_name(self) -> None:
        info = errors.classify(RuntimeError())
        assert info.message == "RuntimeError"


class TestErrorInfo:
    def test_payload_is_the_wire_shape(self) -> None:
        info = errors.ErrorInfo(errors.DECK_MISSING, "Deck 'x' not found.")
        assert info.payload() == {"error": "deck_missing", "message": "Deck 'x' not found."}

    def test_http_status_per_code(self) -> None:
        assert errors.ErrorInfo(errors.ANKI_UNAVAILABLE, "").http_status == 503
        assert errors.ErrorInfo(errors.MISSING_API_KEY, "").http_status == 503
        assert errors.ErrorInfo(errors.DUPLICATE_NOTE, "").http_status == 409
        assert errors.ErrorInfo(errors.DECK_MISSING, "").http_status == 400
        assert errors.ErrorInfo(errors.VALIDATION_ERROR, "").http_status == 400

    def test_an_unmapped_code_is_a_client_error_not_a_crash(self) -> None:
        assert errors.ErrorInfo("something_new", "").http_status == 400

    def test_every_code_has_a_status_and_a_typed_error(self) -> None:
        codes = {
            value
            for name, value in vars(errors).items()
            if name.isupper() and isinstance(value, str) and not name.startswith("_")
        }
        assert codes == set(errors.CODE_HTTP_STATUS)
        raised = {
            cls.code
            for cls in vars(errors).values()
            if isinstance(cls, type) and issubclass(cls, errors.FlashGenError)
        }
        assert raised == codes


def fake_response(payload: dict, status: int = 200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(str(status))
    else:
        response.raise_for_status.return_value = None
    return response


class TestAnkiInvokeRaisesTypedErrors:
    """anki_invoke is where an HTTP fact becomes a contract code."""

    def test_a_refused_connection_is_anki_unavailable(self) -> None:
        with patch(
            "flashgen_core.anki.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(errors.AnkiUnavailable) as excinfo:
                anki.anki_invoke("version")
        assert "Could not connect to AnkiConnect" in str(excinfo.value)

    def test_a_timeout_is_anki_unavailable(self) -> None:
        with patch(
            "flashgen_core.anki.requests.post",
            side_effect=requests.exceptions.Timeout("too slow"),
        ):
            with pytest.raises(errors.AnkiUnavailable):
                anki.anki_invoke("version")

    def test_a_non_2xx_is_anki_unavailable_and_says_what_answered(self) -> None:
        with patch("flashgen_core.anki.requests.post", return_value=fake_response({}, 502)):
            with pytest.raises(errors.AnkiUnavailable) as excinfo:
                anki.anki_invoke("version")
        message = str(excinfo.value)
        assert "502" in message and "version" in message
        # It did answer, so don't claim nothing was reachable.
        assert "Could not connect" not in message

    def test_ankis_own_duplicate_complaint_is_a_duplicate(self) -> None:
        # addNote can be the one to notice, without a canAddNotes check first.
        payload = {"result": None, "error": "cannot create note because it is a duplicate"}
        with patch("flashgen_core.anki.requests.post", return_value=fake_response(payload)):
            with pytest.raises(errors.DuplicateNote):
                anki.anki_invoke("addNote", {"note": {}})

    def test_any_other_ankiconnect_error_stays_an_engine_error(self) -> None:
        payload = {"result": None, "error": "model was not found: Basic"}
        with patch("flashgen_core.anki.requests.post", return_value=fake_response(payload)):
            with pytest.raises(RuntimeError) as excinfo:
                anki.anki_invoke("addNote", {"note": {}})
        assert not isinstance(excinfo.value, errors.FlashGenError)
        # It reaches the caller as anki_unavailable through the legacy phrase
        # rule, which is the pre-existing behavior for a protocol-level fault.
        assert errors.classify(excinfo.value).code == "anki_unavailable"
