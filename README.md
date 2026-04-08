# flashgen

Automatically create Japanese Anki flashcards with AI-generated audio — directly from a ChatGPT conversation.

---

## What it does

`flashgen` takes a Japanese phrase (or English — it auto-translates), generates a natural-sounding MP3 using OpenAI's text-to-speech API, and creates an Anki flashcard with audio in your deck — all in one command.

It communicates with the locally-running Anki application via the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on and produces two cards per note:

- **Listening card** — plays the audio and asks 何を言っていますか？; the answer reveals the Japanese text, English translation, and notes.
- **Production card** — shows the English prompt; the answer reveals the Japanese text, plays the audio, and shows notes.

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

- **Python > 3.9** (versions 3.9 and below cause deprecation warnings)
- **[Anki](https://apps.ankiweb.net/)** desktop app installed and running
- **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)** add-on installed in Anki (add-on code: `2055492159`)
- An **OpenAI API key** with access to TTS and chat models
- **macOS** (the `jpflash` alias uses `pbpaste`; Linux/Windows users can adapt it)

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/flashgen.git
cd flashgen
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set your OpenAI API key

Add this to your `~/.zshrc` (or `~/.bashrc`):

```zsh
export OPENAI_API_KEY="sk-..."
```

### 4. Configure flashgen

Open `flashgen.py` and update the constants at the top of the file to match your Anki setup:

```python
DECK_NAME  = "日本語-Soso"                    # Your Anki deck name
MODEL_NAME = "Japanese Listening+Production"  # Your note type name (see Anki Setup below)
```

You can also adjust the TTS voice, models, and default tags here.

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

---

## Anki Setup

You need to create a Note Type with the exact name and fields that `flashgen` expects.

### Create the Note Type

1. Open Anki → **Tools** → **Manage Note Types**
2. Click **Add** → **Add: Basic** → name it exactly: `Japanese Listening+Production`
3. Click **Fields** and create these four fields in order:
   - `Japanese`
   - `English`
   - `Notes`
   - `Audio`

### Add Card Templates

Still in the Note Type editor, click **Cards** and set up two templates:

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

<div style="font-size: 1.4em;">{{Japanese}}</div>
<br>
<div>{{English}}</div>
<br>
{{#Notes}}
<div style="margin-top: 12px; color: #666; font-size: 18px;">
  {{Notes}}
</div>
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

<div style="font-size: 1.4em;">{{Japanese}}</div>
<br>
<div>{{Audio}}</div>
{{#Notes}}
<div style="margin-top: 12px; color: #666; font-size: 18px;">
  {{Notes}}
</div>
{{/Notes}}
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

ChatGPT will output a JSON object like:

```json
{
  "japanese": "彼はやっとのことで怒りを抑えた。",
  "english": "He finally managed to hold back his anger.",
  "notes": "やっとのことで: barely, with great effort\n抑える（おさえる）: to hold back, to suppress",
  "tags": ["auto", "jp", "conversation"]
}
```

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
  "japanese": "string (optional — auto-translated from english if omitted)",
  "english":  "string (optional — auto-translated from japanese if omitted)",
  "notes":    "string (optional)",
  "tags":     ["list", "of", "tags"]
}
```

At least one of `japanese` or `english` is required.

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
  "audio_file":       "filename.mp3",
  "local_audio_path": "anki_audio_out/filename.mp3"
}
```

---

## License

MIT
