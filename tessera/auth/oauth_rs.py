"""OAuth 2.1 Resource Server endpoints + bearer-token validator.

Implemented endpoints (all registered via make_metadata_route):
  GET  /.well-known/oauth-protected-resource  — RFC 9728 metadata
  GET  /.well-known/jwks.json                 — stub (empty key set)
  POST /register                              — RFC 7591 DCR proxy (per-IP rate limited)
  POST /introspect                            — RFC 7662 token introspection (sig-verified)
  POST /revoke                               — RFC 7009 token revocation

Module-level API (v0.7.0 Item D §7.1):
  class OAuthResourceServer       — verifies Bearer tokens issued by the
                                    tessera.cloudmorph.ai authorization
                                    server (EdDSA + Ed25519). Local pubkey
                                    verify with JWKS-URL fallback. Verified
                                    tokens cached 5min by SHA-256(token).
  get_oauth_resource_server()     — module-level singleton accessor
  set_oauth_resource_server()     — test injection point
  require_scope(scope)            — FastAPI dependency that gates a route
                                    on `scope` being present on the token

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
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import httpx
from fastapi import HTTPException, Request  # noqa: TC002 — used at runtime
from fastapi.responses import JSONResponse

from tessera.errors import UnauthorizedError

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


_REVOCATION_TABLE = """
CREATE TABLE IF NOT EXISTS revoked_jtis (
  jti         TEXT PRIMARY KEY,
  revoked_at  TEXT NOT NULL
);
"""


class SqliteRevocationStore:
    """SQLite-backed revocation store — survives process restarts.

    The database file is created at ``path`` on first open.  ``path``
    is derived from the audit DB path by replacing the filename with
    ``revocation.db`` in the same directory, matching
    ``AuditConfig.path`` conventions.  Callers may also pass an
    explicit path for testing.

    Thread-safe: all writes are protected by a threading.Lock.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(_REVOCATION_TABLE)
            conn.commit()
        finally:
            conn.close()

    async def revoke(self, jti: str) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO revoked_jtis (jti, revoked_at) VALUES (?, ?)",
                    (jti, now),
                )
                conn.commit()
            finally:
                conn.close()

    async def is_revoked(self, jti: str) -> bool:
        conn = sqlite3.connect(self._path)
        try:
            row = conn.execute(
                "SELECT 1 FROM revoked_jtis WHERE jti = ? LIMIT 1", (jti,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def _default_revocation_db_path() -> str | None:
    """Return a path for the revocation DB, mirroring the audit DB location.

    Reads ``TESSERA_AUDIT_PATH`` env var first; if absent uses the
    ``AuditConfig`` default ``/var/lib/tessera/audit.db``.  Returns
    ``None`` when the parent directory does not exist and cannot be
    created (e.g. read-only container at import time).
    """
    audit_path = os.environ.get("TESSERA_AUDIT_PATH", "/var/lib/tessera/audit.db")
    parent = Path(audit_path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        return str(parent / "revocation.db")
    except OSError:
        return None


def _build_default_revocation_store() -> RevocationStore:
    """Return a SqliteRevocationStore when a writable path is available.

    Falls back to InMemoryRevocationStore and logs a warning when the
    persistence path cannot be created.
    """
    path = _default_revocation_db_path()
    if path is not None:
        try:
            store = SqliteRevocationStore(path)
            logger.info("event=revocation_store_sqlite path=%s", path)
            return store
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=revocation_store_sqlite_init_failed path=%s reason=%s "
                "falling_back_to=in_memory",
                path, exc,
            )
    logger.warning(
        "event=revocation_store_in_memory reason=no_persistence_path "
        "note=revocations_will_vanish_on_restart"
    )
    return InMemoryRevocationStore()


# Module-level singleton — SqliteRevocationStore by default when the
# persistence path is available; InMemoryRevocationStore as explicit fallback.
_revocation_store: RevocationStore = _build_default_revocation_store()


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


# ── v0.7.0 Item D §7.1 — OAuthResourceServer ────────────────────────────────


_DEFAULT_TESSERA_ISSUER = "tessera.cloudmorph.ai"
_DEFAULT_TESSERA_AUDIENCE = "tessera.cloudmorph.ai/api"
# v0.7.2: JWKS fallback URL points to the auth.tessera.cloudmorph.ai
# ApiMapping on tessera-api-prod. The `iss` claim of issued tokens stays
# `tessera.cloudmorph.ai` (unchanged — RFC-7519 issuer is an identifier,
# not a URL). Only the network-fetch URL for JWKS is updated.
_DEFAULT_TESSERA_JWKS_URL = "https://auth.tessera.cloudmorph.ai/oauth/jwks.json"
_DEFAULT_PUBKEY_PATH = str(Path(__file__).parent / "oauth_pubkey.pem")
_TOKEN_CACHE_TTL_SECONDS = 300
_TOKEN_CACHE_MAX_SIZE = 1024


class OAuthResourceServer:
    """Verifier for Bearer tokens issued by tessera.cloudmorph.ai.

    Two verification paths:
      1. Local: parse the bundled `oauth_pubkey.pem` once at init, then verify
         every token against that Ed25519 public key. Cheapest path; no
         network. Used in the steady state.
      2. JWKS-URL fallback: if the bundled pubkey is missing/invalid OR a
         local-verify signature failure suggests `kid` rotation, fall through
         to the live JWKS endpoint via PyJWKClient (which caches per-kid).

    Verified claims are cached by SHA-256(token) for 5 minutes (raw tokens
    are never stored). A bounded dict (max 1024 entries) keeps memory finite
    under token-stuffing scenarios.

    Configuration is sourced from env vars (overridable at construction):
        TESSERA_OAUTH_ISSUER         (default: "tessera.cloudmorph.ai")
        TESSERA_OAUTH_AUDIENCE       (default: "tessera.cloudmorph.ai/api")
        TESSERA_OAUTH_JWKS_FALLBACK  (default: "https://auth.tessera.cloudmorph.ai/oauth/jwks.json")
        TESSERA_OAUTH_PUBKEY_PATH    (default: bundled tessera/auth/oauth_pubkey.pem)
    """

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_url: str | None = None,
        pubkey_path: str | None = None,
    ) -> None:
        self._issuer = issuer or os.environ.get("TESSERA_OAUTH_ISSUER", _DEFAULT_TESSERA_ISSUER)
        self._audience = audience or os.environ.get("TESSERA_OAUTH_AUDIENCE", _DEFAULT_TESSERA_AUDIENCE)
        self._jwks_url = jwks_url or os.environ.get("TESSERA_OAUTH_JWKS_FALLBACK", _DEFAULT_TESSERA_JWKS_URL)
        self._pubkey_path = pubkey_path or os.environ.get("TESSERA_OAUTH_PUBKEY_PATH", _DEFAULT_PUBKEY_PATH)
        self._local_pubkey: Any = self._try_load_pubkey()
        # Lazy — created on first JWKS path use
        self._jwks_client: Any = None
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._cache_lock = threading.Lock()

    def _try_load_pubkey(self) -> Any:
        """Load oauth_pubkey.pem → Ed25519PublicKey. Returns None on any failure.

        Failure-is-None (not raise) so the resource server still works in dev
        environments where the placeholder pubkey ships — those deployments
        verify via the JWKS fallback path.
        """
        try:
            with open(self._pubkey_path, "rb") as fh:
                pem = fh.read()
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            key = serialization.load_pem_public_key(pem)
            if not isinstance(key, Ed25519PublicKey):
                logger.warning(
                    "event=oauth_rs_pubkey_wrong_type path=%s type=%s",
                    self._pubkey_path,
                    type(key).__name__,
                )
                return None
            logger.info("event=oauth_rs_pubkey_loaded path=%s", self._pubkey_path)
            return key
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "event=oauth_rs_pubkey_load_failed path=%s reason=%s",
                self._pubkey_path,
                type(exc).__name__,
            )
            return None

    def _cache_lookup(self, token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._cache_lock:
            entry = self._cache.get(token_hash)
            if entry is None:
                return None
            claims, expires_at = entry
            if time.monotonic() >= expires_at:
                self._cache.pop(token_hash, None)
                return None
            return claims

    def _cache_store(self, token: str, claims: dict[str, Any]) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._cache_lock:
            if len(self._cache) >= _TOKEN_CACHE_MAX_SIZE:
                # Bounded eviction — drop one arbitrary entry rather than
                # building a full LRU. Cache will rewarm naturally.
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[token_hash] = (
                claims,
                time.monotonic() + _TOKEN_CACHE_TTL_SECONDS,
            )

    def validate_bearer_token(self, token: str) -> dict[str, Any]:
        """Verify the token and return its claims dict.

        Raises ``UnauthorizedError`` on any failure (expired, bad signature,
        wrong issuer/audience, unreachable JWKS, etc.). Cached verifications
        skip the cryptographic check entirely for 5 minutes.
        """
        if not token or not isinstance(token, str):
            raise UnauthorizedError("missing bearer token")

        cached = self._cache_lookup(token)
        if cached is not None:
            return cached

        import jwt as _jwt
        from jwt.exceptions import InvalidSignatureError

        decode_kwargs: dict[str, Any] = {
            "algorithms": ["EdDSA"],
            "audience": self._audience,
            "issuer": self._issuer,
            "options": {"verify_exp": True, "verify_iss": True, "verify_aud": True},
        }

        # Path 1 — local pubkey verify
        if self._local_pubkey is not None:
            try:
                claims = _jwt.decode(token, self._local_pubkey, **decode_kwargs)
                self._cache_store(token, claims)
                return claims
            except InvalidSignatureError:
                # Possibly a kid rotation we haven't rebundled yet — fall through
                # to the JWKS fetch path. All other PyJWT errors mean the token
                # is genuinely invalid (expired, wrong aud, etc.) — raise.
                logger.info("event=oauth_rs_local_sig_fail jwks_retry=1")
            except Exception as exc:  # noqa: BLE001
                raise UnauthorizedError(f"token verification failed: {exc}") from exc

        # Path 2 — JWKS fallback
        try:
            from jwt import PyJWKClient

            if self._jwks_client is None:
                self._jwks_client = PyJWKClient(self._jwks_url)
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = _jwt.decode(token, signing_key.key, **decode_kwargs)
            self._cache_store(token, claims)
            return claims
        except UnauthorizedError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UnauthorizedError(
                f"token verification failed (jwks fallback): {exc}"
            ) from exc


# ── Module-level singleton + injection points ───────────────────────────────


_oauth_resource_server: OAuthResourceServer | None = None


def get_oauth_resource_server() -> OAuthResourceServer:
    """Return the active OAuthResourceServer (lazy-initialized)."""
    global _oauth_resource_server
    if _oauth_resource_server is None:
        _oauth_resource_server = OAuthResourceServer()
    return _oauth_resource_server


def set_oauth_resource_server(rs: OAuthResourceServer | None) -> None:
    """Test injection point — pass None to force re-initialization."""
    global _oauth_resource_server
    _oauth_resource_server = rs


# ── require_scope FastAPI dependency ────────────────────────────────────────


def require_scope(scope: str) -> Callable[[Request], dict[str, Any]]:
    """FastAPI dependency that enforces a single OAuth scope.

    Example:
        @app.post("/audit/ingest")
        async def ingest(claims = Depends(require_scope("tessera:audit:write"))):
            ...

    Returns the verified claims dict on success. Raises HTTPException 401
    if the Authorization header is missing/malformed, 403 if the scope is
    not present on an otherwise-valid token.
    """

    async def _dep(request: Request) -> dict[str, Any]:
        header = (
            request.headers.get("Authorization")
            or request.headers.get("authorization")
            or ""
        )
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = header[7:].strip()
        try:
            claims = get_oauth_resource_server().validate_bearer_token(token)
        except UnauthorizedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        scopes = set((claims.get("scope") or "").split())
        if scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"insufficient scope: {scope!r} required",
            )
        return claims

    return _dep


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
