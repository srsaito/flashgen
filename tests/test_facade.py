"""What `import flashgen` is allowed to expose (flashgen-76w).

After the engine moved to flashgen_core (flashgen-8ik), flashgen.py is the CLI
entry point plus a re-export block. A re-export is the same object, so reading
or calling a name through it is exact — but REBINDING one is not. Patching
`flashgen.anki_invoke` rebinds the name in flashgen's namespace while the engine
resolves it in flashgen_core.anki, so the mock is silently ignored and the test
makes real network calls instead of failing.

These tests pin the line: collaborators are absent so that a stale patch target
raises immediately, and the names the CLI needs stay present.
"""
import flashgen
import flashgen_core.anki
import flashgen_core.audio
import flashgen_core.config
import flashgen_core.furigana
import flashgen_core.service


# Side-effecting collaborators the engine calls internally. Patching any of
# these on `flashgen` would bind a name nothing reads, so they must not resolve
# there at all. The module to patch instead is in the message below.
COLLABORATORS = {
    "anki_invoke": "flashgen_core.anki",
    "store_media_file": "flashgen_core.anki",
    "check_anki_ready": "flashgen_core.anki",
    "get_model_field_names": "flashgen_core.anki",
    "ensure_generated_model": "flashgen_core.anki",
    "add_note": "flashgen_core.anki",
    "can_add_note": "flashgen_core.anki",
    "find_existing_notes": "flashgen_core.anki",
    "get_notes_info": "flashgen_core.anki",
    "search_notes": "flashgen_core.anki",
    "get_note": "flashgen_core.anki",
    "delete_notes": "flashgen_core.anki",
    "list_decks": "flashgen_core.anki",
    "list_tags": "flashgen_core.anki",
    # a constant, but the live-Anki tests rebind it, so it belongs here too
    "ANKI_CONNECT_URL": "flashgen_core.anki",
    "generate_tts_file": "flashgen_core.audio",
    "generate_openai_tts_file": "flashgen_core.audio",
    "generate_gemini_tts_file": "flashgen_core.audio",
    "write_wave_file": "flashgen_core.audio",
    "fill_missing_translation": "flashgen_core.service",
    "update_note": "flashgen_core.service",
}

# Constants and pure functions: only ever read or called, never rebound, so
# re-exporting them cannot mislead anyone.
SAFE_REEXPORTS = [
    "CARD_TYPES",
    "DECK_NAME",
    "DEFAULT_TAGS",
    "MODEL_NAME",
    "OUTPUT_DIR",
    "TTSConfig",
    "furigana_errors",
    "markup_errors",
    "markup_warnings",
    "normalize_furigana_text",
    "read_secret",
    "resolve_tts_config",
    "resolve_tts_input",
    "rich_text_to_html",
]


class TestFacadeOmitsCollaborators:
    def test_no_collaborator_resolves_on_the_facade(self):
        present = sorted(n for n in COLLABORATORS if hasattr(flashgen, n))
        assert not present, (
            "These are back on the flashgen facade: "
            + ", ".join(f"{n} (patch {COLLABORATORS[n]}.{n})" for n in present)
            + ". Re-exporting a collaborator makes patch(\"flashgen.<name>\") "
            "resolve but intercept nothing, so a test using it silently makes "
            "real network calls. Patch it where its caller looks it up."
        )

    def test_each_collaborator_is_reachable_in_its_own_module(self):
        """The names are not gone, just addressed where their callers read them."""
        for name, module_path in COLLABORATORS.items():
            module = {
                "flashgen_core.anki": flashgen_core.anki,
                "flashgen_core.audio": flashgen_core.audio,
                "flashgen_core.service": flashgen_core.service,
            }[module_path]
            assert hasattr(module, name), f"{module_path}.{name} is missing"


class TestFacadeKeepsWhatTheCliNeeds:
    def test_create_flashcard_stays_on_the_facade(self):
        """main() calls it, which also makes patch.object(flashgen,
        "create_flashcard") the correct target for CLI tests."""
        assert flashgen.create_flashcard is flashgen_core.service.create_flashcard

    def test_cli_entry_point_is_callable(self):
        assert callable(flashgen.main)

    def test_safe_reexports_are_the_same_objects(self):
        missing = [n for n in SAFE_REEXPORTS if not hasattr(flashgen, n)]
        assert not missing, f"facade lost: {missing}"
        # identity, not equality: a re-export must never become a copy
        assert flashgen.MODEL_NAME is flashgen_core.config.MODEL_NAME
        assert flashgen.rich_text_to_html is flashgen_core.furigana.rich_text_to_html
        assert flashgen.resolve_tts_config is flashgen_core.audio.resolve_tts_config
