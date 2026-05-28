from fastapi import FastAPI

import flashgen
from flashgen_mcp.schema import CardRequest


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

    return app


app = create_app()

