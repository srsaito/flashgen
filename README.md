# flashgen

Automatically create Japanese Anki flashcards with AI-generated audio — directly from a ChatGPT conversation.

---

## What it does

`flashgen` takes a Japanese phrase (or English — it auto-translates), generates provider-selectable TTS audio, and creates an Anki flashcard with audio in your deck — all in one command.

The project now has two front doors over the same card-generation engine:

- the local CLI workflow through `flashgen.py`
- the MCP/server workflow under `src/flashgen_mcp/`

The shared design goal is that improvements to translation, furigana normalization, audio generation, and Anki card creation benefit both workflows.

It communicates with the locally-running Anki application via the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on and produces up to three cards per note:

- **Listening card** — plays the audio and asks 何を言っていますか？; the answer reveals the Japanese text, English translation, and notes.
- **Production card** — shows the English prompt; the answer reveals the Japanese text, plays the audio, and shows notes.
- **Response card** *(optional)* — shows an English situational prompt and plays its audio; the answer reveals both the prompt and the response in Japanese, plus the response audio. Only generated when a situational prompt is provided.

---

## Why it was created

ChatGPT has the best real-time voice for practicing spoken Japanese. The old workflow was:

1. Have a conversation with ChatGPT
2. Hear a useful phrase
3. Manually record it with Quicktime
4. Create an Anki card by hand

This tool eliminates steps 3 and 4. You copy the JSON that ChatGPT outputs, run `jpflash`, and the card — complete with audio — appears in Anki.

---

## Prerequisites

- **Python >= 3.11**
- **[Anki](https://apps.ankiweb.net/)** desktop app installed and running
- **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)** add-on installed in Anki (add-on code: `2055492159`)
- A **Gemini API key** for the default TTS path
- An **OpenAI API key** if you want OpenAI TTS or want FlashGen to auto-translate missing `japanese` or `english`
- **macOS** (the `jpflash` alias uses `pbpaste`; Linux/Windows users can adapt it)

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/flashgen.git
cd flashgen
```

### 2. Create a virtual environment and install dependencies

Using `uv`:

```bash
uv sync --extra dev
```

Or with `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set your API keys

Add this to your `~/.zshrc` (or `~/.bashrc`):

```zsh
export GEMINI_API_KEY="AIza..."
export OPENAI_API_KEY="sk-..."
```

`GEMINI_API_KEY` powers the default TTS path. `OPENAI_API_KEY` is only required when you choose OpenAI TTS or when FlashGen needs to translate because one of `japanese` or `english` is missing.

### 4. Configure flashgen

Open `flashgen.py` and update the constants at the top of the file to match your Anki setup:

```python
DECK_NAME  = "日本語-Soso"                    # Your Anki deck name
MODEL_NAME = "Japanese Listening+Production"  # Your note type name (see Anki Setup below)
```

You can also adjust the default TTS provider, provider-specific voices/models, and default tags here. As of May 1, 2026, the repo defaults to Gemini TTS with `gemini-3.1-flash-tts-preview` and uses `gpt-4o-mini-tts` when `tts_provider` is set to `openai`. FlashGen does not maintain a provider-specific model allowlist, so `tts_provider: "gemini"` can also be paired with another valid Gemini TTS model string such as `gemini-2.5-flash-preview-tts`.

### 5. Add the `jpflash` alias to `~/.zshrc`

```zsh
alias jpflash='pbpaste | /path/to/flashgen/.venv/bin/python /path/to/flashgen/flashgen.py'
```

Replace `/path/to/flashgen` with the actual path where you cloned the repo — for example:

```zsh
alias jpflash='pbpaste | /Users/yourname/ML/flashgen/.venv/bin/python /Users/yourname/ML/flashgen/flashgen.py'
```

Then reload your shell:

```bash
source ~/.zshrc
```

### MCP/server scaffold

The MCP/server package currently exposes a health endpoint:

```bash
uv run uvicorn flashgen_mcp.app:app --reload
```

The server is intended to call the same card-generation engine as the CLI. See `docs/SYSTEM_DESIGN.md` for the one-repo architecture and Lightsail deployment model.

---

## Anki Setup

You need to create a Note Type with the exact name and fields that `flashgen` expects.

### Create the Note Type

1. Open Anki → **Tools** → **Manage Note Types**
2. Click **Add** → **Add: Basic** → name it exactly: `Japanese Listening+Production`
3. Click **Fields** and create these seven fields in order:
   - `Japanese`
   - `English`
   - `Notes`
   - `Audio`
   - `Japanese Prompt`
   - `English Prompt`
   - `Audio Prompt`

The `_tts` JSON fields are synthesis-only inputs. They are used to generate the embedded audio but are not stored in separate Anki note fields, so the note type stays exactly the same.

### Add Card Templates

Still in the Note Type editor, click **Cards** and set up three templates:

First, paste this into the **Styling** section (shared across both cards):

```css
.notes {
  margin-top: 12px;
  font-size: 18px;
  color: #444;
}
.nightMode .notes {
  color: #bbb;
}
```

---

**Card 1 — Listening** (audio prompt → text answer)

*Front Template:*
```html
<div>何を言っていますか？</div>
<br>
<div>{{Audio}}</div>
```

*Back Template:*
```html
{{FrontSide}}

<hr id=answer>

<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<br>
<div>{{English}}</div>
<br>
{{#Notes}}
<div class="notes">{{furigana:Notes}}</div>
{{/Notes}}
```

---

**Card 2 — Production** (English prompt → Japanese answer)

Click **Add Card** to create a second template.

*Front Template:*
```html
<div>{{English}}</div>
```

*Back Template:*
```html
{{FrontSide}}

<hr id=answer>

<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<br>
<div>{{Audio}}</div>
{{#Notes}}
<div class="notes">{{furigana:Notes}}</div>
{{/Notes}}
```

---

**Card 3 — Response** *(situational prompt → contextual response)*

Click **Add Card** to create a third template. The `{{#Japanese Prompt}}` wrapper ensures this card is only generated for notes that have a prompt — Standard notes are unaffected.

*Front Template:*
```html
{{#Japanese Prompt}}
<b>きっかけ</b>：{{English Prompt}}<br>
<b>回答</b>：{{English}}<br>
<br>
<div>{{Audio Prompt}}</div>
{{/Japanese Prompt}}
```

*Back Template:*
```html
{{#Japanese Prompt}}
{{FrontSide}}

<hr id=answer>

<b>きっかけ</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese Prompt}}</div>
<br>
<b>回答</b>：
<div style="font-size: 1.4em;">{{furigana:Japanese}}</div>
<br>
<div>{{Audio}}</div>
{{#Notes}}<div class="notes">{{furigana:Notes}}</div>{{/Notes}}
{{/Japanese Prompt}}
```

---

## How to use with ChatGPT

### Step 1 — Load the system prompt

Open `chatgpt_prompt.md`, copy the prompt block, and paste it into a new ChatGPT conversation. You can ask ChatGPT to remember it for future sessions:

> *"Please remember this flashcard prompt for our future conversations."*

### Step 2 — Practice Japanese

Have a conversation with ChatGPT in voice or text mode. ChatGPT's Japanese voice is excellent for pronunciation modeling.

### Step 3 — Request a flashcard

When you hear or see a phrase you want to learn, say:

> *"Please make a flashcard for that sentence."*

You can give the phrase in **English or Japanese** — ChatGPT will fill in the other language automatically. You can also ask it to add notes for specific words:

> *"Flashcard for that sentence, and add a definition for 抑える."*

For **Standard cards**, ChatGPT outputs JSON like:

```json
{
  "japanese": " 彼[かれ]はやっとのことで 怒[いか]りを 抑[おさ]えた。",
  "japanese_tts": "彼はやっとのことで怒りを抑えた。",
  "english": "He finally managed to hold back his anger.",
  "notes": "やっとのことで: barely, with great effort\n 抑[おさ]える: to hold back, to suppress",
  "tags": ["auto", "jp", "conversation"]
}
```

If you want to force a particular TTS backend for a card, include both `tts_provider` and `tts_model` together. For example:

```json
{
  "japanese": " 写[しゃ] 真[しん]を 撮[さつ] 影[えい]しました。",
  "english": "I took a photo.",
  "notes": "",
  "tags": ["auto", "jp"],
  "tts_provider": "openai",
  "tts_model": "gpt-4o-mini-tts"
}
```

For **Response cards**, ChatGPT will first ask about (or propose) a situational prompt, confirm it with you, then output JSON with the additional prompt fields:

```json
{
  "japanese": "スティーブ・ 斉[さい] 藤[とう]で 予[よ] 約[やく]しております。",
  "japanese_tts": "スティーブ・斉藤で予約しております。",
  "english": "I have a reservation under the name Steve Saito.",
  "notes": " 予[よ] 約[やく]: reservation\nしております: polite form of している",
  "tags": ["auto", "jp", "hotel"],
  "japanese_prompt": "ご 予[よ] 約[やく]のお 名[な] 前[まえ]を 頂[ちょう] 戴[だい]してもよろしいでしょうか。",
  "english_prompt": "May I have the name under which your reservation was made?",
  "japanese_prompt_tts": "ご予約のお名前を頂戴してもよろしいでしょうか。"
}
```

`japanese_tts` and `japanese_prompt_tts` are plain Japanese strings meant only for synthesis. They let ChatGPT swap just the risky kanji to hiragana when a TTS engine would likely misread a name, jukujikun, classical expression, or rare compound, while `japanese` and `japanese_prompt` remain fully annotated for Anki display.

> **Important:** Anki must be open and running on the same computer where flashgen is running. AnkiConnect only listens locally on `127.0.0.1:8765`.

### Step 4 — Create the card

Copy the JSON output (Cmd+C), switch to your terminal, and run:

```bash
jpflash
```

The card appears in Anki immediately, complete with TTS audio.

---

## Input / Output reference

### Input (JSON via stdin)

```json
{
  "japanese":        "string (optional — annotated as kanji[reading]; auto-translated from english if omitted)",
  "english":         "string (optional — auto-translated from japanese if omitted)",
  "notes":           "string (optional — use word[reading] format for furigana in definitions)",
  "tags":            ["list", "of", "tags"],
  "deck":            "string (optional — overrides the configured deck)",
  "japanese_tts":    "string (optional — plain Japanese text used for TTS; falls back to stripped japanese when omitted)",
  "japanese_prompt": "string (optional — situational prompt in Japanese, annotated as kanji[reading])",
  "english_prompt":  "string (optional — English version of the situational prompt)",
  "japanese_prompt_tts": "string (optional — plain Japanese text used for prompt TTS; falls back to stripped japanese_prompt when omitted)",
  "tts_provider":    "string (optional — openai or gemini; defaults to gemini when both TTS fields are omitted)",
  "tts_model":       "string (optional — provider-specific TTS model id such as gemini-3.1-flash-tts-preview, gemini-2.5-flash-preview-tts, or gpt-4o-mini-tts; must be provided together with tts_provider)"
}
```

At least one of `japanese` or `english` is required. `japanese_prompt` and `english_prompt` must be provided together or not at all. `tts_provider` and `tts_model` must also be provided together or not at all. If `japanese_tts` or `japanese_prompt_tts` is omitted, FlashGen falls back to the corresponding display field with furigana markup stripped. If both TTS provider fields are omitted, FlashGen defaults to Gemini TTS.

### Output (JSON to stdout)

```json
{
  "status":           "ok",
  "note_id":          12345,
  "deck":             "日本語-Soso",
  "model":            "Japanese Listening+Production",
  "japanese":         "...",
  "english":          "...",
  "notes":            "...",
  "tags":             ["..."],
  "tts_provider":     "gemini",
  "tts_model":        "gemini-3.1-flash-tts-preview",
  "japanese_tts":     "...",
  "audio_file":       "filename.wav",
  "local_audio_path": "anki_audio_out/filename.wav",
  "japanese_prompt":  "... (only present for Response cards)",
  "english_prompt":   "... (only present for Response cards)",
  "japanese_prompt_tts": "... (only present for Response cards)",
  "audio_prompt_file": "filename.wav (only present for Response cards)"
}
```

The returned `japanese` and `japanese_prompt` fields are the display text for Anki and contain FlashGen-normalized furigana. The returned `_tts` fields are the plain Japanese strings actually sent to the TTS engine. When Gemini TTS is used, `audio_file` and `audio_prompt_file` end in `.wav`. When OpenAI TTS is used, they end in `.mp3`.

---

## License

MIT
