import json

import requests as _requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import flashgen
from flashgen_mcp.schema import CardRequest

_ANKI_CONNECT_PHRASES = ("AnkiConnect", "Could not connect to Anki")

_CARD_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "japanese": {
            "type": "string",
            "description": "Japanese text, optionally with furigana in [reading] notation",
        },
        "english": {"type": "string", "description": "English translation"},
        "notes": {"type": "string", "description": "Optional notes about the card"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anki tags",
        },
        "deck": {"type": "string", "description": "Target Anki deck name"},
        "japanese_tts": {
            "type": "string",
            "description": "Text for Japanese TTS audio (defaults to japanese field)",
        },
        "japanese_prompt": {"type": "string", "description": "Japanese prompt question text"},
        "english_prompt": {"type": "string", "description": "English prompt question text"},
        "japanese_prompt_tts": {
            "type": "string",
            "description": "TTS text for the prompt audio",
        },
        "tts_provider": {
            "type": "string",
            "enum": ["openai", "gemini"],
            "description": "TTS provider",
        },
        "tts_model": {"type": "string", "description": "TTS model name"},
    },
}

_TOOLS = [
    {
        "name": "validate_flashcard",
        "description": (
            "Validate and normalize a flashcard JSON without creating an Anki note. "
            "Use this to preview and confirm card content before asking the user to approve creation."
        ),
        "inputSchema": _CARD_INPUT_SCHEMA,
    },
    {
        "name": "create_flashcard",
        "description": (
            "WRITE ACTION — only call after the user has reviewed the card JSON and explicitly "
            "confirmed they want to create the card. "
            "Creates an Anki note and generates TTS audio immediately. "
            "This action is irreversible."
        ),
        "inputSchema": _CARD_INPUT_SCHEMA,
    },
]

_SERVER_INFO = {
    "protocolVersion": "2025-03-26",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "FlashGen MCP", "version": "0.1.0"},
}


def _is_anki_error(msg: str) -> bool:
    return any(phrase in msg for phrase in _ANKI_CONNECT_PHRASES)


def _is_missing_api_key(msg: str) -> bool:
    return "_API_KEY" in msg and "is not set" in msg


def _validate_args(args: dict) -> dict:
    """Run the validate logic and return a result dict."""
    req = CardRequest(**args)
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


def _create_args(args: dict) -> dict:
    """Run create_flashcard and return a result dict, raising on error."""
    req = CardRequest(**args)
    return flashgen.create_flashcard(
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


def _rpc_ok(id_, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _rpc_error(id_, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _tool_error_content(error_key: str, message: str) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": error_key, "message": message})}],
        "isError": True,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="FlashGen MCP", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/mcp")
    async def mcp_discovery() -> JSONResponse:
        return JSONResponse({
            "serverInfo": _SERVER_INFO["serverInfo"],
            "protocolVersion": _SERVER_INFO["protocolVersion"],
            "tools": [t["name"] for t in _TOOLS],
        })

    @app.post("/mcp")
    async def mcp_handler(request: Request) -> Response:
        body = await request.json()
        rpc_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}

        if method == "initialize":
            return _rpc_ok(rpc_id, _SERVER_INFO)

        if method in ("notifications/initialized", "initialized"):
            return Response(status_code=202)

        if method == "tools/list":
            return _rpc_ok(rpc_id, {"tools": _TOOLS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}

            try:
                if name == "validate_flashcard":
                    result = _validate_args(args)
                    return _rpc_ok(rpc_id, {"content": [{"type": "text", "text": json.dumps(result)}]})

                if name == "create_flashcard":
                    try:
                        result = _create_args(args)
                        return _rpc_ok(rpc_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
                    except _requests.exceptions.ConnectionError as exc:
                        return _rpc_ok(rpc_id, _tool_error_content("anki_unavailable", str(exc)))
                    except RuntimeError as exc:
                        msg = str(exc)
                        if _is_anki_error(msg):
                            return _rpc_ok(rpc_id, _tool_error_content("anki_unavailable", msg))
                        if _is_missing_api_key(msg):
                            return _rpc_ok(rpc_id, _tool_error_content("missing_api_key", msg))
                        return _rpc_ok(rpc_id, _tool_error_content("engine_error", msg))

                return _rpc_error(rpc_id, -32601, f"Unknown tool: {name}")

            except Exception as exc:
                return _rpc_ok(rpc_id, _tool_error_content("validation_error", str(exc)))

        return _rpc_error(rpc_id, -32601, f"Method not found: {method}")

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
