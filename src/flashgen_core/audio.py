"""TTS: provider/model resolution, stable audio filenames, and the
OpenAI and Gemini synthesis calls.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import wave
from pathlib import Path

from openai import OpenAI

from flashgen_core.config import (
    DEFAULT_TTS_PROVIDER,
    GEMINI_TTS_MODEL,
    GEMINI_TTS_VOICE,
    OPENAI_TTS_VOICE,
    TTSConfig,
    read_secret,
)
from flashgen_core.errors import MissingApiKey, ValidationError

# Gemini TTS models Google has shut down (or announced a shutdown for), mapped
# to the replacement to name in the error. Not an allowlist: any other model
# string still passes through, as before. Rejecting these up front turns a
# vague upstream 404 into a ValidationError that says what to use instead.
# Source: Google deprecation notice received 2026-11-19 — all three stop
# working on 2026-12-18 (flashgen-glm).
RETIRED_GEMINI_TTS_MODELS: dict[str, str] = {
    "gemini-3.1-flash-tts-preview": "gemini-3.8-flash-tts",
    "gemini-2.5-flash-preview-tts": "gemini-3.8-flash-tts",
    "gemini-2.5-pro-preview-tts": "gemini-3.8-flash-tts",
}

def safe_filename_stem(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+", "_", text)
    cleaned = cleaned.strip("_")
    return (cleaned or "audio")[:max_len]


def stable_audio_filename(japanese: str, extension: str = ".mp3") -> str:
    digest = hashlib.sha1(japanese.encode("utf-8")).hexdigest()[:10]
    stem = safe_filename_stem(japanese)
    return f"{stem}_{digest}{extension}"


def resolve_tts_config(
    tts_provider: str | None = None,
    tts_model: str | None = None,
) -> TTSConfig:
    provider = (tts_provider or "").strip()
    model = (tts_model or "").strip()

    if bool(provider) != bool(model):
        raise ValidationError("'tts_provider' and 'tts_model' must be provided together.")

    if not provider and not model:
        provider = DEFAULT_TTS_PROVIDER
        model = GEMINI_TTS_MODEL

    provider = provider.lower()
    if provider == "openai":
        extension = ".mp3"
        voice = OPENAI_TTS_VOICE
    elif provider == "gemini":
        extension = ".wav"
        voice = GEMINI_TTS_VOICE
    else:
        raise ValidationError("'tts_provider' must be one of: openai, gemini.")

    if not model:
        raise ValidationError("'tts_model' must be a non-empty string.")

    if provider == "gemini" and model in RETIRED_GEMINI_TTS_MODELS:
        replacement = RETIRED_GEMINI_TTS_MODELS[model]
        raise ValidationError(
            f"Gemini TTS model '{model}' is retired by Google (shut down "
            f"2026-12-18). Use '{replacement}', or omit 'tts_provider' and "
            "'tts_model' to get FlashGen's default."
        )

    return TTSConfig(
        provider=provider,
        model=model,
        voice=voice,
        extension=extension,
    )


def write_wave_file(
    out_path: Path,
    pcm_data: bytes,
    *,
    channels: int = 1,
    rate: int = 24000,
    sample_width: int = 2,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm_data)


def _pcm_rate_from_mime(mime_type: str, default: int = 24000) -> int:
    match = re.search(r"rate=(\d+)", mime_type or "")
    return int(match.group(1)) if match else default


def write_gemini_audio(out_path: Path, audio_bytes: bytes, mime_type: str | None) -> None:
    """Write a Gemini TTS payload as a .wav file.

    Two payload shapes exist. The 3.1/2.5 preview models return raw 16-bit
    PCM (``audio/l16; rate=24000; channels=1``), which needs a WAV header.
    The 3.8 models return a complete WAV file (``audio/wav``, starting with
    ``RIFF``); wrapping that in another header would put the inner header
    into the audio data as an audible click (flashgen-glm). Decide by the
    bytes, not only the MIME type, so a mislabelled payload cannot double-wrap.
    """
    if audio_bytes[:4] == b"RIFF":
        # Validate before writing: a truncated or non-PCM file should fail
        # here, not later in Anki.
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wav_in:
                if wav_in.getnframes() == 0:
                    raise RuntimeError("Gemini TTS returned a WAV file with no audio frames.")
        except (wave.Error, EOFError) as e:
            raise RuntimeError(f"Gemini TTS returned an unreadable WAV file: {e!r}") from e
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio_bytes)
        return

    write_wave_file(out_path, audio_bytes, rate=_pcm_rate_from_mime(mime_type or ""))


def generate_openai_tts_file(
    client: OpenAI,
    tts_config: TTSConfig,
    text: str,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with client.audio.speech.with_streaming_response.create(
        model=tts_config.model,
        voice=tts_config.voice,
        input=text,
    ) as response:
        response.stream_to_file(out_path)


def generate_gemini_tts_file(
    api_key: str,
    tts_config: TTSConfig,
    text: str,
    out_path: Path,
) -> None:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "Gemini TTS requires the 'google-genai' package to be installed."
        ) from e

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=tts_config.model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=tts_config.voice
                    )
                )
            ),
        ),
    )

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("Gemini TTS returned no candidates.")

    parts = getattr(candidates[0].content, "parts", None) or []
    if not parts or getattr(parts[0], "inline_data", None) is None:
        raise RuntimeError("Gemini TTS returned no audio payload.")

    inline_data = parts[0].inline_data
    audio_data = inline_data.data
    if isinstance(audio_data, str):
        audio_bytes = base64.b64decode(audio_data)
    else:
        audio_bytes = audio_data

    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise RuntimeError("Gemini TTS returned empty audio data.")

    write_gemini_audio(out_path, audio_bytes, getattr(inline_data, "mime_type", None))


def generate_tts_file(tts_config: TTSConfig, text: str, out_path: Path) -> None:
    if tts_config.provider == "openai":
        api_key = read_secret("OPENAI_API_KEY")
        if not api_key:
            raise MissingApiKey("OPENAI_API_KEY is not set.")
        generate_openai_tts_file(
            OpenAI(api_key=api_key),
            tts_config,
            text,
            out_path,
        )
        return

    if tts_config.provider == "gemini":
        api_key = read_secret("GEMINI_API_KEY")
        if not api_key:
            raise MissingApiKey("GEMINI_API_KEY is not set.")
        generate_gemini_tts_file(api_key, tts_config, text, out_path)
        return

    raise RuntimeError(f"Unsupported TTS provider: {tts_config.provider}")
