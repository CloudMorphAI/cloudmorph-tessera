"""Tests for OAuth 2.1 Resource Server endpoints."""

from __future__ import annotations

import base64
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app():
    from tessera.auth.oauth_rs import make_metadata_route
    from fastapi import FastAPI

    test_app = FastAPI()
    make_metadata_route(test_app)
    return test_app


@pytest.fixture()
def client(app):
    return TestClient(app)


# ── Wave 3 tests (metadata + JWKS) ───────────────────────────────────────────

def test_oauth_metadata_shape(client: TestClient) -> None:
    response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    data = response.json()
    assert "resource" in data
    assert "authorization_servers" in data
    assert "scopes_supported" in data
    assert "bearer_methods_supported" in data
    assert data["bearer_methods_supported"] == ["header"]
    assert isinstance(data["scopes_supported"], list)


def test_jwks_stub_shape(client: TestClient) -> None:
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    data = response.json()
    assert "keys" in data
    assert data["keys"] == []


# ── RFC 7591 DCR proxy tests ──────────────────────────────────────────────────

def test_dcr_proxy_returns_503_when_unconfigured(client: TestClient, monkeypatch) -> None:
    """POST /register with no TESSERA_OAUTH_AS_REGISTRATION_URL must return 503."""
    monkeypatch.delenv("TESSERA_OAUTH_AS_REGISTRATION_URL", raising=False)
    response = client.post("/register", json={"client_name": "test-agent"})
    assert response.status_code == 503
    data = response.json()
    assert data["error"] == "server_error"
    assert "not configured" in data["error_description"]


def test_dcr_proxy_forwards_to_upstream(client: TestClient, monkeypatch) -> None:
    """POST /register should forward the body to the upstream AS and relay its response."""
    upstream_url = "https://auth.example.com/register"
    monkeypatch.setenv("TESSERA_OAUTH_AS_REGISTRATION_URL", upstream_url)

    upstream_payload = {
        "client_id": "abc123",
        "client_name": "test-agent",
        "redirect_uris": ["https://app.example.com/cb"],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = upstream_payload

    with patch("tessera.auth.oauth_rs.httpx.Client") as mock_client_cls:
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance

        response = client.post(
            "/register",
            json={"client_name": "test-agent", "redirect_uris": ["https://app.example.com/cb"]},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["client_id"] == "abc123"
    # Confirm the upstream was called with the correct URL
    mock_client_instance.post.assert_called_once()
    call_args = mock_client_instance.post.call_args
    assert call_args[0][0] == upstream_url


# ── RFC 7662 Token Introspection tests ────────────────────────────────────────

def _basic_header(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {credentials}"


def test_introspect_rejects_unauthenticated(client: TestClient, monkeypatch) -> None:
    """POST /introspect without Authorization header must return 401."""
    monkeypatch.setenv("TESSERA_OAUTH_INTROSPECTION_CLIENTS", "auditor:s3cr3t")
    response = client.post(
        "/introspect",
        data={"token": "some.jwt.token"},
    )
    assert response.status_code == 401


def test_introspect_returns_active_false_for_expired_token(client: TestClient, monkeypatch) -> None:
    """POST /introspect with an expired JWT must return {"active": false}."""
    import jwt as _pyjwt

    monkeypatch.setenv("TESSERA_OAUTH_INTROSPECTION_CLIENTS", "auditor:s3cr3t")
    # Clear any issuer env vars so the expired-exp check fires before the issuer check
    monkeypatch.delenv("TESSERA_OAUTH_AUTHORIZATION_SERVER", raising=False)

    # Build an expired token: exp is 60 seconds in the past
    payload = {
        "sub": "user-42",
        "iss": "https://auth.example.com",
        "aud": "tessera-mcp",
        "iat": int(time.time()) - 120,
        "exp": int(time.time()) - 60,  # expired
    }
    expired_token = _pyjwt.encode(payload, "test-secret", algorithm="HS256")

    response = client.post(
        "/introspect",
        data={"token": expired_token},
        headers={"Authorization": _basic_header("auditor", "s3cr3t")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data == {"active": False}
