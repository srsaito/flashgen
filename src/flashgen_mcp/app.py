import base64
import hashlib
import html
import json
import os
import secrets
import time

import requests as _requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import flashgen
from flashgen_mcp.schema import CardRequest

_MCP_TOKEN = os.environ.get("FLASHGEN_MCP_TOKEN", "")

_ISSUER = "https://mcp.ssaito.net"
_oauth_codes: dict[str, dict] = {}  # auth_code → {code_challenge, redirect_uri, expires}


def _check_bearer(request: Request) -> bool:
    """Return True if the request carries a valid Bearer token, or if no token is configured."""
    if not _MCP_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_MCP_TOKEN}"


def _pkce_verify(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return computed == challenge

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

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_metadata() -> JSONResponse:
        return JSONResponse({
            "issuer": _ISSUER,
            "authorization_endpoint": f"{_ISSUER}/oauth/authorize",
            "token_endpoint": f"{_ISSUER}/oauth/token",
            "registration_endpoint": f"{_ISSUER}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    @app.post("/oauth/register")
    async def oauth_register(request: Request) -> JSONResponse:
        body = await request.json()
        client_id = secrets.token_urlsafe(16)
        return JSONResponse(status_code=201, content={
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": body.get("redirect_uris", []),
            "token_endpoint_auth_method": "none",
        })

    @app.get("/oauth/authorize")
    async def oauth_authorize_get(
        response_type: str = "code",
        client_id: str = "",
        redirect_uri: str = "",
        code_challenge: str = "",
        code_challenge_method: str = "S256",
        state: str = "",
    ) -> HTMLResponse:
        e = html.escape
        body = f"""<!DOCTYPE html>
<html><head><title>FlashGen MCP – Authorize</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:80px auto;padding:0 16px}}
button{{padding:10px 24px;font-size:16px;cursor:pointer}}</style></head>
<body>
<h2>Authorize FlashGen MCP</h2>
<p>Allow Claude to access your FlashGen MCP tools (validate and create Anki flashcards)?</p>
<form method="post" action="/oauth/authorize">
  <input type="hidden" name="client_id" value="{e(client_id)}">
  <input type="hidden" name="redirect_uri" value="{e(redirect_uri)}">
  <input type="hidden" name="code_challenge" value="{e(code_challenge)}">
  <input type="hidden" name="code_challenge_method" value="{e(code_challenge_method)}">
  <input type="hidden" name="state" value="{e(state)}">
  <button type="submit">Authorize</button>
</form>
</body></html>"""
        return HTMLResponse(body)

    @app.post("/oauth/authorize")
    async def oauth_authorize_post(
        client_id: str = Form(""),
        redirect_uri: str = Form(""),
        code_challenge: str = Form(""),
        code_challenge_method: str = Form("S256"),
        state: str = Form(""),
    ) -> RedirectResponse:
        code = secrets.token_urlsafe(32)
        _oauth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "expires": time.time() + 300,
        }
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            data = dict(form)
        else:
            data = await request.json()

        if data.get("grant_type") != "authorization_code":
            return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})

        stored = _oauth_codes.pop(data.get("code", ""), None)
        if not stored or time.time() > stored["expires"]:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if stored["redirect_uri"] != data.get("redirect_uri", ""):
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if not _pkce_verify(data.get("code_verifier", ""), stored["code_challenge"]):
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})

        return JSONResponse({
            "access_token": _MCP_TOKEN,
            "token_type": "bearer",
            "expires_in": 31536000,
        })

    @app.get("/health")
    async def health() -> JSONResponse:
        try:
            flashgen.anki_invoke("version")
            return JSONResponse(status_code=200, content={"ok": True})
        except Exception:
            return JSONResponse(status_code=503, content={"ok": False, "anki": False})

    @app.get("/mcp")
    async def mcp_discovery() -> JSONResponse:
        return JSONResponse({
            "serverInfo": _SERVER_INFO["serverInfo"],
            "protocolVersion": _SERVER_INFO["protocolVersion"],
            "tools": [t["name"] for t in _TOOLS],
        })

    @app.post("/mcp")
    async def mcp_handler(request: Request) -> Response:
        if not _check_bearer(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
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
