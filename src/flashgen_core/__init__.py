"""FlashGen engine: the card-generation logic shared by the CLI and the MCP
server (flashgen-8ik).

Layered bottom-up, each module importing only from the ones above it:

    config    constants, note-type definitions, secrets, debug printing
    furigana  text normalization, furigana rules, markup validation, HTML
    audio     TTS config resolution, filenames, provider calls
    anki      AnkiConnect client, note create/read/update, collection search
    service   create_flashcard / update_note orchestration

Cross-module calls to the side-effecting layers (anki, audio) are written
module-qualified — `anki.anki_invoke(...)`, not a bare imported name — so one
patch of `flashgen_core.anki.anki_invoke` intercepts every caller. Pure helpers
from config and furigana are imported by name.
"""
