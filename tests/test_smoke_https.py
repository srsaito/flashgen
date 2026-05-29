"""Phase 5 smoke tests for the public HTTPS endpoint at mcp.ssaito.net.

Run with: FLASHGEN_SMOKE=1 pytest tests/test_smoke_https.py -v
Skipped entirely unless FLASHGEN_SMOKE=1 is set.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("FLASHGEN_SMOKE_URL", "https://mcp.ssaito.net")
SMOKE_TOKEN = os.environ.get("FLASHGEN_SMOKE_TOKEN", "")

smoke = pytest.mark.skipif(
    os.environ.get("FLASHGEN_SMOKE") != "1",
    reason="Set FLASHGEN_SMOKE=1 to run live endpoint smoke tests",
)

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def jsonrpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


@smoke
def test_health_returns_200_with_ok():
    resp = requests.get(f"{BASE_URL}/health", timeout=10)
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


@smoke
def test_mcp_without_token_returns_401():
    resp = requests.post(
        f"{BASE_URL}/mcp",
        json=jsonrpc("tools/list"),
        headers=MCP_HEADERS,
        timeout=10,
    )
    assert resp.status_code == 401


@smoke
def test_mcp_with_valid_token_returns_200():
    assert SMOKE_TOKEN, "Set FLASHGEN_SMOKE_TOKEN to a valid auth token"
    auth_headers = {**MCP_HEADERS, "Authorization": f"Bearer {SMOKE_TOKEN}"}
    resp = requests.post(
        f"{BASE_URL}/mcp",
        json=jsonrpc("tools/list"),
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 200
    body = resp.json()
    tool_names = [t["name"] for t in body["result"]["tools"]]
    assert "validate_flashcard" in tool_names
    assert "create_flashcard" in tool_names
