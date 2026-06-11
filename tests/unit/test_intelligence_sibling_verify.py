"""Unit tests for P0-9 per-bundle sibling .signed.json verification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from unittest.mock import MagicMock

import httpx
import pytest

from tessera.intelligence.client import IntelligenceClient


# ---------------------------------------------------------------------------
# Test keypair (throwaway — never used in production)
# ---------------------------------------------------------------------------

_PRIV_PEM = b"""-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIC2x2mTuMX75x2CZ1cVHPHfdJrmWPw1vFHleG+bk43l8
-----END PRIVATE KEY-----
"""

_PUB_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAiup1jJz8AJessfu3peS1blxZPtQf1j9vQu6FwSjyk5E=
-----END PUBLIC KEY-----
"""


def _sign_content_hash(content_hash: str) -> str:
    """Sign content_hash bytes with the test private key, return base64."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(_PRIV_PEM, password=None)
    sig_bytes = key.sign(content_hash.encode("utf-8"))
    return base64.b64encode(sig_bytes).decode()


def _make_client(tmp_path) -> IntelligenceClient:
    """Build an IntelligenceClient that uses the test public key."""
    pub_key_file = tmp_path / "test_pub.pem"
    pub_key_file.write_bytes(_PUB_PEM)

    config = MagicMock()
    config.cache_dir = str(tmp_path)
    config.public_key_path = str(pub_key_file)
    return IntelligenceClient(config=config)


def _tarball_bytes() -> bytes:
    """Return deterministic fake tarball bytes."""
    return b"fake-mapping-bundle-content-for-tests"


def _make_signed_json(tarball: bytes, kind: str = "mapping_bundle") -> dict:
    content_hash = hashlib.sha256(tarball).hexdigest()
    signature = _sign_content_hash(content_hash)
    return {
        "kind": kind,
        "target_file": "aws-v1.0.0.tar.gz",
        "content_hash": content_hash,
        "signature": signature,
        "signed_at": "2026-06-11T00:00:00Z",
    }


def _make_mock_transport(responses: dict[str, tuple[int, bytes | dict]]) -> httpx.MockTransport:
    """Build an httpx mock transport. responses maps URL → (status_code, body)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, (status, body) in responses.items():
            if url.endswith(pattern):
                if isinstance(body, (bytes, bytearray)):
                    content = body
                    headers = {"Content-Type": "application/octet-stream"}
                else:
                    content = json.dumps(body).encode()
                    headers = {"Content-Type": "application/json"}
                return httpx.Response(status, headers=headers, content=content)
        return httpx.Response(404, content=b"not found")

    return httpx.MockTransport(_handler)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tests: _fetch_and_verify_sibling_signed_json
# ---------------------------------------------------------------------------


def test_sibling_verify_valid(tmp_path) -> None:
    """Valid .signed.json → returns the content_hash."""
    client = _make_client(tmp_path)
    tarball = _tarball_bytes()
    signed_json = _make_signed_json(tarball)
    content_hash = signed_json["content_hash"]

    transport = _make_mock_transport({
        "bundle.tar.gz.signed.json": (200, signed_json),
    })

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://cdn") as hc:
            return await client._fetch_and_verify_sibling_signed_json(
                "http://cdn/bundle.tar.gz", hc
            )

    result = _run(_go())
    assert result == content_hash


def test_sibling_verify_absent_returns_none(tmp_path) -> None:
    """404 on .signed.json → returns None (caller falls back)."""
    client = _make_client(tmp_path)
    transport = _make_mock_transport({})  # all 404

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://cdn") as hc:
            return await client._fetch_and_verify_sibling_signed_json(
                "http://cdn/bundle.tar.gz", hc
            )

    result = _run(_go())
    assert result is None


def test_sibling_verify_tampered_signature_raises(tmp_path) -> None:
    """Bad signature in .signed.json → raises ValueError."""
    client = _make_client(tmp_path)
    tarball = _tarball_bytes()
    signed_json = _make_signed_json(tarball)
    # Corrupt the signature
    signed_json["signature"] = base64.b64encode(b"X" * 64).decode()

    transport = _make_mock_transport({
        "bundle.tar.gz.signed.json": (200, signed_json),
    })

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://cdn") as hc:
            return await client._fetch_and_verify_sibling_signed_json(
                "http://cdn/bundle.tar.gz", hc
            )

    with pytest.raises(ValueError, match="Ed25519 verify failed"):
        _run(_go())


def test_sibling_verify_missing_fields_raises(tmp_path) -> None:
    """Incomplete .signed.json (no content_hash) → raises ValueError."""
    client = _make_client(tmp_path)

    transport = _make_mock_transport({
        "bundle.tar.gz.signed.json": (200, {"kind": "mapping_bundle"}),
    })

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://cdn") as hc:
            return await client._fetch_and_verify_sibling_signed_json(
                "http://cdn/bundle.tar.gz", hc
            )

    with pytest.raises(ValueError, match="missing content_hash or signature"):
        _run(_go())


# ---------------------------------------------------------------------------
# Tests: tarball verify using the sibling path
# ---------------------------------------------------------------------------


def test_sibling_verify_tarball_match(tmp_path) -> None:
    """Sibling content_hash matches actual tarball → _verify_tarball_hash passes."""
    client = _make_client(tmp_path)
    tarball = _tarball_bytes()
    signed_json = _make_signed_json(tarball)
    content_hash = signed_json["content_hash"]
    # Should not raise
    client._verify_tarball_hash(tarball, content_hash)


def test_sibling_verify_tarball_mismatch(tmp_path) -> None:
    """Sibling content_hash mismatch → TamperDetected."""
    from tessera.errors import TamperDetected

    client = _make_client(tmp_path)
    tarball = _tarball_bytes()
    signed_json = _make_signed_json(tarball)
    content_hash = signed_json["content_hash"]

    with pytest.raises(TamperDetected):
        client._verify_tarball_hash(b"swapped-content", content_hash)
