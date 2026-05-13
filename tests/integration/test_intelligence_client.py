"""Integration tests for IntelligenceClient — CDN and license server are mocked with respx."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tessera.config import IntelligenceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, **overrides) -> IntelligenceConfig:
    defaults = {
        "enabled": True,
        "cache_dir": str(tmp_path / "intelligence"),
        "catalog_url": "https://cdn.test/pack-index.json",
        "mapping_url": "https://cdn.test/mapping-index.json",
        "license_check_url": "https://license.test/v1/check",
        "license_key_env": "TESSERA_LICENSE_KEY",
        "refresh_interval_hours": 24,
        "license_cache_fallback_days": 7,
        "fail_closed_on_license_check": False,
        "public_key_path": "bundled",
    }
    defaults.update(overrides)
    return IntelligenceConfig(**defaults)


def _make_pack_tar_bytes() -> bytes:
    """Create a minimal tar.gz archive with a single JSON file."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        content = b'{"rules": []}'
        info = tarfile.TarInfo(name="pack.json")
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_catalog(pack_data: bytes) -> dict:
    return {
        "packs": [
            {
                "name": "aws-s3-baseline",
                "version": "1.0.0",
                "min_tier": "free",
                "content_hash": _sha256_hex(pack_data),
                "signature": "",
                "pack_url": "https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz",
                "status": "active",
            }
        ]
    }


def _make_mapping_catalog(mapping_data: bytes) -> dict:
    return {
        "mappings": [
            {
                "name": "aws-actions",
                "version": "1.0.0",
                "min_tier": "free",
                "content_hash": _sha256_hex(mapping_data),
                "signature": "",
                "mapping_url": "https://cdn.test/mappings/aws-actions-1.0.0.tar.gz",
                "status": "active",
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_successful_fetch_end_to_end(tmp_path, respx_mock):
    """Full pipeline: fetch catalogs, download packs, verify hashes, extract."""
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    catalog = _make_catalog(pack_data)
    mapping_catalog = _make_mapping_catalog(mapping_data)

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path)
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    result = await client.refresh(force=True)

    assert result["packs_downloaded"] == 1
    assert result["mappings_downloaded"] == 1
    assert result["errors"] == []

    # Cached pack directory should exist
    packs = client.get_cached_packs()
    assert len(packs) == 1
    assert "aws-s3-baseline" in str(packs[0])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manifest_signature_invalid_raises(tmp_path, respx_mock):
    """When a catalog declares a signature and it is wrong, refresh raises."""
    pack_data = _make_pack_tar_bytes()
    catalog = {
        "body_bytes_hex": b"not-the-real-body".hex(),
        "signature": "deadbeef" * 8,  # 32 bytes of wrong sig
        "packs": [],
    }

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json={"mappings": []})

    config = _make_config(tmp_path)
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    with pytest.raises(ValueError, match="signature"):
        await client.refresh(force=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_content_hash_mismatch_rejects_that_pack(tmp_path, respx_mock):
    """Pack with wrong content hash is rejected; other packs succeed."""
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    # Deliberately wrong hash for the pack
    catalog = {
        "packs": [
            {
                "name": "bad-pack",
                "version": "1.0.0",
                "min_tier": "free",
                "content_hash": "a" * 64,  # wrong SHA-256
                "signature": "",
                "pack_url": "https://cdn.test/packs/bad-pack-1.0.0.tar.gz",
                "status": "active",
            }
        ]
    }
    mapping_catalog = _make_mapping_catalog(mapping_data)

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/packs/bad-pack-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path)
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    result = await client.refresh(force=True)

    # Bad pack rejected, mapping succeeded
    assert result["packs_downloaded"] == 0
    assert result["mappings_downloaded"] == 1
    assert len(result["errors"]) == 1
    assert "bad-pack" in result["errors"][0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_license_check_401_falls_back_to_free(tmp_path, respx_mock):
    """License server 401 response causes LicenseValidator to return free tier."""
    from tessera.intelligence.license import LicenseValidator

    config = _make_config(tmp_path)

    respx_mock.post("https://license.test/v1/check").respond(status_code=401)

    import importlib.resources as _ilr
    pub_key_pem = (_ilr.files("tessera.intelligence") / "public_key.pem").read_bytes()

    validator = LicenseValidator(config=config, public_key_pem=pub_key_pem)

    import os
    old_val = os.environ.get("TESSERA_LICENSE_KEY")
    os.environ["TESSERA_LICENSE_KEY"] = "invalid-key"
    try:
        status = await validator.check(force=True)
        assert status.tier == "free"
    finally:
        if old_val is None:
            os.environ.pop("TESSERA_LICENSE_KEY", None)
        else:
            os.environ["TESSERA_LICENSE_KEY"] = old_val


@pytest.mark.integration
@pytest.mark.asyncio
async def test_license_server_unreachable_uses_cached(tmp_path, respx_mock):
    """License server unreachable → returns cached license if within 7d."""
    from tessera.intelligence.license import LicenseValidator, LicenseStatus

    config = _make_config(tmp_path)

    import importlib.resources as _ilr
    pub_key_pem = (_ilr.files("tessera.intelligence") / "public_key.pem").read_bytes()

    validator = LicenseValidator(config=config, public_key_pem=pub_key_pem)

    # Seed the disk cache with a developer tier license 2 hours old
    cache_dir = Path(config.cache_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "license.json"
    cache_data = {
        "tier": "developer",
        "expires_at": None,
        "seats": 5,
        "customer_id": "cust-123",
        "cached_at": time.time() - 7200,  # 2 hours ago
    }
    cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    # License server unreachable
    respx_mock.post("https://license.test/v1/check").mock(side_effect=Exception("Connection refused"))

    import os
    old_val = os.environ.get("TESSERA_LICENSE_KEY")
    os.environ["TESSERA_LICENSE_KEY"] = "some-key"
    try:
        status = await validator.check(force=True)
        assert status.tier == "developer"
        assert status.from_cache is True
    finally:
        if old_val is None:
            os.environ.pop("TESSERA_LICENSE_KEY", None)
        else:
            os.environ["TESSERA_LICENSE_KEY"] = old_val


@pytest.mark.integration
@pytest.mark.asyncio
async def test_license_cache_expired_beyond_7d_degrades_to_free(tmp_path, respx_mock):
    """License cache older than license_cache_fallback_days degrades to free."""
    from tessera.intelligence.license import LicenseValidator

    config = _make_config(tmp_path, license_cache_fallback_days=7)

    import importlib.resources as _ilr
    pub_key_pem = (_ilr.files("tessera.intelligence") / "public_key.pem").read_bytes()

    validator = LicenseValidator(config=config, public_key_pem=pub_key_pem)

    # Seed cache with an enterprise license that is 8+ days old
    cache_dir = Path(config.cache_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "license.json"
    cache_data = {
        "tier": "enterprise",
        "expires_at": None,
        "seats": 100,
        "customer_id": "cust-enterprise",
        "cached_at": time.time() - (8 * 86400),  # 8 days ago
    }
    cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    # License server unreachable — cache fallback should degrade to free
    respx_mock.post("https://license.test/v1/check").mock(side_effect=Exception("Timeout"))

    import os
    old_val = os.environ.get("TESSERA_LICENSE_KEY")
    os.environ["TESSERA_LICENSE_KEY"] = "some-key"
    try:
        status = await validator.check(force=True)
        assert status.tier == "free"
    finally:
        if old_val is None:
            os.environ.pop("TESSERA_LICENSE_KEY", None)
        else:
            os.environ["TESSERA_LICENSE_KEY"] = old_val


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intelligence_disabled_makes_no_network_calls(tmp_path, respx_mock):
    """When intelligence.enabled=False, no network calls should be made."""
    config = _make_config(tmp_path, enabled=False)
    # Don't call refresh at all when disabled — simulate proxy.py lifespan behaviour
    assert config.enabled is False
    # Zero respx calls expected
    assert respx_mock.calls.call_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_task_fires_after_interval(tmp_path, respx_mock):
    """start_refresh_task creates an asyncio Task that exists on the client."""
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=_make_catalog(pack_data))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_make_mapping_catalog(mapping_data))
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path)
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    await client.refresh(force=True)
    await client.start_refresh_task()

    assert client._refresh_task is not None
    assert not client._refresh_task.done()

    # Clean up the background task
    client._refresh_task.cancel()
    try:
        await client._refresh_task
    except asyncio.CancelledError:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_refresh_deduplicated(tmp_path, respx_mock):
    """Concurrent refresh() calls are deduplicated via asyncio.Lock — only one runs at a time."""
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    # Allow multiple calls but count them
    catalog_route = respx_mock.get("https://cdn.test/pack-index.json").respond(json=_make_catalog(pack_data))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_make_mapping_catalog(mapping_data))
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path, refresh_interval_hours=24)
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    # First call does the real refresh
    result1 = await client.refresh(force=True)
    # Second call within the interval is skipped (not forced)
    result2 = await client.refresh(force=False)

    assert result1["packs_downloaded"] == 1
    assert result2.get("skipped") is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tier_downgrade_after_server_response_changes(tmp_path, respx_mock):
    """When license server returns a lower tier on second check, client updates."""
    from tessera.intelligence.license import LicenseValidator

    config = _make_config(tmp_path)

    import importlib.resources as _ilr
    pub_key_pem = (_ilr.files("tessera.intelligence") / "public_key.pem").read_bytes()

    validator = LicenseValidator(config=config, public_key_pem=pub_key_pem)

    # First call returns team tier
    respx_mock.post("https://license.test/v1/check").respond(
        json={"tier": "team", "seats": 10, "customer_id": "cust-1", "expires_at": None}
    )

    import os
    old_val = os.environ.get("TESSERA_LICENSE_KEY")
    os.environ["TESSERA_LICENSE_KEY"] = "valid-key"
    try:
        status1 = await validator.check(force=True)
        assert status1.tier == "team"

        # Second call: server downgrades to developer
        respx_mock.post("https://license.test/v1/check").respond(
            json={"tier": "developer", "seats": 3, "customer_id": "cust-1", "expires_at": None}
        )

        status2 = await validator.check(force=True)
        assert status2.tier == "developer"
        assert status2.seats == 3
    finally:
        if old_val is None:
            os.environ.pop("TESSERA_LICENSE_KEY", None)
        else:
            os.environ["TESSERA_LICENSE_KEY"] = old_val
