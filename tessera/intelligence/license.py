"""License validator for Tessera intelligence packs.

Checks the license server and caches results with a configurable TTL.
Falls back to cached value on server unreachability for up to license_cache_fallback_days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

_TIER_ORDER: dict[str, int] = {
    "free": 0,
    "developer": 1,
    "team": 2,
    "scale": 2,  # CRITICAL-5 (code-audit-2026-05-22): scale-tier customers were silently
                 # downgraded to free because this dict lacked "scale". Same rank as "team".
    "enterprise": 3,
}


@dataclass
class LicenseStatus:
    tier: Literal["free", "developer", "team", "scale", "enterprise"]
    expires_at: datetime | None
    seats: int
    customer_id: str | None
    from_cache: bool
    # Raw signed JWT returned by the license server. Forwarded to the CDN under
    # `X-Tessera-License` so the CloudFront tier-gating Function can read the
    # tier claim. A still-valid verified outage cache retains its token; None
    # when running unlicensed or when verification/authorization failed.
    jwt: str | None = None


class LicenseValidator:
    """Validates a TESSERA_LICENSE_KEY against the license server.

    Caches successful checks with a 24h TTL. On server unreachability, falls back
    to the cached value for up to license_cache_fallback_days.
    """

    _CACHE_TTL_SECONDS = 86400  # 24 hours

    def __init__(
        self,
        config: object,
        public_key_pem: bytes,
    ) -> None:
        from tessera.config import IntelligenceConfig
        self._config: IntelligenceConfig = config  # type: ignore[assignment]
        self._public_key_pem = public_key_pem
        self._cache_path = Path(self._config.cache_dir).expanduser() / "license.json"
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._in_memory_cache: LicenseStatus | None = None
        self._in_memory_ts: float = 0.0
        self._in_memory_key_hash: str | None = None
        # A failed disk write must not resurrect an older paid token after an
        # authoritative downgrade. Only verified reauthorization clears a hold.
        self._fallback_denied_keys: set[str] = set()

    def _license_key_hash(self) -> str | None:
        key = os.environ.get(self._config.license_key_env)
        return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else None

    def _free_status(self, from_cache: bool = False) -> LicenseStatus:
        return LicenseStatus(
            tier="free",
            expires_at=None,
            seats=1,
            customer_id=None,
            from_cache=from_cache,
        )

    def _load_cache(self) -> tuple[LicenseStatus | None, float]:
        """Load persisted license cache. Returns (status, timestamp) or (None, 0)."""
        if not self._cache_path.exists():
            return None, 0.0
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            ts = float(data.get("cached_at", 0))
            if not math.isfinite(ts) or not 0 <= ts <= time.time():
                return None, 0.0
            if data.get("license_key_hash") != self._license_key_hash():
                return None, 0.0
            # Cached JSON is not an authorization source. Reverify the persisted
            # token; legacy unsigned caches can only retain the free tier.
            status = self._verify_response({"token": data.get("jwt")})
            status.from_cache = True
            return status, ts
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=license_cache_load_failed path=%s error=%s", self._cache_path, exc)
            return None, 0.0

    def _persist_cache(self, status: LicenseStatus, ts: float) -> None:
        """Persist license status to disk."""
        data: dict[str, Any] = {
            "tier": status.tier,
            "expires_at": status.expires_at.isoformat() if status.expires_at else None,
            "seats": status.seats,
            "customer_id": status.customer_id,
            "cached_at": ts,
            "jwt": status.jwt,
            "license_key_hash": self._license_key_hash(),
        }
        try:
            self._cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=license_cache_write_failed path=%s error=%s", self._cache_path, exc)

    def _verify_response(self, response_data: dict[str, Any]) -> LicenseStatus:
        """Parse and validate the license server response.

        The server returns a JSON body with tier, expires_at, seats, customer_id.
        If a signed JWT is present under 'token' or legacy 'signed_jwt', its Ed25519 signature is verified
        against the bundled public key and the JWT claims take precedence. A token
        that fails verification raises — there is NO code path that trusts an
        unsigned or tampered response body at an elevated tier. A response with NO
        token is capped at the free tier (the license server always includes one,
        so a missing token means a tampered or non-genuine response). Callers treat
        a verification failure as an authoritative downgrade, without outage grace.
        """
        token = response_data.get("token")
        legacy_token = response_data.get("signed_jwt")
        if token and legacy_token and token != legacy_token:
            raise ValueError("Conflicting license JWT response fields")
        jwt_token = token or legacy_token
        if jwt_token and not isinstance(jwt_token, str):
            raise ValueError("License JWT must be a string")
        if jwt_token:
            claims = self._verify_jwt_ed25519(jwt_token)
            if not isinstance(claims, dict):
                raise ValueError("License JWT claims must be an object")
            exp = claims.get("exp")
            if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
                raise ValueError("License JWT requires a finite expiration")
            tier = claims.get("tier")
            if tier not in _TIER_ORDER:
                raise ValueError("License JWT requires a known signed tier")
            if claims.get("iss") not in (None, "license.tessera.cloudmorph.ai"):
                raise ValueError("Unexpected license JWT issuer")
            audience = claims.get("aud")
            if audience is not None and audience != "cloudmorph-tessera" and not (
                isinstance(audience, list) and "cloudmorph-tessera" in audience
            ):
                raise ValueError("Unexpected license JWT audience")
            deadline = float(exp)
            entitlement_expiry = claims.get("expires_at")
            if entitlement_expiry is not None:
                if not isinstance(entitlement_expiry, str):
                    raise ValueError("Invalid signed license expiry")
                entitlement = datetime.fromisoformat(entitlement_expiry.replace("Z", "+00:00"))
                if entitlement.tzinfo is None:
                    raise ValueError("Signed license expiry requires a timezone")
                deadline = min(deadline, entitlement.timestamp())
            if deadline <= time.time():
                raise ValueError("License JWT or entitlement has expired")
            expires_at = datetime.fromtimestamp(deadline, tz=UTC)
            seats = int(claims.get("seats", 1))
            if seats < 1:
                raise ValueError("License JWT requires positive seats")
            customer_id = claims.get("customer_id")
        else:
            # Fail closed: an unsigned response body must never grant a paid
            # tier. The license server always returns a signed `token`
            # (signTierJWT), so a missing token means a stripped, forged, or
            # non-genuine response — cap at free rather than trust the body.
            logger.warning(
                "event=license_response_unsigned action=tier_capped_free "
                "reason=no_ed25519_token_in_response"
            )
            tier = "free"
            expires_at = None
            seats = 1
            customer_id = None

        if tier not in _TIER_ORDER:
            tier = "free"

        return LicenseStatus(
            tier=tier,
            expires_at=expires_at,
            seats=seats,
            customer_id=customer_id,
            from_cache=False,
            jwt=jwt_token,
        )

    def _verify_jwt_ed25519(self, token: str) -> dict[str, Any]:
        """Verify an EdDSA (Ed25519) JWT against the bundled public key.

        Uses ``cryptography`` directly — always present with the ``[intelligence]``
        extra — so verification does NOT depend on python-jose being installed
        (the previous jose-only path silently trusted the raw response body when
        jose was absent, which is the default for ``pip install
        "cloudmorph-tessera[intelligence]"``). Raises on ANY verification failure:
        malformed token, a non-EdDSA ``alg`` (rejects ``alg=none`` and
        algorithm-confusion downgrades), or a bad signature. A raise means
        "untrusted — do not grant tier".
        """
        import base64

        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        def _b64url_decode(segment: str) -> bytes:
            padding = "=" * (-len(segment) % 4)
            return base64.urlsafe_b64decode(segment + padding)

        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Malformed license JWT: expected 3 dot-separated segments")
        header_b64, payload_b64, sig_b64 = parts

        header = json.loads(_b64url_decode(header_b64))
        alg = header.get("alg")
        if alg != "EdDSA":
            raise ValueError(f"Unsupported license JWT alg {alg!r} (expected EdDSA)")

        pub_key = load_pem_public_key(self._public_key_pem)
        if not isinstance(pub_key, Ed25519PublicKey):
            raise ValueError("Bundled license public key is not an Ed25519 key")

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = _b64url_decode(sig_b64)
        try:
            pub_key.verify(signature, signing_input)
        except InvalidSignature as exc:
            raise ValueError("License JWT signature verification failed") from exc

        return json.loads(_b64url_decode(payload_b64))

    async def check(self, force: bool = False) -> LicenseStatus:
        """Check license status.

        Returns cached result within TTL unless force=True.
        Falls back to disk cache on server unreachability (up to license_cache_fallback_days).
        """
        license_key = os.environ.get(self._config.license_key_env)
        if not license_key:
            return self._free_status()

        now = time.time()
        key_hash = hashlib.sha256(license_key.encode("utf-8")).hexdigest()

        # Return in-memory cache if fresh
        if (
            not force
            and self._in_memory_cache is not None
            and self._in_memory_key_hash == key_hash
            and 0 <= now - self._in_memory_ts < self._CACHE_TTL_SECONDS
            and (
                self._in_memory_cache.tier == "free"
                or self._in_memory_cache.expires_at is not None
                and now < self._in_memory_cache.expires_at.timestamp()
            )
        ):
            return self._in_memory_cache

        # Try license server
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self._config.license_check_url,
                    json={"license_key": license_key},
                    headers={"Content-Type": "application/json"},
                )

            if response.status_code in (401, 403):
                # The producer uses 403 for revoked/expired licenses. An explicit
                # denial must invalidate a paid cache rather than invoke outage grace.
                logger.warning("event=license_check_denied status=%s degrading to free tier", response.status_code)
                # Persist the degraded status
                free = self._free_status()
                self._in_memory_cache = free
                self._in_memory_ts = now
                self._in_memory_key_hash = key_hash
                self._fallback_denied_keys.add(key_hash)
                self._persist_cache(free, now)
                return free

            response.raise_for_status()
            try:
                status = self._verify_response(response.json())
            except Exception:  # noqa: BLE001
                # An untrusted/expired response is not a temporary outage.
                logger.warning("event=license_response_invalid degrading to free tier")
                status = self._free_status()
            if status.tier == "free":
                self._fallback_denied_keys.add(key_hash)
            else:
                self._fallback_denied_keys.discard(key_hash)
            self._in_memory_cache = status
            self._in_memory_ts = now
            self._in_memory_key_hash = key_hash
            self._persist_cache(status, now)
            return status

        except httpx.HTTPStatusError as exc:
            logger.warning("event=license_check_http_error status=%s", exc.response.status_code)
            # Fall through to cache fallback
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=license_check_unreachable error=%s", exc)
            # Fall through to cache fallback

        # Explicit rejection outranks disk fallback, even if persisting the
        # rejection failed (for example, a read-only or full cache filesystem).
        if key_hash in self._fallback_denied_keys:
            return self._free_status(from_cache=True)

        # Fallback to disk cache
        cached_status, cached_ts = self._load_cache()
        fallback_seconds = self._config.license_cache_fallback_days * 86400
        if cached_status is not None and 0 <= (now - cached_ts) < fallback_seconds:
            logger.info(
                "event=license_check_cache_fallback age_hours=%.1f",
                (now - cached_ts) / 3600,
            )
            return cached_status

        # Cache expired or absent — degrade to free
        logger.warning(
            "event=license_check_cache_expired degrading to free tier age_days=%.1f",
            (now - cached_ts) / 86400 if cached_ts else 0,
        )
        return self._free_status(from_cache=True)
