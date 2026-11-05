"""Offline regressions for resource-server token lifecycle and introspection."""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tessera.auth import oauth_rs
from tessera.errors import UnauthorizedError


@pytest.fixture()
def signer_and_server(tmp_path):
    key = Ed25519PrivateKey.generate()
    server = oauth_rs.OAuthResourceServer(
        issuer="https://issuer.example", audience="example-api", pubkey_path=str(tmp_path / "absent.pem"),
    )
    server._local_pubkey = key.public_key()
    return key, server


def _claims(**changes):
    return {"iss": "https://issuer.example", "aud": "example-api", "sub": "tester",
            "scope": "tessera:audit:read", "exp": int(time.time()) + 60, **changes}


def test_cached_token_stops_authorizing_at_its_expiry(signer_and_server):
    key, server = signer_and_server
    claims = _claims(exp=int(time.time()) + 1)
    token = jwt.encode(claims, key, algorithm="EdDSA")
    assert server.validate_bearer_token(token)["sub"] == "tester"
    time.sleep(max(0, claims["exp"] - time.time()) + 0.02)
    with pytest.raises(UnauthorizedError):
        server.validate_bearer_token(token)


@pytest.mark.parametrize("expiry", [None, float("inf"), float("nan"), True])
def test_tokens_require_a_finite_numeric_expiry(signer_and_server, expiry):
    key, server = signer_and_server
    claims = _claims(exp=expiry)
    if expiry is None:
        claims.pop("exp")
    token = jwt.encode(claims, key, algorithm="EdDSA")
    with pytest.raises(UnauthorizedError):
        server.validate_bearer_token(token)


def _introspection_client(monkeypatch):
    monkeypatch.setenv("TESSERA_OAUTH_INTROSPECTION_CLIENTS", "test-client:public-fixture-secret")
    app = FastAPI()
    oauth_rs.make_metadata_route(app)
    basic = base64.b64encode(b"test-client:public-fixture-secret").decode()
    return TestClient(app), {"Authorization": f"Basic {basic}"}


def test_unsigned_introspection_cannot_report_forged_token_active(monkeypatch):
    monkeypatch.delenv("TESSERA_OAUTH_JWKS_URL", raising=False)
    monkeypatch.setenv("TESSERA_OAUTH_AUTHORIZATION_SERVER", "https://issuer.example")
    client, headers = _introspection_client(monkeypatch)
    forged = jwt.encode(_claims(), "attacker-controlled-public-fixture-secret", algorithm="HS256")
    with client:
        assert client.post("/introspect", data={"token": forged}, headers=headers).json() == {"active": False}


@pytest.mark.parametrize("valid_signature", [True, False])
def test_introspection_cryptographically_verifies_its_trusted_key(monkeypatch, valid_signature):
    monkeypatch.setenv("TESSERA_OAUTH_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setenv("TESSERA_OAUTH_AUTHORIZATION_SERVER", "https://issuer.example")
    trusted_key = Ed25519PrivateKey.generate()
    signing_key = trusted_key if valid_signature else Ed25519PrivateKey.generate()
    token = jwt.encode(_claims(), signing_key, algorithm="EdDSA", headers={"kid": "fixture-key"})
    jwks_client = MagicMock()
    jwks_client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=trusted_key.public_key())
    client, headers = _introspection_client(monkeypatch)
    with patch("jwt.PyJWKClient", return_value=jwks_client), client:
        response = client.post("/introspect", data={"token": token}, headers=headers).json()
        assert response["active"] is valid_signature


@pytest.mark.parametrize("changes", [{"iss": "https://other-issuer.example"}, {"exp": 1}, {"exp": None}])
def test_introspection_rechecks_verified_claims(monkeypatch, changes):
    monkeypatch.setenv("TESSERA_OAUTH_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setenv("TESSERA_OAUTH_AUTHORIZATION_SERVER", "https://issuer.example")
    client, headers = _introspection_client(monkeypatch)
    with patch("tessera.auth.oauth_rs._verify_token_signature", return_value=_claims(**changes)), client:
        assert client.post("/introspect", data={"token": "fixture"}, headers=headers).json() == {"active": False}


def test_revoked_token_cannot_use_scope_guarded_route(signer_and_server):
    key, server = signer_and_server
    token = jwt.encode(_claims(jti="revoked-fixture"), key, algorithm="EdDSA")
    server.validate_bearer_token(token)  # Revocation must also apply to cached claims.
    store = oauth_rs.InMemoryRevocationStore()
    store._revoked.add("revoked-fixture")
    app = FastAPI()
    guard = Depends(oauth_rs.require_scope("tessera:audit:read"))

    @app.get("/protected")
    async def protected(claims=guard):
        return {"sub": claims["sub"]}

    with patch("tessera.auth.oauth_rs.get_oauth_resource_server", return_value=server), \
            patch("tessera.auth.oauth_rs.get_revocation_store", return_value=store), TestClient(app) as client:
        assert client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 401
