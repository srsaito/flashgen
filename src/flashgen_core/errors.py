"""The structured error contract shared by both front doors (flashgen-yya).

Every failure the CLI or the MCP server reports carries a stable machine key:

    {"error": "<code>", "message": "<human text>"}

The engine raises a typed FlashGenError carrying that code, and both front
doors call `classify()` to turn any exception into the pair. One classifier,
one set of codes — so the two doors cannot drift apart.

⭐ WHY TYPED, NOT STRING-SNIFFED: classification used to be substring matching
on the display message, and add_note's duplicate message begins "AnkiConnect
says this note cannot be added." — so a duplicate card was reported as an Anki
outage and the caller retried forever instead of being told the card already
exists (flashgen-oxs caught this). The exception's TYPE is checked first and
always wins; the substring rules below are a last resort for plain RuntimeErrors
raised outside the engine's typed paths, never a fallback the engine relies on.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

# Error codes. Keep this list and CODE_HTTP_STATUS in step.
ANKI_UNAVAILABLE = "anki_unavailable"
DECK_MISSING = "deck_missing"
NOTE_TYPE_MISSING = "note_type_missing"
DUPLICATE_NOTE = "duplicate_note"
MISSING_API_KEY = "missing_api_key"
VALIDATION_ERROR = "validation_error"
ENGINE_ERROR = "engine_error"


class FlashGenError(RuntimeError):
    """An engine failure that knows its own contract code.

    Subclasses RuntimeError so that `except RuntimeError` at every existing
    call site — and in third-party code — keeps working unchanged.
    """

    code = ENGINE_ERROR


class AnkiUnavailable(FlashGenError):
    """AnkiConnect could not be reached at all."""

    code = ANKI_UNAVAILABLE


class DeckMissing(FlashGenError):
    """The target deck does not exist in the collection."""

    code = DECK_MISSING


class NoteTypeMissing(FlashGenError):
    """The required note type does not exist in the collection."""

    code = NOTE_TYPE_MISSING


class DuplicateNote(FlashGenError):
    """Anki refuses the note because an equivalent one already exists."""

    code = DUPLICATE_NOTE


class MissingApiKey(FlashGenError):
    """A provider API key the request needs is not configured."""

    code = MISSING_API_KEY


class ValidationError(FlashGenError):
    """The caller's input is wrong — nothing was attempted against Anki."""

    code = VALIDATION_ERROR


# HTTP status for each code, used by the REST front door. A caller mistake is a
# 4xx; an outage or missing credential is a 503 the caller can retry after.
CODE_HTTP_STATUS = {
    ANKI_UNAVAILABLE: 503,
    MISSING_API_KEY: 503,
    DUPLICATE_NOTE: 409,
    DECK_MISSING: 400,
    NOTE_TYPE_MISSING: 400,
    VALIDATION_ERROR: 400,
    ENGINE_ERROR: 400,
}

# Last-resort rules for untyped RuntimeErrors — see the module docstring. Order
# matters: the API-key rule is checked before the Anki ones because a missing
# key can be reported while talking about Anki.
_MISSING_KEY_MARKERS = ("_API_KEY",)
_ANKI_DOWN_PHRASES = (
    "Could not connect to Anki",
    "AnkiConnect unreachable",
    "AnkiConnect error",
    "AnkiConnect",
)


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str

    @property
    def http_status(self) -> int:
        return CODE_HTTP_STATUS.get(self.code, 400)

    def payload(self) -> dict[str, str]:
        """The wire shape both front doors emit."""
        return {"error": self.code, "message": self.message}


def classify(exc: BaseException, default: str = ENGINE_ERROR) -> ErrorInfo:
    """Map any exception to its contract code and human message.

    `default` is the code for an exception nothing else explains — the MCP
    front door passes validation_error for failures raised while parsing a
    tool's arguments, where the caller's input is the only suspect.
    """
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, FlashGenError):
        return ErrorInfo(exc.code, message)

    # requests' own failures: the engine wraps these, but a mocked or
    # third-party caller can let one through.
    if isinstance(exc, requests.exceptions.RequestException):
        return ErrorInfo(ANKI_UNAVAILABLE, message)

    if isinstance(exc, RuntimeError):
        if any(m in message for m in _MISSING_KEY_MARKERS) and "is not set" in message:
            return ErrorInfo(MISSING_API_KEY, message)
        if any(p in message for p in _ANKI_DOWN_PHRASES):
            return ErrorInfo(ANKI_UNAVAILABLE, message)
        return ErrorInfo(ENGINE_ERROR, message)

    return ErrorInfo(default, message)
