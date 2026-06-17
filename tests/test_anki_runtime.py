"""Integration tests for the headless Anki runtime.

Skipped (tests 1-4) unless ANKI_CONNECT_URL is set in the environment.
Set ANKI_CONNECT_URL to point at a running AnkiConnect instance, e.g.:

    ANKI_CONNECT_URL=http://localhost:8765 pytest tests/test_anki_runtime.py

In docker-compose CI the variable would be set to http://anki:8765.
These tests are written TDD-first and are expected to fail until the
anki-headless container is built and running.
"""

import os
import subprocess
import time
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import flashgen
from flashgen_mcp.app import app

_ANKI_CONNECT_URL = os.environ.get("ANKI_CONNECT_URL", "")
_DECK_NAME = os.environ.get("ANKI_DECK_NAME", flashgen.DECK_NAME)

_needs_anki = pytest.mark.skipif(
    not _ANKI_CONNECT_URL,
    reason="ANKI_CONNECT_URL not set — skipping Anki runtime integration tests",
)


def _anki_invoke(action: str, params: dict | None = None, url: str | None = None) -> object:
    target = url or _ANKI_CONNECT_URL
    payload = {"action": action, "version": 6, "params": params or {}}
    response = requests.post(target, json=payload, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error on '{action}': {data['error']}")
    return data.get("result")


def _wait_for_anki(timeout: int = 30) -> None:
    """Block until AnkiConnect responds or timeout is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _anki_invoke("version")
            return
        except Exception:
            time.sleep(1)
    pytest.fail(f"AnkiConnect at {_ANKI_CONNECT_URL} did not become ready within {timeout}s")


# ---------------------------------------------------------------------------
# 1. AnkiConnect ping
# ---------------------------------------------------------------------------

@_needs_anki
class TestAnkiConnectPing:
    def test_version_returns_integer(self) -> None:
        result = _anki_invoke("version")
        assert isinstance(result, int), f"Expected integer version, got {result!r}"
        assert result >= 6, f"Expected AnkiConnect v6+, got {result}"


# ---------------------------------------------------------------------------
# 2. Deck list
# ---------------------------------------------------------------------------

@_needs_anki
class TestAnkiDeckList:
    def test_deck_list_includes_configured_deck(self) -> None:
        deck_names = _anki_invoke("deckNames")
        assert _DECK_NAME in deck_names, (
            f"Expected deck '{_DECK_NAME}' in deck list, got: {deck_names}"
        )


# ---------------------------------------------------------------------------
# 3. Profile persistence across docker compose restart
# ---------------------------------------------------------------------------

@_needs_anki
class TestAnkiProfilePersistence:
    def test_card_count_stable_across_docker_restart(self) -> None:
        note_ids_before = _anki_invoke("findNotes", {"query": f"deck:{_DECK_NAME}"})
        count_before = len(note_ids_before)

        result = subprocess.run(
            ["docker", "compose", "restart", "anki"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"docker compose restart failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        _wait_for_anki()

        note_ids_after = _anki_invoke("findNotes", {"query": f"deck:{_DECK_NAME}"})
        count_after = len(note_ids_after)

        assert count_before == count_after, (
            f"Card count changed after docker restart: {count_before} → {count_after}. "
            "Profile persistence may not be configured correctly."
        )


# ---------------------------------------------------------------------------
# 4. Network reachability: flashgen module uses the configured URL
#
# TDD: this test fails until flashgen.py reads ANKI_CONNECT_URL from the
# environment instead of hardcoding http://127.0.0.1:8765.  In docker-compose
# the env var is set to http://anki:8765 so the module can reach the container.
# ---------------------------------------------------------------------------

@_needs_anki
class TestFlashgenMcpNetworkReachability:
    def test_flashgen_module_reads_anki_connect_url_from_env(self) -> None:
        """flashgen.ANKI_CONNECT_URL must equal the ANKI_CONNECT_URL env var."""
        assert flashgen.ANKI_CONNECT_URL == _ANKI_CONNECT_URL, (
            f"flashgen.ANKI_CONNECT_URL is '{flashgen.ANKI_CONNECT_URL}' but "
            f"ANKI_CONNECT_URL env var is '{_ANKI_CONNECT_URL}'. "
            "Update flashgen.py to read ANKI_CONNECT_URL from os.environ."
        )

    def test_flashgen_anki_invoke_reaches_anki_connect(self) -> None:
        """flashgen.anki_invoke('version') must succeed with the configured URL."""
        with patch.object(flashgen, "ANKI_CONNECT_URL", _ANKI_CONNECT_URL):
            result = flashgen.anki_invoke("version")
        assert isinstance(result, int), f"Expected integer version, got {result!r}"


# ---------------------------------------------------------------------------
# 5. Health endpoint reflects AnkiConnect liveness
#
# TDD: this test fails until /health is updated to probe AnkiConnect and
# return {"ok": false} when it is unreachable.
# ---------------------------------------------------------------------------

class TestHealthEndpointAnkiLiveness:
    def test_health_returns_ok_false_when_anki_unreachable(self) -> None:
        """Health endpoint must report degraded state when AnkiConnect is down."""
        client = TestClient(app)

        with patch.object(
            flashgen,
            "anki_invoke",
            side_effect=RuntimeError("Could not connect to AnkiConnect"),
        ):
            response = client.get("/health")

        body = response.json()
        assert body.get("ok") is False, (
            f"Expected ok=false when AnkiConnect is unreachable, got: {body}. "
            "Update /health to probe AnkiConnect and return ok=false on failure."
        )


# ---------------------------------------------------------------------------
# 6. flashgen-sync addon: headless AnkiWeb login + auto-sync bootstrap
#
# The addon runs inside Anki (needs aqt), so it can't be exercised here. This
# guards against syntax/typo regressions and confirms it wires the expected API
# before a slow container rebuild. End-to-end auth/sync is verified on the host.
# ---------------------------------------------------------------------------

import importlib.util
import py_compile
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

_DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "anki-headless"
_ADDON = _DEPLOY / "flashgen-sync" / "__init__.py"
_SEED_GEN = _DEPLOY / "seed_prefs.py"
_ENTRYPOINT = _DEPLOY / "entrypoint.sh"
_DOCKERFILE = _DEPLOY / "Dockerfile"


def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_addon_module():
    """Import the addon with aqt stubbed so its pure helpers can be unit-tested.

    The addon does `from aqt import gui_hooks, mw` / `from aqt.qt import QTimer`,
    which don't exist off-container. Inject mock modules so the file loads and we
    can call logic like _required_info() directly.
    """
    fake_aqt = types.ModuleType("aqt")
    fake_aqt.gui_hooks = MagicMock()
    fake_aqt.mw = MagicMock()
    fake_qt = types.ModuleType("aqt.qt")
    fake_qt.QTimer = MagicMock()
    with patch.dict(sys.modules, {"aqt": fake_aqt, "aqt.qt": fake_qt}):
        return _load_module_from_path("flashgen_sync_addon", _ADDON)


class TestFlashgenSyncAddon:
    def test_addon_compiles(self) -> None:
        assert _ADDON.is_file(), f"addon missing at {_ADDON}"
        py_compile.compile(str(_ADDON), doraise=True)

    def test_addon_wires_expected_api(self) -> None:
        src = _ADDON.read_text(encoding="utf-8")
        for needle in (
            "gui_hooks.profile_did_open",  # startup hook
            "sync_login",                   # programmatic AnkiWeb login
            "set_sync_key",                 # persist hkey to profile
            "sync_collection",              # background sync
            "ANKIWEB_USERNAME",
            "ANKIWEB_PASSWORD",
        ):
            assert needle in src, f"flashgen-sync addon missing expected API: {needle}"

    def test_addon_wires_empty_volume_full_download(self) -> None:
        """A wiped/empty volume must auto-seed via a one-way FULL_DOWNLOAD.

        Guards the disaster-recovery path (flashgen-2b9): the addon must gate on
        an empty local collection and pull from AnkiWeb with upload=False, never
        full-upload an empty collection (which would wipe AnkiWeb).
        """
        src = _ADDON.read_text(encoding="utf-8")
        for needle in (
            "full_upload_or_download",  # perform the full sync
            "upload=False",             # download direction (never upload-wipe)
            "card_count",               # emptiness heuristic
            "close_for_full_sync",      # full-sync prep
            "reopen",                   # bring the collection back online
        ):
            assert needle in src, f"flashgen-sync addon missing full-download API: {needle}"


class TestRequiredInfo:
    """Guards the live-discovered bug: Anki delivers the sync 'required' field as
    a RAW INT (3 = FULL_DOWNLOAD), so a `.name`-only check ('FULL' in '3') was
    False and the recovery download was skipped. _required_info must handle both
    int and enum-object forms. See flashgen-2b9."""

    def test_int_full_download_is_full(self) -> None:
        mod = _load_addon_module()
        val, name, is_full = mod._required_info(3)
        assert (val, name, is_full) == (3, "FULL_DOWNLOAD", True)

    def test_int_full_sync_and_upload_are_full(self) -> None:
        mod = _load_addon_module()
        assert mod._required_info(2)[2] is True  # FULL_SYNC
        assert mod._required_info(4)[2] is True  # FULL_UPLOAD

    def test_int_normal_and_nochanges_not_full(self) -> None:
        mod = _load_addon_module()
        assert mod._required_info(1) == (1, "NORMAL_SYNC", False)
        assert mod._required_info(0) == (0, "NO_CHANGES", False)

    def test_enum_object_with_name_attr(self) -> None:
        mod = _load_addon_module()
        enum_like = types.SimpleNamespace(name="FULL_DOWNLOAD")  # int() raises
        val, name, is_full = mod._required_info(enum_like)
        assert name == "FULL_DOWNLOAD" and is_full is True


class TestSeedPrefs:
    """The seed prefs21.db must suppress Anki's first-run language dialog (which
    blocks a wiped volume headlessly) without baking in any secret. flashgen-2b9."""

    def test_seed_generates_dialog_suppressing_db(self, tmp_path) -> None:
        import pickle
        import sqlite3

        mod = _load_module_from_path("seed_prefs", _SEED_GEN)
        db = tmp_path / "prefs21.db"
        mod.write_seed(str(db))

        con = sqlite3.connect(str(db))
        names = {r[0] for r in con.execute("SELECT name FROM profiles")}
        assert names == {"_global", "User 1"}

        meta = pickle.loads(
            con.execute("SELECT data FROM profiles WHERE name='_global'").fetchone()[0]
        )
        # defaultLang set + firstRun False are what skip the language modal.
        assert meta["defaultLang"], "defaultLang must be set to skip language dialog"
        assert meta["firstRun"] is False
        assert meta["last_loaded_profile_name"] == "User 1"

        profile = pickle.loads(
            con.execute("SELECT data FROM profiles WHERE name='User 1'").fetchone()[0]
        )
        assert profile["syncKey"] is None, "seed must not bake in a sync secret"

    def test_entrypoint_seeds_only_fresh_volume(self) -> None:
        ep = _ENTRYPOINT.read_text(encoding="utf-8")
        # Copies the seed, and only when prefs21.db is absent (never clobbers).
        assert "/opt/flashgen-seed/prefs21.db" in ep
        assert "! -f" in ep and "prefs21.db" in ep

    def test_dockerfile_builds_the_seed(self) -> None:
        df = _DOCKERFILE.read_text(encoding="utf-8")
        assert "seed_prefs.py" in df
        assert "/opt/flashgen-seed/prefs21.db" in df
