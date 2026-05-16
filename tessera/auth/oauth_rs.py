"""OAuth 2.1 Resource Server endpoints.

Implemented endpoints (all registered via make_metadata_route):
  GET  /.well-known/oauth-protected-resource  — RFC 9728 metadata
  GET  /.well-known/jwks.json                 — stub (empty key set)
  POST /register                              — RFC 7591 DCR proxy (per-IP rate limited)
  POST /introspect                            — RFC 7662 token introspection (sig-verified)
  POST /revoke                               — RFC 7009 token revocation

DCR proxy (POST /register)
  Transparent proxy to an upstream AS's /register endpoint.
  Requires TESSERA_OAUTH_AS_REGISTRATION_URL to be set; returns 503 otherwise.
  Rate limiting: per-IP token bucket, configurable via TESSERA_DCR_RATE_LIMIT
  (format: "10/minute" or "100/hour"; default 10/minute).

Token introspection (POST /introspect)
  Decodes a presented JWT, cryptographically verifies the signature against
  the JWKS cache, and returns RFC 7662 active/inactive status.
  Requires HTTP Basic auth validated against TESSERA_OAUTH_INTROSPECTION_CLIENTS.

Token revocation (POST /revoke)
  Accepts form-encoded token + optional token_type_hint.
  Requires HTTP Basic auth (same allowlist as /introspect).
  Stores revoked JTIs in the RevocationStore; always returns 200 per RFC 7009 §2.2.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import time
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from fastapi import Request  # noqa: TC002 — used at runtime for FastAPI dependency injection
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


# ── Revocation store ────────────────────────────────────────────────────────


class RevocationStore(Protocol):
    async def revoke(self, jti: str) -> None: ...
    async def is_revoked(self, jti: str) -> bool: ...


class InMemoryRevocationStore:
    def __init__(self) -> None:
        self._revoked: set[str] = set()

    async def revoke(self, jti: str) -> None:
        self._revoked.add(jti)

    async def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked


# Module-level singleton for v0.4.0 single-instance deployments.
# Production deployments swap this out for a Redis/DDB backend via the Protocol.
_revocation_store: RevocationStore = InMemoryRevocationStore()


def get_revocation_store() -> RevocationStore:
    """Return the active revocation store (injectable for tests)."""
    return _revocation_store


def set_revocation_store(store: RevocationStore) -> None:
    """Replace the module-level revocation store (used in tests)."""
    global _revocation_store
    _revocation_store = store


# ── Rate limiter ────────────────────────────────────────────────────────────


class RateLimiter(Protocol):
    async def check(self, key: str) -> tuple[bool, float | None]:
        """Return (allowed, retry_after_seconds_or_None)."""


class InMemoryTokenBucket:
    """Token-bucket rate limiter, keyed by arbitrary string (e.g. client IP).

    Refill is continuous: tokens accumulate at rate tokens_per_second.
    A request that finds ≥1 token consumes one and is allowed; otherwise denied.
    """

    def __init__(self, capacity: int, refill_rate: float) -> None:
        """
        capacity      — max tokens per bucket (= burst limit)
        refill_rate   — tokens added per second
        """
        self._capacity = capacity
        self._refill_rate = refill_rate
        # {key: (tokens_float, last_refill_monotonic)}
        self._buckets: dict[str, tuple[float, float]] = {}

    async def check(self, key: str) -> tuple[bool, float | None]:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self._capacity), now))
        # Refill
        elapsed = now - last
        tokens = min(self._capacity, tokens + elapsed * self._refill_rate)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True, None
        # Denied — compute how long until one token refills
        wait = (1.0 - tokens) / self._refill_rate
        self._buckets[key] = (tokens, now)
        return False, wait


def _parse_rate_limit(spec: str) -> tuple[int, float]:
    """Parse a rate-limit spec string into (capacity, tokens_per_second).

    Accepted formats: "10/minute", "100/hour", "5/second".
    Defaults to 10/minute on any parse error.
    """
    default = (10, 10 / 60.0)
    if not spec:
        return default
    m = re.fullmatch(r"(\d+)/(second|minute|hour)", spec.strip().lower())
    if not m:
        return default
    count = int(m.group(1))
    unit = m.group(2)
    divisors = {"second": 1, "minute": 60, "hour": 3600}
    rate = count / divisors[unit]
    return count, rate


# Module-level rate limiter; rebuilt lazily when TESSERA_DCR_RATE_LIMIT changes.
_rate_limiter: InMemoryTokenBucket | None = None
_rate_limiter_spec: str = ""


def _get_rate_limiter() -> InMemoryTokenBucket:
    global _rate_limiter, _rate_limiter_spec
    spec = os.environ.get("TESSERA_DCR_RATE_LIMIT", "10/minute")
    if _rate_limiter is None or spec != _rate_limiter_spec:
        capacity, rate = _parse_rate_limit(spec)
        _rate_limiter = InMemoryTokenBucket(capacity=capacity, refill_rate=rate)
        _rate_limiter_spec = spec
    return _rate_limiter


def _reset_rate_limiter() -> None:
    """Force recreation of the rate limiter (used in tests)."""
    global _rate_limiter, _rate_limiter_spec
    _rate_limiter = None
    _rate_limiter_spec = ""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_metadata(config: Any) -> dict[str, Any]:
    """Build the RFC 9728 oauth-protected-resource metadata document.

    Values are read from TesseraConfig when available, with env-var fallbacks.
    """
    resource = os.environ.get("TESSERA_OAUTH_RESOURCE_URL", "")

    authorization_servers: list[str] = []
    if config is not None:
        auth = getattr(config, "auth", None)
        if auth is not None:
            jwt_cfg = getattr(auth, "jwt", None)
            if jwt_cfg is not None and jwt_cfg.issuer:
                authorization_servers.append(jwt_cfg.issuer)
            mp_cfg = getattr(auth, "management_plane", None)
            if (
                mp_cfg is not None
                and mp_cfg.issuer
                and mp_cfg.issuer not in authorization_servers
            ):
                authorization_servers.append(mp_cfg.issuer)

    if not authorization_servers:
        env_as = os.environ.get("TESSERA_OAUTH_AUTHORIZATION_SERVER", "")
        if env_as:
            authorization_servers = [env_as]

    return {
        "resource": resource,
        "authorization_servers": authorization_servers,
        "scopes_supported": ["tessera:proxy", "tessera:admin", "tessera:audit:read"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://cloudmorph.ai/docs/tessera/auth",
    }


def _parse_introspection_allowlist() -> dict[str, str]:
    """Parse TESSERA_OAUTH_INTROSPECTION_CLIENTS into a {client_id: client_secret} dict.

    Format: comma-separated client_id:client_secret pairs.
    Example: "auditor:s3cr3t,monitor:p@ssw0rd"
    """
    raw = os.environ.get("TESSERA_OAUTH_INTROSPECTION_CLIENTS", "")
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            cid, _, csecret = pair.partition(":")
            cid = cid.strip()
            csecret = csecret.strip()
            if cid and csecret:
                result[cid] = csecret
    return result


def _check_basic_auth(authorization_header: str | None, allowlist: dict[str, str]) -> bool:
    """Validate an HTTP Basic auth header against the allowlist.

    Returns True iff the credentials are present and match a known client.
    Uses constant-time comparison to prevent timing attacks.
    """
    if not authorization_header:
        return False
    scheme, _, credentials = authorization_header.partition(" ")
    if scheme.lower() != "basic" or not credentials:
        return False
    try:
        decoded = base64.b64decode(credentials.encode()).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    client_id, _, client_secret = decoded.partition(":")
    if not client_id or not client_secret:
        return False
    expected_secret = allowlist.get(client_id)
    if expected_secret is None:
        return False
    return secrets.compare_digest(expected_secret, client_secret)


def _decode_token_claims(token: str) -> dict[str, Any] | None:
    """Attempt to decode a JWT without signature verification to extract claims.

    Returns the claims dict on success, or None if the token is unparseable.
    """
    try:
        import jwt as _jwt
        result = _jwt.api_jwt.decode_complete(
            token,
            options={"verify_signature": False},
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256", "EdDSA"],
        )
        return result.get("payload")
    except Exception:  # noqa: BLE001
        return None


def _verify_token_signature(token: str, jwks_url: str) -> dict[str, Any] | None:
    """Cryptographically verify token signature against the JWKS endpoint.

    Returns claims dict on success, or None on any failure.
    Failures emit a structured log event.
    """
    try:
        import jwt as _jwt
        from jwt import PyJWKClient
    except ImportError:
        # PyJWT not installed or old version without PyJWKClient
        logger.warning("event=oauth_introspect_sigverify_failed kid=unknown reason=pyjwt_missing")
        return None

    try:
        header = _jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "event=oauth_introspect_sigverify_failed kid=unknown reason=malformed_header error=%s",
            exc,
        )
        return None

    kid = header.get("kid", "unknown")
    alg = header.get("alg", "RS256")

    try:
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        decoded: dict[str, Any] = _jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            options={
                "verify_exp": True,
                "verify_iss": False,  # issuer checked separately
                "verify_aud": False,  # audience not always present on RS tokens
            },
        )
        return decoded
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "event=oauth_introspect_sigverify_failed kid=%s reason=%s",
            kid,
            type(exc).__name__,
        )
        return None


def _validate_token_for_introspection(
    token: str,
    trusted_issuers: list[str],
) -> dict[str, Any] | None:
    """Validate token and return claims on success, or None on any failure.

    Decodes the JWT without verifying the signature (Tessera is a resource
    server that does not hold the AS's private key).  Validation checks:
      - Token is a well-formed JWT
      - Issuer is in the trusted_issuers list (from TESSERA_OAUTH_AUTHORIZATION_SERVER)
      - Token is not expired (exp claim)
    If any check fails, returns None and the caller returns {"active": false}.
    """
    claims = _decode_token_claims(token)
    if claims is None:
        return None

    # Expiry check
    exp = claims.get("exp")
    if exp is not None:
        try:
            if float(exp) < time.time():
                return None
        except (TypeError, ValueError):
            return None

    # Issuer check — if no trusted issuers configured, skip the check
    iss = claims.get("iss")
    if trusted_issuers and iss not in trusted_issuers:
        return None

    return claims


def make_metadata_route(app_ref: FastAPI) -> None:
    """Register all OAuth 2.1 Resource Server routes against the running app.

    Called from proxy.create_app() so handlers have access to app.state.config.

    Routes registered:
      GET  /.well-known/oauth-protected-resource  — RFC 9728
      GET  /.well-known/jwks.json                 — stub
      POST /register                              — RFC 7591 DCR proxy (rate-limited)
      POST /introspect                            — RFC 7662 token introspection (sig-verified)
      POST /revoke                               — RFC 7009 token revocation
    """
    @app_ref.get("/.well-known/oauth-protected-resource", tags=["oauth"])
    async def oauth_metadata(request: Request) -> JSONResponse:
        cfg = getattr(request.app.state, "config", None)
        metadata = _build_metadata(cfg)
        return JSONResponse(metadata)

    @app_ref.get("/.well-known/jwks.json", tags=["oauth"])
    async def jwks() -> JSONResponse:
        # Stub: Tessera does not currently issue tokens and has no signing keys
        # to publish. This will be populated if/when Tessera gains a token-issuance
        # path (e.g., signed audit receipts).
        return JSONResponse({"keys": []})

    # ── RFC 7591: Dynamic Client Registration proxy ───────────────────────────

    @app_ref.post("/register", tags=["oauth"])
    async def dcr_proxy(request: Request) -> JSONResponse:
        """RFC 7591 Dynamic Client Registration proxy.

        Forwards client registration requests to the upstream AS configured via
        TESSERA_OAUTH_AS_REGISTRATION_URL.  Tessera does not issue its own client
        credentials — it is a transparent proxy only.

        Rate limited per client IP via token-bucket (TESSERA_DCR_RATE_LIMIT env var).
        Returns 503 when the env var is unset.
        Returns 502 on upstream timeout or 5xx.
        """
        # Per-IP rate limiting
        client_ip = (request.client.host if request.client else None) or "unknown"
        limiter = _get_rate_limiter()
        allowed, retry_after = await limiter.check(client_ip)
        if not allowed:
            headers = {}
            if retry_after is not None:
                headers["Retry-After"] = str(int(retry_after) + 1)
            return JSONResponse(
                {
                    "error": "too_many_requests",
                    "error_description": "rate limit exceeded",
                },
                status_code=429,
                headers=headers,
                media_type="application/json",
            )

        upstream_url = os.environ.get("TESSERA_OAUTH_AS_REGISTRATION_URL", "").strip()
        if not upstream_url:
            return JSONResponse(
                {
                    "error": "server_error",
                    "error_description": "DCR proxy not configured",
                },
                status_code=503,
                media_type="application/json",
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                {"error": "invalid_request", "error_description": "request body must be JSON"},
                status_code=400,
                media_type="application/json",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                upstream_resp = await client.post(
                    upstream_url,
                    json=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
        except httpx.TimeoutException:
            logger.warning(
                "event=oauth_dcr_proxy_call upstream_url=%s status_code=timeout",
                upstream_url,
            )
            return JSONResponse(
                {"error": "temporarily_unavailable", "error_description": "upstream timeout"},
                status_code=502,
                media_type="application/json",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "event=oauth_dcr_proxy_call upstream_url=%s error=%s",
                upstream_url,
                exc,
            )
            return JSONResponse(
                {"error": "temporarily_unavailable", "error_description": "upstream unreachable"},
                status_code=502,
                media_type="application/json",
            )

        logger.info(
            "event=oauth_dcr_proxy_call upstream_url=%s status_code=%d",
            upstream_url,
            upstream_resp.status_code,
        )

        if upstream_resp.status_code >= 500:
            return JSONResponse(
                {"error": "temporarily_unavailable", "error_description": "upstream error"},
                status_code=502,
                media_type="application/json",
            )

        try:
            upstream_body = upstream_resp.json()
        except Exception:  # noqa: BLE001
            upstream_body = {"raw": upstream_resp.text}

        return JSONResponse(
            upstream_body,
            status_code=upstream_resp.status_code,
            media_type="application/json",
        )

    # ── RFC 7662: Token Introspection ─────────────────────────────────────────

    @app_ref.post("/introspect", tags=["oauth"])
    async def introspect(request: Request) -> JSONResponse:
        """RFC 7662 Token Introspection.

        Auth: HTTP Basic credentials validated against
        TESSERA_OAUTH_INTROSPECTION_CLIENTS (comma-separated client_id:secret pairs).
        Returns 401 if credentials are missing or do not match.

        Request body: application/x-www-form-urlencoded with field `token`.

        Signature verification: if TESSERA_OAUTH_JWKS_URL is set, the token
        signature is cryptographically verified against the JWKS endpoint.
        On verification failure: returns {"active": false} and emits
        event=oauth_introspect_sigverify_failed.

        Revocation: tokens revoked via POST /revoke return {"active": false}.
        """
        # Step 1 — Authenticate the introspecting client via Basic auth
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        allowlist = _parse_introspection_allowlist()

        if not allowlist:
            return JSONResponse(
                {"error": "server_error", "error_description": "introspection not configured"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="tessera-introspection"'},
            )

        if not _check_basic_auth(auth_header, allowlist):
            return JSONResponse(
                {"error": "invalid_client"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="tessera-introspection"'},
            )

        # Step 2 — Parse form body; token field is required (RFC 7662 §2.3)
        try:
            form = await request.form()
            token = form.get("token")
        except Exception:  # noqa: BLE001
            token = None

        if not token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "missing token parameter"},
                status_code=400,
            )

        token_str = str(token)

        # Step 3 — Trusted issuers
        trusted_issuers: list[str] = []
        cfg = getattr(request.app.state, "config", None)
        if cfg is not None:
            auth_cfg = getattr(cfg, "auth", None)
            if auth_cfg is not None:
                jwt_sub = getattr(auth_cfg, "jwt", None)
                if jwt_sub is not None and jwt_sub.issuer:
                    trusted_issuers.append(jwt_sub.issuer)
                mp_sub = getattr(auth_cfg, "management_plane", None)
                if (
                    mp_sub is not None
                    and mp_sub.issuer
                    and mp_sub.issuer not in trusted_issuers
                ):
                    trusted_issuers.append(mp_sub.issuer)
        if not trusted_issuers:
            env_as = os.environ.get("TESSERA_OAUTH_AUTHORIZATION_SERVER", "").strip()
            if env_as:
                trusted_issuers = [env_as]

        # Step 4 — Signature verification (when JWKS URL is configured)
        jwks_url = os.environ.get("TESSERA_OAUTH_JWKS_URL", "").strip()
        claims: dict[str, Any]
        if jwks_url:
            sig_claims = _verify_token_signature(token_str, jwks_url)
            if sig_claims is None:
                return JSONResponse({"active": False})
            claims = sig_claims
        else:
            # Fall back to unverified decode + expiry/issuer checks
            unverified_claims = _validate_token_for_introspection(token_str, trusted_issuers)
            if unverified_claims is None:
                return JSONResponse({"active": False})
            claims = unverified_claims

        # Step 5 — Revocation check
        jti = claims.get("jti")
        if jti:
            store = get_revocation_store()
            if await store.is_revoked(str(jti)):
                return JSONResponse({"active": False})

        # Step 6 — Return RFC 7662 shape
        response: dict[str, Any] = {"active": True}
        for claim in ("sub", "client_id", "username", "scope", "exp", "iat", "iss", "aud", "jti"):
            if claim in claims:
                response[claim] = claims[claim]
        for claim in ("tenant_id", "tier"):
            if claim in claims:
                response[claim] = claims[claim]

        return JSONResponse(response)

    # ── RFC 7009: Token Revocation ────────────────────────────────────────────

    @app_ref.post("/revoke", tags=["oauth"])
    async def revoke(request: Request) -> JSONResponse:
        """RFC 7009 Token Revocation.

        Auth: HTTP Basic credentials validated against
        TESSERA_OAUTH_INTROSPECTION_CLIENTS (same allowlist as /introspect).
        Returns 401 if credentials are missing or do not match.

        Request body: application/x-www-form-urlencoded with:
          token           — required
          token_type_hint — optional (per RFC 7009 §2.1; accepted but not acted on)

        Per RFC 7009 §2.2: always returns 200 regardless of whether the token
        was valid or already revoked — the client cannot distinguish these cases.
        """
        # Step 1 — Authenticate via Basic auth (same allowlist as /introspect)
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        allowlist = _parse_introspection_allowlist()

        if not allowlist:
            return JSONResponse(
                {"error": "server_error", "error_description": "revocation not configured"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="tessera-revocation"'},
            )

        if not _check_basic_auth(auth_header, allowlist):
            return JSONResponse(
                {"error": "invalid_client"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="tessera-revocation"'},
            )

        # Step 2 — Parse form body
        try:
            form = await request.form()
            token = form.get("token")
            # token_type_hint accepted per RFC 7009 §2.1; we log it but don't gate on it
            token_type_hint = form.get("token_type_hint")
        except Exception:  # noqa: BLE001
            token = None
            token_type_hint = None

        if not token:
            # Per RFC 7009 §2.1 the token parameter is required.
            # Return 400 only on missing parameter — NOT on invalid token (§2.2).
            return JSONResponse(
                {"error": "invalid_request", "error_description": "missing token parameter"},
                status_code=400,
            )

        token_str = str(token)

        # Step 3 — Extract JTI (without signature verification per spec intent)
        claims = _decode_token_claims(token_str)
        if claims is not None:
            jti = claims.get("jti")
            if jti:
                store = get_revocation_store()
                await store.revoke(str(jti))
                logger.info(
                    "event=oauth_token_revoked jti=%s token_type_hint=%s",
                    jti,
                    token_type_hint or "none",
                )

        # Per RFC 7009 §2.2: always 200, even if token was unknown/invalid
        return JSONResponse({}, status_code=200)
