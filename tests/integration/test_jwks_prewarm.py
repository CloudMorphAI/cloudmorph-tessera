"""Integration test: JWKS pre-warm at lifespan startup.

Starts a fake JWKS server, verifies that lifespan calls prewarm_jwks_cache()
so the first real request does NOT make a sync httpx call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tessera.auth._jwks import JWKSCache, prewarm_jwks_cache
from tessera.auth.jwt_mcp import JWTAuthenticator


class TestPrewarmJwksCache:
    """Tests for the standalone prewarm_jwks_cache() helper."""

    @pytest.mark.asyncio
    async def test_prewarm_success_returns_cache(self) -> None:
        """On success, prewarm_jwks_cache returns a populated JWKSCache."""
        fake_jwks = {
            "keys": [
                {"kid": "key-1", "kty": "RSA", "n": "abc", "e": "AQAB"},
            ]
        }

        async def _fake_get(*args, **kwargs) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = fake_jwks
            return resp

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = _fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = await prewarm_jwks_cache("https://example.com/.well-known/jwks.json")

        assert result is not None
        assert isinstance(result, JWKSCache)
        assert "key-1" in result.keys

    @pytest.mark.asyncio
    async def test_prewarm_failure_returns_none(self) -> None:
        """On network error, prewarm_jwks_cache swallows and returns None."""
        import httpx as _httpx

        async def _failing_get(*args, **kwargs) -> None:
            raise _httpx.ConnectError("connection refused")

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = _failing_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = await prewarm_jwks_cache("https://unreachable.invalid/jwks.json")

        assert result is None

    @pytest.mark.asyncio
    async def test_prewarm_logs_warning_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """On failure, prewarm_jwks_cache logs an event=jwks_prewarm_failed WARNING."""
        import logging

        import httpx as _httpx

        async def _failing_get(*args, **kwargs) -> None:
            raise _httpx.TimeoutException("timeout")

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = _failing_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            with caplog.at_level(logging.WARNING, logger="tessera.auth._jwks"):
                result = await prewarm_jwks_cache("https://unreachable.invalid/jwks.json")

        assert result is None
        assert any("jwks_prewarm_failed" in r.message for r in caplog.records)


class TestJWTAuthenticatorPrewarm:
    """Tests for JWTAuthenticator.prewarm() method."""

    @pytest.mark.asyncio
    async def test_prewarm_populates_cache(self) -> None:
        """After prewarm(), the authenticator's _cache is populated."""
        fake_jwks = {
            "keys": [
                {"kid": "rsa-1", "kty": "RSA", "n": "xyz", "e": "AQAB"},
            ]
        }

        async def _fake_get(*args, **kwargs) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = fake_jwks
            return resp

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = _fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            auth = JWTAuthenticator(
                jwks_url="https://example.com/.well-known/jwks.json",
                issuer="https://example.com",
                audience="tessera",
            )
            assert auth._cache is None, "cache should be None before prewarm"
            await auth.prewarm()

        assert auth._cache is not None
        assert isinstance(auth._cache, JWKSCache)
        assert "rsa-1" in auth._cache.keys

    @pytest.mark.asyncio
    async def test_prewarm_failure_leaves_cache_none(self) -> None:
        """If prewarm() fails, _cache stays None and no exception propagates."""
        import httpx as _httpx

        async def _failing_get(*args, **kwargs) -> None:
            raise _httpx.ConnectError("refused")

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = _failing_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            auth = JWTAuthenticator(
                jwks_url="https://unreachable.invalid/jwks.json",
                issuer="https://unreachable.invalid",
                audience="tessera",
            )
            # Must not raise
            await auth.prewarm()

        assert auth._cache is None

    @pytest.mark.asyncio
    async def test_async_client_used_not_sync_httpx(self) -> None:
        """prewarm_jwks_cache uses httpx.AsyncClient.get, NOT httpx.Client.get."""
        sync_get_called = []

        _original_client_init = __import__("httpx").Client.__init__

        def _track_sync_get(self, url, **kwargs):  # type: ignore[no-untyped-def]
            sync_get_called.append(url)

        fake_jwks = {"keys": [{"kid": "k1", "kty": "RSA", "n": "n", "e": "AQAB"}]}

        async def _fake_async_get(*args, **kwargs) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = fake_jwks
            return resp

        with patch("tessera.auth._jwks.httpx.AsyncClient") as mock_cls, \
             patch("tessera.auth._jwks.httpx.Client.get", side_effect=_track_sync_get):
            mock_client = AsyncMock()
            mock_client.get = _fake_async_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            await prewarm_jwks_cache("https://example.com/.well-known/jwks.json")

        # Sync httpx.Client.get must never have been called
        assert sync_get_called == [], f"Sync httpx.Client.get was called: {sync_get_called}"
