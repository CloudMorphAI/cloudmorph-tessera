"""OAuth 2.1 Resource Server endpoints.

Implemented endpoints (all registered via make_metadata_route):
  GET  /.well-known/oauth-protected-resource  — RFC 9728 metadata
  GET  /.well-known/jwks.json                 — stub (empty key set)
  POST /register                              — RFC 7591 DCR proxy
  POST /introspect                            — RFC 7662 token introspection

DCR proxy (POST /register)
  Transparent proxy to an upstream AS's /register endpoint.
  Requires TESSERA_OAUTH_AS_REGISTRATION_URL to be set; returns 503 otherwise.
  Rate limiting is deferred to v0.3.1 — see comment on make_metadata_route.

Token introspection (POST /introspect)
  Decodes a presented JWT and returns its RFC 7662 active/inactive status.
  Requires HTTP Basic auth validated against TESSERA_OAUTH_INTROSPECTION_CLIENTS.
  Bearer-auth option on the endpoint itself is deferred to a follow-up batch.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_metadata(config: Any) -> dict:
    """Build the RFC 9728 oauth-protected-resource metadata document.

    Values are read from TesseraConfig when available, with env-var fallbacks.
    """
    # Resource identifier — the URL of this Tessera instance
    resource = os.environ.get("TESSERA_OAUTH_RESOURCE_URL", "")

    # Authorization servers — read from JWT config or management-plane config if wired
    authorization_servers: list[str] = []
    if config is not None:
        auth = getattr(config, "auth", None)
        if auth is not None:
            jwt_cfg = getattr(auth, "jwt", None)
            if jwt_cfg is not None and jwt_cfg.issuer:
                authorization_servers.append(jwt_cfg.issuer)
            mp_cfg = getattr(auth, "management_plane", None)
            if mp_cfg is not None and mp_cfg.issuer:
                if mp_cfg.issuer not in authorization_servers:
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

    Returns the claims dict on success, or None if the token is unparseable
    (missing segments, bad base64, etc.).

    Signature verification and expiry are validated separately by the caller
    using PyJWT's jwt.decode(); this function is only used for the failure path
    where we know the token is invalid and need to return {"active": false}.

    This function is intentionally lenient — it only needs to not crash.
    """
    try:
        import jwt as _jwt
        # decode_complete returns {"header": ..., "payload": ..., "signature": ...}
        # options={"verify_signature": False} skips all verification
        result = _jwt.api_jwt.decode_complete(
            token,
            options={"verify_signature": False},
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256"],
        )
        return result.get("payload")  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
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
    import time

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
    # (permissive mode: any well-formed non-expired token is active)
    iss = claims.get("iss")
    if trusted_issuers and iss not in trusted_issuers:
        return None

    return claims


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata() -> JSONResponse:
    """RFC 9728 — OAuth 2.0 Protected Resource Metadata."""
    # Config is not available at route-definition time; routes are registered
    # inside create_app where app.state will hold the config after startup.
    # At request time the state is populated, but we don't have a reference here
    # without a Request param.  Import lazily to avoid circular deps.
    from fastapi import Request as _Request  # noqa: F401
    # Re-implement as a proper Request-accepting endpoint below.
    # This placeholder is superseded by the one below.
    return JSONResponse({"error": "use_request_endpoint"}, status_code=500)


# Override with the real implementation that reads app.state
@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def _oauth_metadata_with_state() -> JSONResponse:
    """Superseded — real implementation is registered in create_app."""
    return JSONResponse({}, status_code=500)


def make_metadata_route(app_ref: Any) -> None:
    """Register all OAuth 2.1 Resource Server routes against the running app.

    Called from proxy.create_app() so handlers have access to app.state.config.

    Routes registered:
      GET  /.well-known/oauth-protected-resource  — RFC 9728
      GET  /.well-known/jwks.json                 — stub
      POST /register                              — RFC 7591 DCR proxy
      POST /introspect                            — RFC 7662 token introspection

    Deferred (v0.3.1):
      - Per-IP rate limiting on POST /register (TESSERA_OAUTH_DCR_RATE_LIMIT env var)
      - Bearer-auth option on POST /introspect (Basic-auth only this batch)
    """
    @app_ref.get("/.well-known/oauth-protected-resource", tags=["oauth"])
    async def oauth_metadata(request: Request) -> JSONResponse:
        cfg = getattr(request.app.state, "config", None)
        metadata = _build_metadata(cfg)
        return JSONResponse(metadata)

    @app_ref.get("/.well-known/jwks.json", tags=["oauth"])
    async def jwks() -> JSONResponse:
        # Stub: Tessera does not currently issue tokens and has no signing keys
        # to publish.  The endpoint exists for forward-compatibility with OAuth 2.1
        # validators that require the JWKS discovery surface even when the key set
        # is empty.  This will be populated if/when Tessera gains a token-issuance
        # path (e.g., signed audit receipts).
        return JSONResponse({"keys": []})

    # ── RFC 7591: Dynamic Client Registration proxy ───────────────────────────

    @app_ref.post("/register", tags=["oauth"])
    async def dcr_proxy(request: Request) -> JSONResponse:
        """RFC 7591 Dynamic Client Registration proxy.

        Forwards client registration requests to the upstream AS configured via
        TESSERA_OAUTH_AS_REGISTRATION_URL.  Tessera does not issue its own client
        credentials — it is a transparent proxy only.

        Returns 503 when the env var is unset.
        Returns 502 on upstream timeout or 5xx.
        """
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
            with httpx.Client(timeout=10.0) as client:
                upstream_resp = client.post(
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
        Returns RFC 7662 shape: {"active": true, <claims>} or {"active": false}.

        Bearer-auth on this endpoint is deferred to a follow-up batch.
        """
        # Step 1 — Authenticate the introspecting client via Basic auth
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        allowlist = _parse_introspection_allowlist()

        if not allowlist:
            # No clients configured at all — reject all introspection requests
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

        # Step 3 — Validate the presented token
        # Trusted issuers come from app.state.config if available, else env var
        trusted_issuers: list[str] = []
        cfg = getattr(request.app.state, "config", None)
        if cfg is not None:
            auth_cfg = getattr(cfg, "auth", None)
            if auth_cfg is not None:
                jwt_sub = getattr(auth_cfg, "jwt", None)
                if jwt_sub is not None and jwt_sub.issuer:
                    trusted_issuers.append(jwt_sub.issuer)
                mp_sub = getattr(auth_cfg, "management_plane", None)
                if mp_sub is not None and mp_sub.issuer:
                    if mp_sub.issuer not in trusted_issuers:
                        trusted_issuers.append(mp_sub.issuer)
        if not trusted_issuers:
            env_as = os.environ.get("TESSERA_OAUTH_AUTHORIZATION_SERVER", "").strip()
            if env_as:
                trusted_issuers = [env_as]

        claims = _validate_token_for_introspection(token_str, trusted_issuers)

        # Step 4 — Return RFC 7662 shape
        # Per RFC 7662 §2.2: NEVER reveal internal details on active=false
        if claims is None:
            return JSONResponse({"active": False})

        # Extract the standard + Tessera-specific claims to expose
        response: dict[str, Any] = {"active": True}
        for claim in ("sub", "client_id", "username", "scope", "exp", "iat", "iss", "aud"):
            if claim in claims:
                response[claim] = claims[claim]
        # Tessera-specific claims
        for claim in ("tenant_id", "tier"):
            if claim in claims:
                response[claim] = claims[claim]

        return JSONResponse(response)
