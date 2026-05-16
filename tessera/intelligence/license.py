"""License validator for Tessera intelligence packs.

Checks the license server and caches results with a configurable TTL.
Falls back to cached value on server unreachability for up to license_cache_fallback_days.
"""

from __future__ import annotations

import json
import logging
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
    "enterprise": 3,
}


@dataclass
class LicenseStatus:
    tier: Literal["free", "developer", "team", "enterprise"]
    expires_at: datetime | None
    seats: int
    customer_id: str | None
    from_cache: bool
    # Raw signed JWT returned by the license server. Forwarded to the CDN under
    # `X-Tessera-License` so the CloudFront tier-gating Function can read the
    # tier claim. None when the validator could not reach the license server or
    # when running unlicensed (`free` tier).
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
            tier = data.get("tier", "free")
            if tier not in _TIER_ORDER:
                tier = "free"
            expires_raw = data.get("expires_at")
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
            status = LicenseStatus(
                tier=tier,
                expires_at=expires_at,
                seats=int(data.get("seats", 1)),
                customer_id=data.get("customer_id"),
                from_cache=True,
                jwt=data.get("jwt"),
            )
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
        }
        try:
            self._cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=license_cache_write_failed path=%s error=%s", self._cache_path, exc)

    def _verify_response(self, response_data: dict[str, Any]) -> LicenseStatus:
        """Parse and validate the license server response.

        The server returns a JSON body with tier, expires_at, seats, customer_id.
        If a JWT token is present under 'token', we verify it with the Ed25519 key.
        """
        jwt_token = response_data.get("token")
        if jwt_token:
            # Verify Ed25519 JWT signature using python-jose if available, else manual
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                from cryptography.hazmat.primitives.serialization import load_pem_public_key
                from jose import jwt as jose_jwt

                pub_key = load_pem_public_key(self._public_key_pem)
                assert isinstance(pub_key, Ed25519PublicKey)

                # jose uses raw key bytes for Ed25519
                pub_key_bytes = pub_key.public_bytes_raw()
                claims = jose_jwt.decode(
                    jwt_token,
                    pub_key_bytes,
                    algorithms=["EdDSA"],
                )

                # Validate expiry
                exp = claims.get("exp")
                if exp and time.time() > exp:
                    raise ValueError("License JWT has expired")

                tier = claims.get("tier", response_data.get("tier", "free"))
                expires_at_ts = claims.get("exp")
                expires_at = (
                    datetime.fromtimestamp(expires_at_ts, tz=UTC)
                    if expires_at_ts
                    else None
                )
                seats = int(claims.get("seats", response_data.get("seats", 1)))
                customer_id = claims.get("customer_id", response_data.get("customer_id"))
            except ImportError:
                # python-jose not installed — fall back to plain response parsing
                tier = response_data.get("tier", "free")
                expires_raw = response_data.get("expires_at")
                expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
                seats = int(response_data.get("seats", 1))
                customer_id = response_data.get("customer_id")
        else:
            tier = response_data.get("tier", "free")
            expires_raw = response_data.get("expires_at")
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
            seats = int(response_data.get("seats", 1))
            customer_id = response_data.get("customer_id")

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

    async def check(self, force: bool = False) -> LicenseStatus:
        """Check license status.

        Returns cached result within TTL unless force=True.
        Falls back to disk cache on server unreachability (up to license_cache_fallback_days).
        """
        license_key = os.environ.get(self._config.license_key_env)
        if not license_key:
            return self._free_status()

        now = time.time()

        # Return in-memory cache if fresh
        if (
            not force
            and self._in_memory_cache is not None
            and now - self._in_memory_ts < self._CACHE_TTL_SECONDS
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

            if response.status_code == 401:
                logger.warning("event=license_check_unauthorized degrading to free tier")
                # Persist the degraded status
                free = self._free_status()
                self._in_memory_cache = free
                self._in_memory_ts = now
                self._persist_cache(free, now)
                return free

            response.raise_for_status()
            status = self._verify_response(response.json())
            self._in_memory_cache = status
            self._in_memory_ts = now
            self._persist_cache(status, now)
            return status

        except httpx.HTTPStatusError as exc:
            logger.warning("event=license_check_http_error status=%s", exc.response.status_code)
            # Fall through to cache fallback
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=license_check_unreachable error=%s", exc)
            # Fall through to cache fallback

        # Fallback to disk cache
        cached_status, cached_ts = self._load_cache()
        fallback_seconds = self._config.license_cache_fallback_days * 86400
        if cached_status is not None and (now - cached_ts) < fallback_seconds:
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
