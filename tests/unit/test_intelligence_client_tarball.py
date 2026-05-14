"""Unit tests for tarball-hash verification in IntelligenceClient."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from tessera.errors import TamperDetected
from tessera.intelligence.client import IntelligenceClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_fake_tarball(content: bytes = b"fake-pack-content") -> bytes:
    """Return bytes of a minimal .tar.gz containing a single file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="pack.json")
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture()
def fake_tarball() -> bytes:
    return _make_fake_tarball()


@pytest.fixture()
def fake_tarball_sha256(fake_tarball: bytes) -> str:
    return hashlib.sha256(fake_tarball).hexdigest()


# ---------------------------------------------------------------------------
# Tests — exercising _verify_tarball_hash directly
# (avoids needing a live HTTP backend for the full refresh() path)
# ---------------------------------------------------------------------------


def test_refresh_accepts_valid_tarball_hash(fake_tarball, fake_tarball_sha256, tmp_path) -> None:
    """_verify_tarball_hash does not raise when hash matches."""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.cache_dir = str(tmp_path)
    config.public_key_path = "bundled"

    client = IntelligenceClient(config=config)
    # Should complete without raising
    client._verify_tarball_hash(fake_tarball, fake_tarball_sha256)


def test_refresh_rejects_tampered_tarball(fake_tarball_sha256, tmp_path) -> None:
    """_verify_tarball_hash raises TamperDetected when the tarball bytes were altered."""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.cache_dir = str(tmp_path)
    config.public_key_path = "bundled"

    client = IntelligenceClient(config=config)
    tampered = b"this-is-not-the-original-tarball"

    with pytest.raises(TamperDetected):
        client._verify_tarball_hash(tampered, fake_tarball_sha256)
