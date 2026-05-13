"""OIDC/JWKS validator for management-plane auth (Clerk default per OQ-2).

Validates JWT bearer tokens against a configured JWKS endpoint. Caches keys with
configurable TTL and re-fetches on `kid` not found.
"""
from __future__ import annotations

import httpx

from tessera.auth._jwks import JWKSCache, validate_jwt
from tessera.auth.base import SCOPE_RE, AuthContext, Authenticator
from tessera.errors import UnauthorizedError


class OIDCAuthenticator:
    """Implements the Authenticator Protocol.

    Configured per cfg.auth.management_plane: provider, jwks_url, issuer,
    audience, clock_skew_seconds, scope_claim.
    """

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        clock_skew_seconds: int = 60,
        scope_claim: str = "email",
        provider: str = "clerk",
        deployment_id: str = "default",
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._clock_skew = clock_skew_seconds
        self._scope_claim = scope_claim
        self._provider = provider
        self._deployment_id = deployment_id
        self._cache: JWKSCache | None = None
        self._http = httpx.Client(timeout=10.0)

    def authenticate(self, request) -> AuthContext:
        # Extract Bearer token
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

        principal_id = claims.get("sub", "unknown")
        scope_value = claims.get(self._scope_claim) or self._deployment_id
        # Normalize email-style claims to slug for SCOPE_RE compliance
        scope_slug = str(scope_value).replace("@", "_at_").replace(".", "_").lower()
        if not SCOPE_RE.match(scope_slug):
            scope_slug = self._deployment_id

        return AuthContext(
            principal_id=str(principal_id),
            scope=scope_slug,
            metadata={"provider": self._provider, "claims": claims},
        )


# Satisfy the Authenticator Protocol at type-check time
_: Authenticator = OIDCAuthenticator.__new__(OIDCAuthenticator)  # type: ignore[assignment]
