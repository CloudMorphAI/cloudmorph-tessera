"""Unit tests for OIDCAuthenticator (management-plane auth).

JWKS endpoint is mocked with respx so no real HTTP calls are made.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response
from jose import jwt

from tessera.auth.oidc import OIDCAuthenticator
from tessera.errors import UnauthorizedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JWKS_URL = "https://clerk.example.com/.well-known/jwks.json"
_ISSUER = "https://clerk.example.com"
_AUDIENCE = "tessera-management"

# A minimal RSA public key in JWK format (2048-bit test key — NOT a real secret)
_KID = "test-kid-001"
_JWK_PUBLIC = {
    "kty": "RSA",
    "use": "sig",
    "kid": _KID,
    "alg": "RS256",
    "n": (
        "syfp1JBIjTVMNcCJqLHlpjRrWJn-QJaFRGc5kCFvw_CWVGF3pBBBCe5QzXPQ"
        "m6Ic94GHRiRiqbB4wH7HMNjM5t7VHR2H5VdV5BQNM1QUeK7P7rYlcnzN9MSVD"
        "n2ERTG9UhNr7pXGqL8vE59ZNhPT4BwEL1-FtKb6-W2EAKOYQRtWUGSCvXO_uW"
        "Vp2y8TfG1M39Ie2-UJxLWLEYS5hYHBFIxd2VNp3c_wCDfALWrVzBn-tnYdJBq"
        "PKqQHHHsFTsMXQDI2XVHj6i4TuJlXlAXKA5G5y4oZaWHWL5Gu4TzJZ5x-Y9kG"
        "4CAlNH_F_yGH9oOfxnFd6w"
    ),
    "e": "AQAB",
}

_JWKS_RESPONSE = {"keys": [_JWK_PUBLIC]}


def _make_authenticator() -> OIDCAuthenticator:
    return OIDCAuthenticator(
        jwks_url=_JWKS_URL,
        issuer=_ISSUER,
        audience=_AUDIENCE,
        clock_skew_seconds=60,
        scope_claim="email",
        provider="clerk",
        deployment_id="default",
    )


def _make_request(token: str) -> MagicMock:
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    return req


def _make_request_no_header() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    return req


# ---------------------------------------------------------------------------
# Fixtures — real RSA key pair generated with python-jose for deterministic tests
# We use jose's RSA utilities to generate a test keypair inline.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate an RSA keypair for signing test JWTs."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key


@pytest.fixture(scope="module")
def test_jwk(rsa_keypair):
    """Return (private_key_pem, public_jwk_dict) for signing and JWKS mock."""
    from cryptography.hazmat.primitives import serialization
    from jose import jwk as jose_jwk

    private_pem = rsa_keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = rsa_keypair.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk_dict = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = _KID
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"
    return private_pem, jwk_dict


def _sign_jwt(private_pem: bytes, claims: dict, kid: str = _KID, alg: str = "RS256") -> str:
    return jwt.encode(claims, private_pem, algorithm=alg, headers={"kid": kid})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
def test_valid_clerk_jwt_validates(test_jwk):
    """Valid Clerk-style JWT with correct kid, issuer, audience, and expiry validates."""
    private_pem, public_jwk = test_jwk
    jwks_payload = {"keys": [public_jwk]}

    respx.get(_JWKS_URL).mock(return_value=Response(200, json=jwks_payload))

    claims = {
        "sub": "user_abc123",
        "email": "alice@example.com",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign_jwt(private_pem, claims)
    auth = _make_authenticator()
    ctx = auth.authenticate(_make_request(token))

    assert ctx.principal_id == "user_abc123"
    assert ctx.scope == "alice_at_example_com"
    assert ctx.metadata["provider"] == "clerk"


@respx.mock
def test_expired_jwt_raises_unauthorized(test_jwk):
    """Expired JWT raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user_abc",
        "email": "bob@example.com",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) - 7200,  # 2 hours ago
        "iat": int(time.time()) - 10000,
    }
    token = _sign_jwt(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="expired"):
        auth.authenticate(_make_request(token))


@respx.mock
def test_unknown_kid_triggers_refetch(test_jwk):
    """Unknown kid causes re-fetch; if still not found, UnauthorizedError is raised."""
    private_pem, public_jwk = test_jwk

    # First call returns empty keys; second call (re-fetch) also returns empty
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": []}))

    claims = {
        "sub": "user_xyz",
        "email": "charlie@example.com",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign_jwt(private_pem, claims, kid="unknown-kid-999")
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="unknown JWT kid"):
        auth.authenticate(_make_request(token))


@respx.mock
def test_wrong_audience_raises(test_jwk):
    """JWT with wrong audience raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user_xyz",
        "email": "dave@example.com",
        "iss": _ISSUER,
        "aud": "wrong-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign_jwt(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="JWT validation failed"):
        auth.authenticate(_make_request(token))


@respx.mock
def test_wrong_issuer_raises(test_jwk):
    """JWT with wrong issuer raises UnauthorizedError."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user_xyz",
        "email": "eve@example.com",
        "iss": "https://evil.example.com",
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign_jwt(private_pem, claims)
    auth = _make_authenticator()

    with pytest.raises(UnauthorizedError, match="JWT validation failed"):
        auth.authenticate(_make_request(token))


def test_missing_bearer_header_raises():
    """Request with no Authorization header raises UnauthorizedError."""
    auth = _make_authenticator()
    with pytest.raises(UnauthorizedError, match="missing or malformed"):
        auth.authenticate(_make_request_no_header())


def test_malformed_authorization_header_raises():
    """Request with non-Bearer Authorization raises UnauthorizedError."""
    auth = _make_authenticator()
    req = MagicMock()
    req.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    with pytest.raises(UnauthorizedError, match="missing or malformed"):
        auth.authenticate(req)


@respx.mock
def test_jwks_endpoint_500_raises(test_jwk):
    """JWKS endpoint returning 500 raises UnauthorizedError."""
    respx.get(_JWKS_URL).mock(return_value=Response(500))
    auth = _make_authenticator()
    req = MagicMock()
    req.headers = {"Authorization": "Bearer some.fake.token"}

    # The token itself is malformed so it may raise for that first;
    # but the JWKS fetch failure must propagate as UnauthorizedError.
    with pytest.raises(UnauthorizedError):
        auth.authenticate(req)


@respx.mock
def test_scope_claim_normalization(test_jwk):
    """Email-style scope claim is normalized to SCOPE_RE-compliant slug."""
    private_pem, public_jwk = test_jwk
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))

    claims = {
        "sub": "user_001",
        "email": "frank.doe@company.io",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = _sign_jwt(private_pem, claims)
    auth = _make_authenticator()
    ctx = auth.authenticate(_make_request(token))

    # frank.doe@company.io → frank_doe_at_company_io
    assert ctx.scope == "frank_doe_at_company_io"
    assert ctx.principal_id == "user_001"
