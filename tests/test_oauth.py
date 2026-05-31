"""Tests for the minimal OAuth 2.1 server (authorization_code + PKCE)."""
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import flashgen_mcp.app as app_module
from flashgen_mcp.app import app

client = TestClient(app, follow_redirects=False)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _do_authorize(challenge: str, redirect_uri: str = "https://claude.ai/callback", state: str = "s1") -> str:
    """POST to /oauth/authorize and return the auth code from the redirect."""
    resp = client.post(
        "/oauth/authorize",
        data={
            "client_id": "test-client",
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    qs = parse_qs(urlparse(location).query)
    assert qs["state"][0] == state
    return qs["code"][0]


class TestOAuthMetadata:
    def test_metadata_returns_required_fields(self):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        body = resp.json()
        assert body["issuer"] == "https://mcp.ssaito.net"
        assert "authorization_endpoint" in body
        assert "token_endpoint" in body
        assert "S256" in body["code_challenge_methods_supported"]
        assert "authorization_code" in body["grant_types_supported"]

    def test_metadata_includes_registration_endpoint(self):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert "registration_endpoint" in resp.json()


class TestOAuthRegister:
    def test_register_returns_client_id(self):
        resp = client.post(
            "/oauth/register",
            json={"redirect_uris": ["https://claude.ai/callback"], "client_name": "Claude"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "client_id" in body
        assert body["redirect_uris"] == ["https://claude.ai/callback"]

    def test_register_token_endpoint_auth_method_is_none(self):
        resp = client.post("/oauth/register", json={"redirect_uris": ["https://claude.ai/callback"]})
        assert resp.json()["token_endpoint_auth_method"] == "none"


class TestOAuthAuthorize:
    def test_get_returns_html_with_authorize_button(self):
        _, challenge = _pkce_pair()
        resp = client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "test-client",
                "redirect_uri": "https://claude.ai/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "abc",
            },
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Authorize" in resp.text

    def test_post_redirects_with_code_and_state(self):
        _, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": "test-client",
                "redirect_uri": "https://claude.ai/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "mystate",
            },
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        qs = parse_qs(urlparse(location).query)
        assert "code" in qs
        assert qs["state"][0] == "mystate"


class TestOAuthToken:
    def test_full_pkce_flow_returns_access_token(self, monkeypatch):
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "test-secret-token")
        verifier, challenge = _pkce_pair()
        code = _do_authorize(challenge)
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/callback",
                "code_verifier": verifier,
                "client_id": "test-client",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "test-secret-token"
        assert body["token_type"] == "bearer"

    def test_wrong_code_verifier_returns_invalid_grant(self):
        _, challenge = _pkce_pair()
        code = _do_authorize(challenge)
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/callback",
                "code_verifier": "wrong-verifier",
                "client_id": "test-client",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_invalid_code_returns_invalid_grant(self):
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "nonexistent-code",
                "redirect_uri": "https://claude.ai/callback",
                "code_verifier": "anything",
                "client_id": "test-client",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_code_can_only_be_used_once(self, monkeypatch):
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "test-secret-token")
        verifier, challenge = _pkce_pair()
        code = _do_authorize(challenge)
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/callback",
            "code_verifier": verifier,
            "client_id": "test-client",
        }
        client.post("/oauth/token", data=payload)
        resp = client.post("/oauth/token", data=payload)
        assert resp.status_code == 400

    def test_unsupported_grant_type_returns_error(self):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"


class TestOAuthTokenJsonBody:
    def test_token_accepts_json_body(self, monkeypatch):
        import flashgen_mcp.app as app_module
        monkeypatch.setattr(app_module, "_MCP_TOKEN", "test-token")
        verifier, challenge = _pkce_pair()
        code = _do_authorize(challenge)
        resp = client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/callback",
                "code_verifier": verifier,
                "client_id": "test-client",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "test-token"

    def test_mismatched_redirect_uri_returns_invalid_grant(self):
        _, challenge = _pkce_pair()
        code = _do_authorize(challenge, redirect_uri="https://claude.ai/callback")
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://evil.example.com/callback",
                "code_verifier": "anything",
                "client_id": "test-client",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"
