"""Text layer: furigana normalization and validation, the markup
allowlist, line-break funnelling, and Anki HTML rendering.

Pure functions — no network, no Anki, no file system.
"""
from __future__ import annotations

import html
import re
import unicodedata

ANNOTATED_KANJI_RE = re.compile(
    r"[\u0020\u3000]*([\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+)\[([^\]]+)\]"
)


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == "\n")
    return text


def normalize_furigana_annotation(kanji: str, reading: str) -> str:
    """Emit a furigana annotation as a single whole unit: ` kanji[reading]`.

    We deliberately do NOT re-derive per-kanji readings. Splitting a compound's
    reading across its kanji is reading-unsafe — pronunciation and morpheme
    boundaries don't line up with character counts (e.g. 試着室[しちゃくしつ]
    sliced evenly yields 試[しち] 着[ゃく] 室[しつ], where 着[ゃく] even begins
    with a standalone small ゃ). Keeping the bracket over the whole kanji run is
    always reading-safe and respects whatever grouping the LLM chose, which owns
    furigana insertion. The leading space is the Anki furigana separator; the
    caller's regex collapses any pre-existing spacing into this one.
    """
    return f" {kanji}[{reading}]"


def normalize_furigana_text(text: str) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    return ANNOTATED_KANJI_RE.sub(
        lambda match: normalize_furigana_annotation(match.group(1), match.group(2)),
        text,
    )


# Anki renders Japanese / Japanese Prompt / Notes through {{furigana:...}}.
# That filter splits the field on ASCII spaces and makes EVERYTHING before the
# "[" in a chunk the ruby base, so a bracket that is not a reading still steals
# whatever precedes it: `[13:42]` renders 13:42 as ruby over the backtick, and
# "remodeling（和製[わせい]" puts わせい over "remodeling（和製". Normalization
# re-emits the leading space for kanji-run units, which fixes the second shape;
# this check catches the first, which no normalization can rescue.
_STRAY_BRACKET_RE = re.compile(r"(\S*?)\[([^\]]*)\]")


def furigana_errors(text: str) -> list[str]:
    """Brackets in a furigana-rendered field that Anki will mis-parse as ruby.

    Run AFTER normalize_furigana_text: a well-formed annotation is then always
    a space-delimited pure-kanji run, so anything else is a stray bracket that
    would silently render as ruby over the wrong characters.
    """
    problems: list[str] = []
    if not text:
        return problems
    for chunk in normalize_line_breaks(text).replace("\n", " ").split(" "):
        match = _STRAY_BRACKET_RE.match(chunk)
        if not match:
            continue
        base, reading = match.group(1), match.group(2)
        if base and ANNOTATED_KANJI_RE.fullmatch(f" {base}[{reading}]"):
            continue
        shown = chunk[: len(base) + len(reading) + 2]
        if not base:
            problems.append(f"bracket with no kanji before it: {shown}")
        else:
            problems.append(
                f"[...] would render as ruby over non-kanji text: {shown}"
            )
    return problems


def strip_furigana_markup(text: str) -> str:
    if not text:
        return ""

    return ANNOTATED_KANJI_RE.sub(lambda match: match.group(1), text)


def resolve_tts_input(display_text: str, tts_text: str = "") -> str:
    candidate = tts_text if tts_text.strip() else strip_furigana_markup(display_text)
    candidate = unicodedata.normalize("NFC", sanitize_text(candidate))
    # Markup must never reach TTS: the text is spoken and feeds the audio
    # filename stem. Display fields may carry <br>/newlines/emphasis tags, and
    # the update-time regen path recovers display text from the stored HTML
    # field, so strip breaks and emphasis here rather than trusting callers.
    candidate = strip_emphasis_markup(candidate)
    candidate = _NEWLINE_RUN_RE.sub(" ", normalize_line_breaks(candidate))
    return strip_furigana_markup(candidate).strip()


# Line-break representations a caller may supply in text fields. Anki fields
# are HTML: only <br> renders a break, so every break form must funnel to a
# real newline BEFORE html.escape (otherwise a literal <br> escapes into the
# visible text "&lt;br&gt;"), then back to <br> after escaping.
_BR_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)

_NEWLINE_RUN_RE = re.compile(r"\s*\n\s*")

# Inline emphasis allowlist (GH #5 / flashgen-c9a). Attributes are not
# permitted: a tag carrying any attribute fails this regex and is escaped as
# literal text rather than silently stripped. Deliberately no interior
# whitespace either, so English gloss text like "a < b > c" stays literal.
_EMPHASIS_TAG_RE = re.compile(r"<(/?)(b|strong|i|em|u)>", re.IGNORECASE)
_EMPHASIS_PLACEHOLDER_RE = re.compile("\x00(/?)(b|strong|i|em|u)\x00")

# Anything else that looks like an HTML tag (letter right after "<", so
# comparison text like "a < b > c" is ignored) — reported by validation as
# markup that will render as literal text.
_OTHER_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")


def strip_emphasis_markup(text: str) -> str:
    return _EMPHASIS_TAG_RE.sub("", text)


def _reject_bad_markup(**fields: str) -> None:
    """Reject unbalanced/unit-splitting emphasis rather than emitting broken
    HTML into a note. Keys are request-level field names for the message."""
    for label, value in fields.items():
        problems = markup_errors(value)
        if problems:
            raise RuntimeError(f"Invalid markup in '{label}': " + "; ".join(problems))


def markup_errors(text: str) -> list[str]:
    """Hard markup problems: unbalanced emphasis tags, or a tag boundary
    inside an annotated ' kanji[reading]' unit (which would break the furigana
    regex and silently lose the annotation). Create/update reject on these."""
    problems: list[str] = []
    if not text:
        return problems
    text = normalize_line_breaks(text)

    stack: list[str] = []
    for match in _EMPHASIS_TAG_RE.finditer(text):
        name = match.group(2).lower()
        if not match.group(1):
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
        else:
            problems.append(f"unbalanced </{name}> tag")
    problems.extend(f"unclosed <{name}> tag" for name in stack)

    # Map each tag's position into the tag-stripped text, then flag any tag
    # that lands strictly inside an annotated unit's kanji-run + reading.
    # A boundary in the unit's leading spaces or right before the first kanji
    # is fine — furigana normalization re-emits the leading space inside the
    # tag.
    stripped_parts: list[str] = []
    tag_positions: list[tuple[int, str]] = []
    last = pos = 0
    for match in _EMPHASIS_TAG_RE.finditer(text):
        stripped_parts.append(text[last:match.start()])
        pos += match.start() - last
        tag_positions.append((pos, match.group(0)))
        last = match.end()
    stripped_parts.append(text[last:])
    stripped = "".join(stripped_parts)
    for match in ANNOTATED_KANJI_RE.finditer(stripped):
        unit = stripped[match.start(1):match.end()]
        for tag_pos, tag in tag_positions:
            if match.start(1) < tag_pos < match.end():
                problems.append(
                    f"emphasis tag {tag} splits the annotated unit 「{unit}」 — "
                    "emphasis must wrap whole ' kanji[reading]' units"
                )
    return problems


def markup_warnings(text: str) -> list[str]:
    """Non-allowlisted markup: rendered as visible literal text, which is
    rarely what the caller meant. Reported by validation, never rejected."""
    if not text:
        return []
    remaining = strip_emphasis_markup(normalize_line_breaks(text))
    return [
        f"unsupported markup {match.group(0)!r} will render as literal text "
        "(allowed tags: <b> <strong> <i> <em> <u> <br>, no attributes)"
        for match in _OTHER_TAG_RE.finditer(remaining)
    ]


def normalize_line_breaks(text: str) -> str:
    """Funnel every break representation to a real newline. Callers vary:
    the local CLI sends real newlines, MCP clients send literal <br> tags or
    escaped "\\n" sequences."""
    text = _BR_TAG_RE.sub("\n", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return text


def rich_text_to_html(text: str) -> str:
    """Render a display field to Anki HTML: line breaks and allowlisted
    emphasis tags (<b> <strong> <i> <em> <u>) render; everything else is
    escaped to literal text.

    Applied to every text field (Japanese/English/Prompt/Notes). Allowed tags
    are swapped for \\x00 placeholders before html.escape (sanitize_text has
    already removed any real control chars) and re-emitted lowercase after.
    """
    if not text.strip():
        return ""

    text = normalize_line_breaks(sanitize_text(text))
    text = _EMPHASIS_TAG_RE.sub(
        lambda m: f"\x00{m.group(1)}{m.group(2).lower()}\x00", text
    )
    escaped = html.escape(text, quote=False)
    escaped = _EMPHASIS_PLACEHOLDER_RE.sub(r"<\1\2>", escaped)
    return escaped.replace("\n", "<br>")


# The furigana convention also puts spaces before UNANNOTATED units
# (その 報[ほう] 告[こく]は ある…), and strip_furigana_markup only consumes the
# spaces attached to annotated runs — so stripped field text keeps interior
# spaces the query never has (flashgen-lhg). Ignore all spacing when matching.
_JA_SPACE_RE = re.compile(r"[\u0020\u00a0\u3000]+")


def _despace(text: str) -> str:
    return _JA_SPACE_RE.sub("", text)
