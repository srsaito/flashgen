"""MCP call logging and environment-driven configuration (flashgen-yya)."""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import flashgen_core.anki
from flashgen_core import config, errors
from flashgen_mcp.app import app

client = TestClient(app)

JAPANESE = " 写真[しゃしん]を撮りました。"


def call_tool(name: str, arguments: dict | None = None):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def tool_call_lines(caplog) -> list[dict]:
    return [
        json.loads(rec.message)
        for rec in caplog.records
        if rec.name == "flashgen.mcp" and '"mcp_tool_call"' in rec.message
    ]


class TestToolCallLogging:
    def test_one_line_per_call_naming_the_tool_and_outcome(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            with patch("flashgen_core.anki.list_decks", return_value=["Default"]):
                call_tool("list_decks")

        lines = tool_call_lines(caplog)
        assert len(lines) == 1
        assert lines[0]["tool"] == "list_decks"
        assert lines[0]["outcome"] == "ok"
        assert lines[0]["door"] == "mcp"
        assert isinstance(lines[0]["duration_ms"], float)

    def test_the_rest_door_is_distinguishable_in_the_log(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            with patch(
                "flashgen_core.service.create_flashcard",
                return_value={"status": "ok", "note_id": 7},
            ):
                client.post("/create", json={"japanese": JAPANESE, "english": "x"})

        (line,) = tool_call_lines(caplog)
        assert line["door"] == "rest"
        assert line["tool"] == "create_flashcard"
        assert line["note_id"] == 7

    def test_a_failure_logs_its_contract_code(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            with patch.object(
                flashgen_core.anki,
                "anki_invoke",
                side_effect=errors.AnkiUnavailable("Could not connect to AnkiConnect"),
            ):
                call_tool("list_tags")

        (line,) = tool_call_lines(caplog)
        assert line["outcome"] == "error"
        assert line["error"] == "anki_unavailable"

    def test_an_unknown_tool_is_logged_too(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            call_tool("no_such_tool", {"x": 1})

        (line,) = tool_call_lines(caplog)
        assert line["tool"] == "no_such_tool"
        assert line["outcome"] == "unknown_tool"

    def test_the_log_names_the_arguments_but_never_their_values(self, caplog) -> None:
        # ⭐ The arguments are the user's study material. Keys are diagnosis;
        # values would copy the cards into the container log.
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            call_tool(
                "validate_flashcard",
                {"japanese": JAPANESE, "english": "I took a photo.", "deck": "日本語-Soso"},
            )

        (line,) = tool_call_lines(caplog)
        assert line["args"] == ["deck", "english", "japanese"]
        assert JAPANESE not in json.dumps(line, ensure_ascii=False)
        assert "I took a photo." not in json.dumps(line, ensure_ascii=False)
        # The deck is configuration, not content, and it is what a bad-deck
        # report needs — so that one value is logged on purpose.
        assert line["deck"] == "日本語-Soso"

    def test_empty_arguments_are_not_listed(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            call_tool("validate_flashcard", {"japanese": JAPANESE, "english": "", "notes": ""})

        (line,) = tool_call_lines(caplog)
        assert line["args"] == ["japanese"]

    def test_a_successful_create_logs_the_note_id(self, caplog) -> None:
        fake = {"status": "ok", "note_id": 4242}
        with caplog.at_level(logging.INFO, logger="flashgen.mcp"):
            with patch("flashgen_core.service.create_flashcard", return_value=fake):
                call_tool("create_flashcard", {"japanese": JAPANESE, "english": "x"})

        (line,) = tool_call_lines(caplog)
        assert line["note_id"] == 4242
        assert line["status"] == "ok"

    def test_an_unhealthy_health_check_logs_the_reason(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="flashgen.mcp"):
            with patch.object(
                flashgen_core.anki,
                "anki_invoke",
                side_effect=errors.AnkiUnavailable("down"),
            ):
                client.get("/health")

        entries = [
            json.loads(rec.message)
            for rec in caplog.records
            if rec.name == "flashgen.mcp" and '"health_check"' in rec.message
        ]
        assert entries and entries[-1] == {
            "event": "health_check",
            "ok": False,
            "reason": "anki_unavailable",
        }


class TestEnvHelpers:
    def test_env_str_falls_back_on_unset_and_blank(self, monkeypatch) -> None:
        monkeypatch.delenv("FLASHGEN_TEST_VAR", raising=False)
        assert config._env_str("FLASHGEN_TEST_VAR", "fallback") == "fallback"
        monkeypatch.setenv("FLASHGEN_TEST_VAR", "   ")
        assert config._env_str("FLASHGEN_TEST_VAR", "fallback") == "fallback"
        monkeypatch.setenv("FLASHGEN_TEST_VAR", "  set  ")
        assert config._env_str("FLASHGEN_TEST_VAR", "fallback") == "set"

    def test_a_bad_log_level_falls_back_to_info(self, monkeypatch, capsys) -> None:
        # A typo here must not stop the server booting.
        probe = load_config(monkeypatch, FLASHGEN_LOG_LEVEL="verbose")
        assert probe.LOG_LEVEL == "INFO"
        assert "FLASHGEN_LOG_LEVEL" in capsys.readouterr().err
        assert load_config(monkeypatch, FLASHGEN_LOG_LEVEL="warning").LOG_LEVEL == "WARNING"

    def test_env_int_keeps_the_default_on_junk(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("FLASHGEN_TEST_INT", "45")
        assert config._env_int("FLASHGEN_TEST_INT", 30) == 45
        monkeypatch.setenv("FLASHGEN_TEST_INT", "soon")
        assert config._env_int("FLASHGEN_TEST_INT", 30) == 30
        # A misconfiguration must say so rather than fail silently.
        assert "FLASHGEN_TEST_INT" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "raw,expected",
        [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
         ("0", False), ("false", False), ("nope", False), ("", None)],
    )
    def test_env_bool(self, monkeypatch, raw, expected) -> None:
        monkeypatch.setenv("FLASHGEN_TEST_BOOL", raw)
        assert config._env_bool("FLASHGEN_TEST_BOOL", False) is (
            False if expected is None else expected
        )

    def test_env_list_splits_and_trims(self, monkeypatch) -> None:
        monkeypatch.setenv("FLASHGEN_TEST_LIST", " jp , auto ,, ")
        assert config._env_list("FLASHGEN_TEST_LIST", ["x"]) == ["jp", "auto"]
        monkeypatch.setenv("FLASHGEN_TEST_LIST", "")
        assert config._env_list("FLASHGEN_TEST_LIST", ["x"]) == ["x"]


def load_config(monkeypatch, **env):
    """Execute config.py again, with `env` set, as a SEPARATE module object.

    ⭐ NOT importlib.reload(config): config reads the environment at import, so
    a reload would rebind the names every other module re-exports, and
    tests/test_facade.py's identity invariant (flashgen.MODEL_NAME IS
    flashgen_core.config.MODEL_NAME) would fail in whatever test ran next.
    Loading a fresh module under its own name leaves sys.modules untouched.
    """
    for name in env_names:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    name = "flashgen_core._config_probe"
    spec = importlib.util.spec_from_file_location(name, config.__file__)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules[cls.__module__], so
    # the probe has to be registered while it executes — under its OWN name,
    # and removed again, so flashgen_core.config is never replaced.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class TestEnvDrivenConstants:
    # These assert the wiring of the variable NAMES. Modules that bound a value
    # by name (anki.py's ANKI_CONNECT_URL) keep their own copy regardless, so
    # none of this reconfigures the running process.
    def test_every_deployment_knob_reads_its_env_var(self, monkeypatch) -> None:
        probe = load_config(
            monkeypatch,
            ANKI_CONNECT_URL="http://anki:8765",
            ANKI_CONNECT_TIMEOUT="7",
            FLASHGEN_DECK="Test-Deck",
            FLASHGEN_MODEL="Test-Model",
            FLASHGEN_DIALOG_MODEL="Test-Dialog",
            FLASHGEN_CUED_MODEL="Test-Cued",
            FLASHGEN_OUTPUT_DIR="/tmp/flashgen-out",
            FLASHGEN_DEFAULT_TAGS="a,b",
            FLASHGEN_TTS_PROVIDER="openai",
            FLASHGEN_LOG_LEVEL="debug",
            FLASHGEN_MCP_ISSUER="https://mcp.example.test",
            FLASHGEN_DEBUG="1",
            FLASHGEN_SEARCH_CANDIDATE_CAP="50",
            FLASHGEN_SEARCH_FIELD_VALUE_MAX="80",
        )
        assert probe.ANKI_CONNECT_URL == "http://anki:8765"
        assert probe.ANKI_CONNECT_TIMEOUT == 7
        assert probe.DECK_NAME == "Test-Deck"
        assert probe.MODEL_NAME == "Test-Model"
        assert probe.DIALOG_MODEL_NAME == "Test-Dialog"
        assert probe.CUED_MODEL_NAME == "Test-Cued"
        assert str(probe.OUTPUT_DIR) == "/tmp/flashgen-out"
        assert probe.DEFAULT_TAGS == ["a", "b"]
        assert probe.DEFAULT_TTS_PROVIDER == "openai"
        assert probe.LOG_LEVEL == "DEBUG"
        assert probe.MCP_ISSUER == "https://mcp.example.test"
        assert probe.DEBUG is True
        assert probe.SEARCH_CANDIDATE_CAP == 50
        assert probe.SEARCH_FIELD_VALUE_MAX == 80

    def test_the_generated_note_types_follow_the_renamed_models(self, monkeypatch) -> None:
        probe = load_config(
            monkeypatch, FLASHGEN_DIALOG_MODEL="Test-Dialog", FLASHGEN_CUED_MODEL="Test-Cued"
        )
        assert probe.GENERATED_MODELS["dialog_response"]["model_name"] == "Test-Dialog"
        assert probe.GENERATED_MODELS["cued_response"]["model_name"] == "Test-Cued"

    def test_defaults_hold_with_a_clean_environment(self, monkeypatch) -> None:
        fresh = load_config(monkeypatch)
        assert fresh.DECK_NAME == "日本語-Soso"
        assert fresh.MODEL_NAME == "Japanese Listening+Production"
        assert fresh.ANKI_CONNECT_URL == "http://127.0.0.1:8765"
        assert fresh.ANKI_CONNECT_TIMEOUT == 30
        assert fresh.DEFAULT_TAGS == ["jp", "auto", "conversation"]
        assert fresh.DEBUG is False


env_names = (
    "ANKI_CONNECT_URL",
    "ANKI_CONNECT_TIMEOUT",
    "FLASHGEN_DECK",
    "FLASHGEN_MODEL",
    "FLASHGEN_DIALOG_MODEL",
    "FLASHGEN_CUED_MODEL",
    "FLASHGEN_OUTPUT_DIR",
    "FLASHGEN_DEFAULT_TAGS",
    "FLASHGEN_TTS_PROVIDER",
    "FLASHGEN_OPENAI_TTS_MODEL",
    "FLASHGEN_OPENAI_TTS_VOICE",
    "FLASHGEN_GEMINI_TTS_MODEL",
    "FLASHGEN_GEMINI_TTS_VOICE",
    "FLASHGEN_OPENAI_TEXT_MODEL",
    "FLASHGEN_LOG_LEVEL",
    "FLASHGEN_MCP_ISSUER",
    "FLASHGEN_DEBUG",
    "FLASHGEN_SEARCH_CANDIDATE_CAP",
    "FLASHGEN_SEARCH_FIELD_VALUE_MAX",
)


class TestEffectiveConfig:
    def test_it_reports_the_resolved_values(self) -> None:
        summary = config.effective_config()
        assert summary["deck"] == config.DECK_NAME
        assert summary["anki_connect_url"] == config.ANKI_CONNECT_URL
        assert summary["log_level"] == config.LOG_LEVEL

    def test_it_carries_no_secret(self, monkeypatch) -> None:
        # This dict is logged at startup, so a credential leaking into it would
        # land in the container log.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-appear")
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-must-not-appear")
        monkeypatch.setenv("FLASHGEN_MCP_TOKEN", "token-must-not-appear")
        dumped = json.dumps(config.effective_config(), ensure_ascii=False)
        for secret in ("sk-must-not-appear", "AIza-must-not-appear", "token-must-not-appear"):
            assert secret not in dumped
        assert not any(
            "key" in name or "token" in name or "secret" in name
            for name in config.effective_config()
        )
