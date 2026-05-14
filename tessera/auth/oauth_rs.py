"""OAuth 2.1 Resource Server endpoints (minimal viable surface).

Implemented endpoints:
  GET /.well-known/oauth-protected-resource  — RFC 9728 metadata
  GET /.well-known/jwks.json                 — stub (empty key set)

Deferred to a follow-up commit:
  POST /oauth/register      — RFC 7591 Dynamic Client Registration proxy
  POST /oauth/introspect    — RFC 7662 Token Introspection

The deferred endpoints require deciding whether Tessera proxies DCR to a
configured upstream IdP or rejects with 501, and whether introspection returns
claims from the cached JWT validation or calls out to the IdP for revocation
status.  Neither is a blocker for the current enterprise discovery conversation;
the metadata + JWKS endpoints satisfy OAuth 2.1 Resource Server discoverability.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
    """Register the metadata route against the running app after startup.

    Called from proxy.create_app() so the handler has access to app.state.config.
    """
    from fastapi import Request

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
