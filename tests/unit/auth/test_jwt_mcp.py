"""Unit tests for JWTAuthenticator (MCP traffic JWT mode).

JWKS endpoint is mocked with respx against Entra/Okta/Cognito-style JWKS samples.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response
from jose import jwt

from tessera.auth.jwt_mcp import JWTAuthenticator
from tessera.errors import UnauthorizedError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JWKS_URL = "https://login.microsoftonline.com/tenant-id/discovery/v2.0/keys"
_ISSUER = "https://login.microsoftonline.com/tenant-id/v2.0"
_AUDIENCE = "api://tessera-mcp"
_KID = "entra-kid-001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keypair():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


@pytest.fixture(scope="module")
def test_jwk(rsa_keypair):
    from cryptography.hazmat.primitives import serialization
    from jose import jwk as jose_jwk

    private_pem = rsa_keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = rsa_keypair.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk_dict = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = _KID
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"
    return private_pem, jwk_dict


def _sign(private_pem: bytes, claims: dict, kid: str = _KID) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _make_authenticator(**kwargs) -> JWTAuthenticator:
    defaults = {
        "jwks_url": _JWKS_URL,
        "issuer": _ISSUER,
        "audience": _AUDIENCE,
        "principal_claim": "sub",
        "scope_claim": "scope",
        "deployment_id": "default",
    }
    defaults.update(kwargs)
    return JWTAuthenticator(**defaults)


def _req(token: str) -> MagicMock:
    r = MagicMock()
    r.headers = {"Authorization": f"Bearer {token}"}
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@respx.mock
def test_valid_entra_jwt(test_jwk):
    """Valid Entra-style JWT authenticates and returns principal from sub claim."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "entra-user-abc123",
        "scope": "tessera.read",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign(private_pem, claims)
    auth = _make_authenticator()
    ctx = auth.authenticate(_req(token))

    assert ctx.principal_id == "entra-user-abc123"
    # The Entra scope claim "tessera.read" contains a dot which fails SCOPE_RE
    # ([a-z0-9_-]). The OIDC validator falls back to deployment_id in that case.
    assert ctx.scope == "default"
    assert ctx.metadata["jwt_provider"] == "external"


@respx.mock
def test_scope_extracted_from_space_delimited_claim(test_jwk):
    """Space-delimited scope claim uses first token if SCOPE_RE-compliant."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "okta-user-xyz",
        "scope": "tessera-read write delete",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign(private_pem, claims)
    auth = _make_authenticator()
    ctx = auth.authenticate(_req(token))

    assert ctx.principal_id == "okta-user-xyz"
    assert ctx.scope == "tessera-read"


@respx.mock
def test_expired_jwt_raises(test_jwk):
    """Expired token raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "some-user",
        "scope": "read",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) - 3600,
        "iat": int(time.time()) - 7200,
    }
    token = _sign(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="expired"):
        auth.authenticate(_req(token))


@respx.mock
def test_wrong_audience_raises(test_jwk):
    """Token with mismatched audience raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user",
        "scope": "read",
        "iss": _ISSUER,
        "aud": "wrong-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="JWT validation failed"):
        auth.authenticate(_req(token))


@respx.mock
def test_wrong_issuer_raises(test_jwk):
    """Token with mismatched issuer raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user",
        "scope": "read",
        "iss": "https://attacker.example.com",
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="JWT validation failed"):
        auth.authenticate(_req(token))


def test_missing_header_raises():
    """No Authorization header raises UnauthorizedError."""
    auth = _make_authenticator()
    r = MagicMock()
    r.headers = {}
    with pytest.raises(UnauthorizedError, match="missing or malformed"):
        auth.authenticate(r)


@respx.mock
def test_cognito_principal_claim(test_jwk):
    """Cognito-style token using username as principal_claim."""
    private_pem, public_jwk = test_jwk
    cognito_jwks_url = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC/.well-known/jwks.json"
    respx.get(cognito_jwks_url).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "cognito-uuid-000",
        "username": "john_doe",
        "scope": "openid",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign(private_pem, claims)
    auth = JWTAuthenticator(
        jwks_url=cognito_jwks_url,
        issuer=_ISSUER,
        audience=_AUDIENCE,
        principal_claim="username",
        scope_claim="scope",
        deployment_id="default",
    )
    ctx = auth.authenticate(_req(token))

    assert ctx.principal_id == "john_doe"
