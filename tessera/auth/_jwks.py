"""Shared JWKS fetch and JWT validation helper.

Used by both OIDCAuthenticator (management-plane) and JWTAuthenticator (MCP
traffic) so JWKS caching and validation logic lives in one place.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from tessera.errors import UnauthorizedError

logger = logging.getLogger(__name__)


@dataclass
class JWKSCache:
    keys: dict[str, dict[str, Any]]  # kid -> JWK dict
    fetched_at: float
    ttl_seconds: int = 3600


def fetch_jwks(jwks_url: str, http_client: httpx.Client) -> JWKSCache:
    """Fetch JWKS from *jwks_url* synchronously and return a populated cache entry."""
    try:
        resp = http_client.get(jwks_url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise UnauthorizedError(f"JWKS endpoint returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise UnauthorizedError(f"JWKS endpoint unreachable: {exc}") from exc
    data = resp.json()
    keys = {k["kid"]: k for k in data.get("keys", []) if "kid" in k}
    return JWKSCache(keys=keys, fetched_at=time.monotonic())


async def fetch_jwks_async(jwks_url: str) -> JWKSCache:
    """Fetch JWKS from *jwks_url* asynchronously and return a populated cache entry."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise UnauthorizedError(f"JWKS endpoint returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise UnauthorizedError(f"JWKS endpoint unreachable: {exc}") from exc
    data = resp.json()
    keys = {k["kid"]: k for k in data.get("keys", []) if "kid" in k}
    return JWKSCache(keys=keys, fetched_at=time.monotonic())


async def prewarm_jwks_cache(jwks_url: str) -> JWKSCache | None:
    """Fetch JWKS once at lifespan startup to pre-populate the cache.

    On failure: logs WARNING and returns None. Callers should swallow None and
    fall through to the on-demand fetch path for the first real request.
    """
    try:
        cache = await fetch_jwks_async(jwks_url)
        logger.info("event=jwks_prewarm_ok url=%s keys=%d", jwks_url, len(cache.keys))
        return cache
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=jwks_prewarm_failed url=%s error=%s", jwks_url, exc)
        return None


def get_key(
    kid: str,
    cache: JWKSCache | None,
    jwks_url: str,
    http_client: httpx.Client,
) -> tuple[dict[str, Any], JWKSCache]:
    """Return the JWK dict for *kid*, refreshing cache as needed.

    Returns (key_dict, updated_cache).  Raises UnauthorizedError if the kid
    is not found after re-fetch.
    """
    if cache is None or (time.monotonic() - cache.fetched_at) > cache.ttl_seconds:
        cache = fetch_jwks(jwks_url, http_client)

    key = cache.keys.get(kid)
    if key is None:
        # Re-fetch on unknown kid (key rotation)
        cache = fetch_jwks(jwks_url, http_client)
        key = cache.keys.get(kid)

    if key is None:
        raise UnauthorizedError(f"unknown JWT kid: {kid}")
    return key, cache


def validate_jwt(
    token: str,
    jwks_url: str,
    issuer: str,
    audience: str,
    clock_skew_seconds: int,
    http_client: httpx.Client,
    cache: JWKSCache | None = None,
) -> tuple[dict[str, Any], JWKSCache]:
    """Validate *token* against the JWKS endpoint and return (claims, updated_cache).

    Raises UnauthorizedError on any validation failure.
    """
    try:
        unverified = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise UnauthorizedError(f"malformed JWT header: {exc}") from exc

    kid = unverified.get("kid")
    if not kid:
        raise UnauthorizedError("JWT header missing kid")

    key, cache = get_key(kid, cache, jwks_url, http_client)

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[unverified.get("alg", "RS256")],
            issuer=issuer,
            audience=audience,
            options={
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "leeway": clock_skew_seconds,
            },
        )
    except ExpiredSignatureError as exc:
        raise UnauthorizedError("JWT expired") from exc
    except JWTError as exc:
        raise UnauthorizedError(f"JWT validation failed: {exc}") from exc

    return claims, cache
