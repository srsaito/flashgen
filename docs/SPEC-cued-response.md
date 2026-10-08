# SPEC: Japanese Cued Response note type (one-card cued response)

**Status:** Agreed — acceptance criteria live in `tests/test_cued_response.py`
**Origin:** GitHub issue #6; maintainer decisions recorded there 2026-10-08.
**Beads:** `flashgen-7mj` (parent) and its children.

---

## 1. Motivation

There is no way today to make a single card that shows a **cue** on the front
and plays a **Japanese prompt as audio**. The three existing scenarios each
miss in a different way:

| What we call it | `card_type` | Anki note type | Cards/note | Why it doesn't fit |
|---|---|---|---|---|
| Standard | `standard` (default) | Japanese Listening+Production | 2 | No prompt at all |
| Prompt-response | `standard` + `japanese_prompt`/`english_prompt` | Japanese Listening+Production (same) | 3 | The Response card is the wanted one, but Listening and Production always come with it and must be deleted by hand |
| Dialog response | `dialog_response` | Japanese Dialog Response | 1 | Front is audio only (`どう答えますか？` + `{{Audio Prompt}}`); no room for a cue |
| **Cued response (this spec)** | **`cued_response`** | **Japanese Cued Response** (new) | **1** | — |

"Prompt-response" is a *usage* of `standard`, not a `card_type`; its extra
cards cannot be switched off, because Anki generates every card the note type
defines. So the fix is a new note type that **is** that Response card alone.

`Japanese Dialog Response` was originally meant to cover this need. Its
audio-only front turned out not to, but many notes of that type already exist,
so its template **stays exactly as it is** and a new type is added instead
(maintainer's decision, 2026-10-08).

### Naming — do NOT call it `prompt_response`

**PROMPT-RESPONSE card** is already the documented name of scenario 2 in
`instructions.md`, `system_prompt.md` and the MCP server instructions: a
`standard` note with the prompt fields filled, which yields **3** cards. It is
not a `card_type` value today, but reusing the name for a new 1-card type would
make "prompt-response" mean two different things. The value is therefore
`cued_response`, and every document that gains the fourth scenario must state
the contrast explicitly: *prompt-response = 3 cards; cued-response = the
Response card alone.*

## 2. Note type definition

| Property | Value |
|---|---|
| Note type (model) name | `Japanese Cued Response` |
| Engine constant | `flashgen.CUED_MODEL_NAME = "Japanese Cued Response"` |
| Card templates | exactly **1**, named `Response` |
| Fields (in order) | `Japanese`, `English`, `Notes`, `Audio`, `Japanese Prompt`, `English Prompt`, `Audio Prompt` |
| CSS | the same CSS as `Japanese Dialog Response` |

The field set and its order are **identical to the two existing models**, so
`add_note()`'s field mapping, `search_notes`, `get_note`, `update_note`
(including audio regeneration) and `delete_notes` all work with no special
cases.

Field semantics differ from `dialog_response` in one important way: **both
English fields are free-form cues shown on the front**, and that is the point
of the type.

- `English Prompt` — a cue for the prompt. It may be a translation of
  `Japanese Prompt`, **or an instruction**, e.g. *"Listen to what is said, and
  reply in each of the three situations below."* Nothing may assume it is a
  translation.
- `English` — the cue for the answer. It may be a list, e.g.
  `1. Fine, no problem.` / `2. Getting by, with some strain.` /
  `3. It worked out in the end.`
- `Japanese Prompt` / `Audio Prompt` — the Japanese line, heard but not shown,
  on the front.
- `Japanese` / `Audio` — the answer, revealed on the back.
- `Notes` — usage notes (furigana markup allowed).

Neither English field may **ever** reach TTS.

### Card template (front)

No Japanese text (decided): English cues plus the prompt audio only.

```html
<b>きっかけ</b>：{{English Prompt}}<br>
<b>回答</b>：{{English}}<br>
<br>
<div>{{Audio Prompt}}</div>
```

Must contain `{{Audio Prompt}}`, `{{English Prompt}}` and `{{English}}`, and
must **not** render `Japanese Prompt`, `Japanese`, or their `furigana:` forms.

### Card template (back)

Prompt text first, then the answer, as on the Dialog Response back:

```html
{{FrontSide}}

<hr id=answer>

<b>きっかけ</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese Prompt}}</div>
<br>
<b>回答</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<div>{{Audio}}</div>
{{#Notes}}<div class="notes">{{furigana:Notes}}</div>{{/Notes}}
```

The English fields are not repeated on the back; `{{FrontSide}}` already
carries them.

### Programmatic model creation

As for `Japanese Dialog Response`: created via AnkiConnect `createModel` when
absent, with templates and CSS living in code as the single source of truth.
The engine gets `ensure_cued_model()` parallel to `ensure_dialog_model()` — if
`Japanese Cued Response` is in `modelNames`, do nothing; otherwise create it
with the seven fields, the CSS, and exactly one card template.

Two near-identical `ensure_*_model()` functions is the point at which the
duplication should be factored into one helper rather than copied a third time.

## 3. API surface

### Request: new `card_type` value

```
card_type: "standard" (default) | "dialog_response" | "cued_response"
```

Validation rules in `CardRequest.check_constraints`, mirrored by the engine:

- Unknown values still rejected.
- `card_type == "cued_response"` **requires a non-empty `japanese_prompt`**,
  as `dialog_response` does — there is no card without the prompt audio.
- All existing constraints unchanged.

### Engine: `create_flashcard(card_type="cued_response")`

- `"cued_response"` joins `CARD_TYPES`.
- The target model is `CUED_MODEL_NAME`; the `model_name` kwarg does not apply,
  the card_type selects the model.
- `ensure_cued_model()` runs before the readiness check, then
  `check_anki_ready(deck_name)` with `model_name=None` (the model has just been
  verified or created).
- Prompt audio (`japanese_prompt_tts` → `Audio Prompt`) and answer audio
  (`japanese_tts` → `Audio`) are generated exactly as on the existing paths.
- Missing/empty `japanese_prompt` raises a clear error.
- `standard` and `dialog_response` behavior is **bit-for-bit unchanged**.

Result dict: unchanged shape; `model` reports `Japanese Cued Response` and
`card_type` echoes `cued_response`.

### Validation — all of it applies

The maintainer's requirement: **every check FlashGen runs on the existing types
runs on this one.** Nothing below is relaxed for the new type.

- `normalize_furigana_text` and `furigana_errors()` on `japanese`,
  `japanese_prompt` and `notes` (one leading ASCII space per annotated unit; no
  non-furigana square brackets; no annotation on kana or punctuation).
- `japanese_tts` / `japanese_prompt_tts` carry no furigana markup; emphasis
  tags and line breaks never reach TTS or the audio filename stem.
- Inline emphasis: allowlisted tags only (`<b> <strong> <i> <em> <u>`),
  balanced, wrapping whole furigana units.
- No double quotes inside field values. (This one is an authoring rule carried
  by the prompts and the server instructions, not an engine check — there is no
  code-level rejection for any card_type today, so there is none to add here.)
- `japanese_prompt` / `english_prompt`: both or neither — and for this
  card_type, both required in practice, since both are shown.
- Tags always include `auto`; deck-not-found lists the available decks.
- A failed TTS call (e.g. the 10-requests-per-minute 429) must not leave a
  half-created note.

### MCP tools & prompts

- `_CARD_INPUT_SCHEMA`'s `card_type` enum gains `cued_response`, and its
  description says when to choose it: over `dialog_response` when a cue is
  needed on the front, over standard prompt-response when the Listening and
  Production cards are not wanted.
- `_SERVER_INSTRUCTIONS` says "three card scenarios" — it becomes four.
- `instructions.md`, `system_prompt.md` and the README card-scenario list gain
  the fourth scenario with the 3-cards-vs-1-card contrast spelled out.

## 4. Motivating cases (regression examples)

1. Prompt 「台風すごかったね。大丈夫？」; `english_prompt` "That typhoon was
   something. Are you OK? (Answer three ways.)"; `english` three numbered
   cases; `japanese` three numbered replies (大丈夫です／なんとか大丈夫です／
   なんとかなりました). Today this needs the 3-card note type and leaves two
   cards to delete by hand.
2. Sentence chains where the next sentence's English is the cue.

## 5. Acceptance criteria

Executable form: `tests/test_cued_response.py`. Summary:

1. **Schema**: `cued_response` accepted; unknown values rejected;
   `cued_response` without `japanese_prompt` rejected; `standard` and
   `dialog_response` unaffected.
2. **Model definition**: when absent, `createModel` is called with exactly
   **1** card template and the seven fields in order; the front references
   `{{English Prompt}}`, `{{English}}` and `{{Audio Prompt}}` and no Japanese
   text field; the back references prompt text, answer text and answer audio.
   When present, `createModel` is not called.
3. **Engine**: `create_flashcard(card_type="cued_response", ...)` adds a note
   with `modelName == "Japanese Cued Response"`, populates the prompt fields
   and both audio fields, and reports `model`/`card_type`; missing
   `japanese_prompt` raises; `standard` keeps the legacy model.
4. **Validation**: the full list in §3 is exercised for this card_type,
   including a stray-bracket case in `notes` and a missing-`japanese_prompt`
   case, and including that neither English field reaches TTS.
5. **MCP**: the tool input schema exposes the new enum value; `/validate`
   accepts a cued payload and rejects one without a prompt; `/create` forwards
   `card_type` to the engine.
6. **Collection**: `search_notes` finds the new model's notes by the default
   furigana-stripped match; `update_note` regenerates `Audio` / `Audio Prompt`
   on text change.
7. **Regression**: the existing suite stays green.
8. **Integration** (gated on `ANKI_CONNECT_URL` + a real TTS key): a
   `cued_response` note yields **exactly 1 card**; a `standard` note with a
   prompt still yields 3.

One deliberate edit to an existing test file is required:
`tests/test_dialog_response.py::TestMcpDialogSurface::test_tool_input_schema_exposes_card_type_enum`
asserts the enum is *exactly* `{"standard", "dialog_response"}`. That becomes a
subset assertion. The dialog loop it guarded is finished, and no behavioral
assertion in that file changes.

## 6. Human review gate

Tests can't judge whether the card *feels* right. After the suite goes green,
create a few `cued_response` notes from the motivating cases, review on mobile
(audio autoplay, cue legibility, furigana rendering, dark mode), then merge.

## 7. Out of scope

- Changing the `Japanese Dialog Response` template.
- Basic / front-back notes with embedded audio — GitHub #3 / `flashgen-dyb`.
