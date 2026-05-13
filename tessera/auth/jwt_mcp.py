"""JWT validator mode for MCP traffic.

A thin authenticator that validates Bearer JWTs from any OIDC-compatible provider
(Entra, Okta, Cognito, etc.) against a configured JWKS endpoint. Intended for
deployments where MCP clients authenticate with JWTs rather than static bearer
tokens.
"""
from __future__ import annotations

import httpx

from tessera.auth._jwks import JWKSCache, validate_jwt
from tessera.auth.base import SCOPE_RE, AuthContext, Authenticator
from tessera.errors import UnauthorizedError


class JWTAuthenticator:
    """Implements the Authenticator Protocol for JWT-authenticated MCP traffic.

    Configured via cfg.auth.jwt: jwks_url, issuer, audience, clock_skew_seconds,
    principal_claim, scope_claim.
    """

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        clock_skew_seconds: int = 60,
        principal_claim: str = "sub",
        scope_claim: str = "scope",
        deployment_id: str = "default",
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._clock_skew = clock_skew_seconds
        self._principal_claim = principal_claim
        self._scope_claim = scope_claim
        self._deployment_id = deployment_id
        self._cache: JWKSCache | None = None
        self._http = httpx.Client(timeout=10.0)

    def authenticate(self, request) -> AuthContext:
        header = request.headers.get("Authorization") or request.headers.get("authorization")
        if not header or not header.lower().startswith("bearer "):
            raise UnauthorizedError("missing or malformed Authorization header")
        token = header[7:].strip()

        claims, self._cache = validate_jwt(
            token=token,
            jwks_url=self._jwks_url,
            issuer=self._issuer,
            audience=self._audience,
            clock_skew_seconds=self._clock_skew,
            http_client=self._http,
            cache=self._cache,
        )

        principal_id = str(claims.get(self._principal_claim, "unknown"))

        # scope claim may be a space-separated string (OAuth 2.0 style) or any value
        raw_scope = claims.get(self._scope_claim) or self._deployment_id
        scope_slug = str(raw_scope).split()[0] if raw_scope else self._deployment_id
        # Ensure SCOPE_RE compliance; fall back to deployment_id
        if not SCOPE_RE.match(scope_slug):
            scope_slug = self._deployment_id

        return AuthContext(
            principal_id=principal_id,
            scope=scope_slug,
            metadata={"jwt_provider": "external", "claims": claims},
        )


# Satisfy the Authenticator Protocol at type-check time
_: Authenticator = JWTAuthenticator.__new__(JWTAuthenticator)  # type: ignore[assignment]
