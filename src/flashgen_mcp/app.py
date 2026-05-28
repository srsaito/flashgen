import requests as _requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import flashgen
from flashgen_mcp.schema import CardRequest

_ANKI_CONNECT_PHRASES = ("AnkiConnect", "Could not connect to Anki")


def _is_anki_error(msg: str) -> bool:
    return any(phrase in msg for phrase in _ANKI_CONNECT_PHRASES)


def _is_missing_api_key(msg: str) -> bool:
    return "_API_KEY" in msg and "is not set" in msg


def create_app() -> FastAPI:
    app = FastAPI(title="FlashGen MCP", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/validate")
    async def validate(req: CardRequest) -> dict:
        japanese = flashgen.normalize_furigana_text(req.japanese)
        japanese_prompt = flashgen.normalize_furigana_text(req.japanese_prompt)
        return {
            "status": "ok",
            "japanese": japanese,
            "english": req.english,
            "notes": req.notes,
            "tags": req.tags,
            "deck": req.deck,
            "japanese_tts": req.japanese_tts,
            "japanese_prompt": japanese_prompt,
            "english_prompt": req.english_prompt,
            "japanese_prompt_tts": req.japanese_prompt_tts,
            "tts_provider": req.tts_provider,
            "tts_model": req.tts_model,
        }

    @app.post("/create")
    async def create(req: CardRequest) -> JSONResponse:
        try:
            result = flashgen.create_flashcard(
                japanese=req.japanese,
                english=req.english,
                notes=req.notes,
                tags=req.tags,
                deck_name=req.deck if req.deck else flashgen.DECK_NAME,
                japanese_tts=req.japanese_tts,
                japanese_prompt=req.japanese_prompt,
                english_prompt=req.english_prompt,
                japanese_prompt_tts=req.japanese_prompt_tts,
                tts_provider=req.tts_provider,
                tts_model=req.tts_model,
            )
        except _requests.exceptions.ConnectionError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": "anki_unavailable", "message": str(exc)},
            )
        except RuntimeError as exc:
            msg = str(exc)
            if _is_anki_error(msg):
                return JSONResponse(
                    status_code=503,
                    content={"error": "anki_unavailable", "message": msg},
                )
            if _is_missing_api_key(msg):
                return JSONResponse(
                    status_code=503,
                    content={"error": "missing_api_key", "message": msg},
                )
            return JSONResponse(
                status_code=400,
                content={"error": "engine_error", "message": msg},
            )
        return JSONResponse(status_code=200, content=result)

    return app


app = create_app()

