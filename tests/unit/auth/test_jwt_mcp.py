"""Unit tests for JWTAuthenticator (MCP traffic JWT mode).

JWKS endpoint is mocked with respx against Entra/Okta/Cognito-style JWKS samples.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
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


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _compact_token(header: dict, claims: dict, signature: bytes = b"") -> str:
    header_part = _b64url(json.dumps(header, separators=(",", ":")).encode())
    claims_part = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{header_part}.{claims_part}.{_b64url(signature)}"


def _hmac_with_public_jwk(claims: dict, public_jwk: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": _KID}
    unsigned = _compact_token(header, claims).rsplit(".", 1)[0]
    # This material is public and attacker-derivable. It models the classic
    # RSA-public-key-as-HMAC-secret confusion attempt without real credentials.
    hmac_key = json.dumps(public_jwk, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(hmac_key, unsigned.encode("ascii"), hashlib.sha256).digest()
    return f"{unsigned}.{_b64url(signature)}"


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
def test_jwk_algorithm_mismatch_is_rejected(test_jwk):
    """A key's trusted alg metadata must agree with the signed JWT header."""
    private_pem, public_jwk = test_jwk
    jwks_route = respx.get(_JWKS_URL).mock(
        return_value=Response(200, json={"keys": [public_jwk]})
    )
    claims = {
        "sub": "synthetic-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(claims, private_pem, algorithm="RS512", headers={"kid": _KID})

    with pytest.raises(UnauthorizedError, match="does not match JWKS key"):
        _make_authenticator().authenticate(_req(token))

    assert jwks_route.call_count == 1


@respx.mock
def test_rs512_authenticates_when_jwk_allows_it(test_jwk):
    """The RSA family remains compatible when trusted JWK metadata agrees."""
    private_pem, public_jwk = test_jwk
    public_jwk = {**public_jwk, "alg": "RS512"}
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))
    claims = {
        "sub": "synthetic-rsa-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(claims, private_pem, algorithm="RS512", headers={"kid": _KID})

    ctx = _make_authenticator().authenticate(_req(token))

    assert ctx.principal_id == "synthetic-rsa-user"


@respx.mock
def test_allowed_es256_jwt_authenticates():
    """ECDSA remains available through the fixed asymmetric allow-list."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jose import jwk as jose_jwk

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_jwk = jose_jwk.construct(public_pem, algorithm="ES256").to_dict()
    public_jwk.update({"kid": _KID, "alg": "ES256", "use": "sig"})
    respx.get(_JWKS_URL).mock(return_value=Response(200, json={"keys": [public_jwk]}))
    claims = {
        "sub": "synthetic-ec-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(claims, private_pem, algorithm="ES256", headers={"kid": _KID})

    ctx = _make_authenticator().authenticate(_req(token))

    assert ctx.principal_id == "synthetic-ec-user"


@respx.mock
def test_rsa_algorithm_rejects_ec_jwk(test_jwk):
    """RSA JWT algorithms cannot be paired with EC keys from the JWKS."""
    private_pem, public_jwk = test_jwk
    mismatched_jwk = {**public_jwk, "kty": "EC"}
    mismatched_jwk.pop("alg", None)
    jwks_route = respx.get(_JWKS_URL).mock(
        return_value=Response(200, json={"keys": [mismatched_jwk]})
    )
    claims = {
        "sub": "synthetic-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = _sign(private_pem, claims)

    with pytest.raises(UnauthorizedError, match="does not match JWKS key type"):
        _make_authenticator().authenticate(_req(token))

    assert jwks_route.call_count == 1


@respx.mock
def test_none_algorithm_is_rejected_before_jwks_fetch(test_jwk):
    """An unsecured JWT cannot select its own accepted algorithm."""
    _, public_jwk = test_jwk
    jwks_route = respx.get(_JWKS_URL).mock(
        return_value=Response(200, json={"keys": [public_jwk]})
    )
    claims = {
        "sub": "synthetic-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = _compact_token({"alg": "none", "typ": "JWT", "kid": _KID}, claims)

    with pytest.raises(UnauthorizedError, match="algorithm is not allowed"):
        _make_authenticator().authenticate(_req(token))

    assert jwks_route.call_count == 0


@respx.mock
def test_hmac_algorithm_cannot_be_selected_for_rsa_jwk(test_jwk):
    """An HMAC token using only public RSA JWK material is rejected up front."""
    _, public_jwk = test_jwk
    jwks_route = respx.get(_JWKS_URL).mock(
        return_value=Response(200, json={"keys": [public_jwk]})
    )
    claims = {
        "sub": "synthetic-user",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = _hmac_with_public_jwk(claims, public_jwk)

    with pytest.raises(UnauthorizedError, match="algorithm is not allowed"):
        _make_authenticator().authenticate(_req(token))

    assert jwks_route.call_count == 0


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
