# SPEC: Japanese Dialog Response note type (single-card 場面 response)

**Status:** Draft — acceptance criteria live in `tests/test_dialog_response.py`
**Origin:** Loop-development pilot. This spec is written first; the tests are the
executable success criteria; the implementation is built by an agent iterating
until the tests pass.

---

## 1. Motivation

The existing note type (`Japanese Listening+Production`) produces up to three
cards per note. Its optional **Response card** (Card 3) already covers
situational responses, but it is bundled with the Listening and Production
cards, and its front *shows the prompt text*.

This feature adds a dedicated **single-card** note type for internalizing the
pragmatically appropriate response to a spoken situation (場面):

- **Front: prompt audio only.** No prompt text. The learner must (a) parse the
  audio (listening comprehension) and (b) produce the appropriate response
  (pragmatic competence). Compact framing: *audio-only prompt → pragmatically
  appropriate response*.
- **Back: prompt text + response.** Showing the prompt text on the back lets
  the learner verify they actually heard the prompt correctly rather than
  guessing from partial comprehension — a self-check on both stacked skills.

Exactly **one card** is generated per note. This is the key behavioral
difference from the default note type and the headline acceptance test.

## 2. Note type definition

| Property | Value |
|---|---|
| Note type (model) name | `Japanese Dialog Response` |
| Engine constant | `flashgen.DIALOG_MODEL_NAME = "Japanese Dialog Response"` |
| Card templates | exactly **1**, named `Response` |
| Fields (in order) | `Japanese`, `English`, `Notes`, `Audio`, `Japanese Prompt`, `English Prompt`, `Audio Prompt` |

The field set is **identical to the existing note type** so that
`add_note()`'s field mapping (`src/flashgen.py:455-464`) is reused unchanged.
Field semantics:

- `Japanese Prompt` / `Audio Prompt` / `English Prompt` — the situational line
  the learner hears (and its audio / English gloss).
- `Japanese` / `Audio` / `English` — the appropriate response.
- `Notes` — usage/register notes (furigana markup allowed).

### Card template (front)

Must contain `{{Audio Prompt}}` (autoplays) and a fixed instruction line
(e.g. どう答えますか？). Must **not** render `Japanese Prompt`,
`English Prompt`, `Japanese`, or `English` — no text that reveals either the
prompt or the answer.

### Card template (back)

Must reveal, in this order:

1. `{{furigana:Japanese Prompt}}` — what was actually said (self-check),
   with `{{English Prompt}}` shown if non-empty
2. `{{furigana:Japanese}}` — the response, with `{{Audio}}` (response audio)
3. `{{English}}` — response gloss
4. `{{furigana:Notes}}` — shown only if non-empty

Styling: consistent with the existing note type's templates (see README "Add
Card Templates"); dark-mode friendly. Exact CSS is implementer's choice.

### Programmatic model creation (new capability)

Unlike the legacy note type (created manually in Anki), this model is created
**programmatically via AnkiConnect `createModel`** when absent:

- New engine function `flashgen.ensure_dialog_model()`:
  - If `Japanese Dialog Response` is already in `modelNames`, do nothing.
  - Otherwise call `createModel` with the 7 fields above, CSS, and exactly
    one card template.
- Template HTML/CSS therefore live **in code** (single source of truth),
  making the feature self-contained, testable, and deployable to the headless
  container (the model syncs to AnkiWeb like any other collection change).
- The legacy model remains manually managed — out of scope.

## 3. API surface

### Request: new `card_type` field

Add to `CardRequest` (`src/flashgen_mcp/schema.py`) and
`_CARD_INPUT_SCHEMA` (`src/flashgen_mcp/app.py`):

```
card_type: "standard" (default) | "dialog_response"
```

Validation rules (in `CardRequest.check_constraints` and mirrored by the
engine):

- Unknown `card_type` values are rejected.
- `card_type == "dialog_response"` **requires a non-empty `japanese_prompt`**
  (the prompt audio is the entire front of the card). v1 does not
  auto-translate `english_prompt` → `japanese_prompt`; that is a listed
  follow-up.
- All existing constraints unchanged (`japanese`/`english` at-least-one,
  TTS provider/model pairing).

### Engine: `create_flashcard(card_type=...)`

`flashgen.create_flashcard()` gains keyword arg `card_type: str = "standard"`.

When `card_type == "dialog_response"`:

- The target model is `DIALOG_MODEL_NAME` (the `model_name` kwarg default no
  longer applies; the card_type selects the model).
- `ensure_dialog_model()` runs before readiness checks so a missing model is
  created, not a hard error (deck existence is still checked as today).
- Prompt audio (`japanese_prompt_tts` → `Audio Prompt`) and response audio
  (`japanese_tts` → `Audio`) are generated exactly as for the existing
  Response card path.
- Missing/empty `japanese_prompt` raises a clear error.

When `card_type == "standard"` (or omitted): behavior is **bit-for-bit
unchanged** — existing tests are the regression suite.

Result dict / `CardResult`: unchanged shape, plus `card_type` echoed;
`model` reports `Japanese Dialog Response`.

### MCP tools & prompts

- `validate_flashcard` accepts and echoes `card_type`, applying the rules
  above (still never touches Anki).
- `create_flashcard` tool schema documents when to use `dialog_response`
  ("internalize the appropriate response to a spoken situation").
- `system_prompt.md` / `instructions.md` (the LLM-facing JSON contract) are
  updated so the assistant knows to emit `card_type: "dialog_response"` for
  situational-response practice.
- CLI (`flashgen.py main()`) passes `card_type` through from the JSON payload.

## 4. Acceptance criteria

Executable form: `tests/test_dialog_response.py`. **The implementing agent
must not modify that file** (loop guardrail — verify with
`git diff tests/test_dialog_response.py` before accepting any iteration).
Summary:

1. **Schema**: `card_type` defaults to `standard`; `dialog_response` accepted;
   unknown values rejected; `dialog_response` without `japanese_prompt`
   rejected.
2. **Model definition**: when the model is absent, `createModel` is called
   with exactly **1** card template and the 7 fields; front references
   `{{Audio Prompt}}` and no prompt/answer text fields; back references
   prompt text, response text, and response audio. When present, `createModel`
   is not called.
3. **Engine**: `create_flashcard(card_type="dialog_response", ...)` adds a
   note with `modelName == "Japanese Dialog Response"`, populates all three
   prompt fields, and returns `model`/`card_type` accordingly; missing
   `japanese_prompt` raises.
4. **MCP**: `/validate` accepts a dialog payload and rejects one without a
   prompt; `/create` forwards `card_type` to the engine; the MCP tool input
   schema exposes the `card_type` enum.
5. **Regression**: `card_type` omitted → existing model, existing behavior
   (existing test suite must stay green).
6. **Integration** (gated on `ANKI_CONNECT_URL`, real TTS key): creating a
   dialog note yields **exactly 1 card** in Anki (`findCards nid:...`);
   a standard note with a prompt still yields 3 cards.

Run: `uv run pytest` (unit); integration additionally needs
`ANKI_CONNECT_URL` + a Gemini or OpenAI key.

## 5. Human review gate (post-loop)

Tests can't judge whether the card *feels* right. After the loop goes green:
create 3–5 sample dialog notes, review on mobile (audio autoplay, furigana
rendering, dark mode), then merge.

## 6. Follow-ups (out of scope for v1)

- Auto-translate `english_prompt` → `japanese_prompt` when only English is
  given (mirrors existing `fill_missing_translation`).
- A/B template variant experiments (e.g. show 場面 context hint text on front).
- Programmatic management of the legacy note type via the same
  `createModel`/`updateModelTemplates` machinery.
