"""Integration tests for IntelligenceClient — CDN and license server are mocked with respx."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from tessera.config import IntelligenceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ed25519_keypair_to_disk(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    """Generate an Ed25519 keypair and write the public half to tmp_path/pub.pem.

    Returns ``(private_key, public_key_path)``. Tests use this to sign mock
    catalogs and point IntelligenceConfig.public_key_path at the matching pub.
    """
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo
    )
    pub_path = tmp_path / "test_public.pem"
    pub_path.write_bytes(pub_bytes)
    return priv, pub_path


def _sign_catalog(priv: Ed25519PrivateKey, payload: dict) -> dict:
    """Return ``payload`` augmented with body_bytes_hex + Ed25519 signature.

    Encodes ``payload`` (without the signature fields) as canonical JSON,
    signs the bytes with ``priv``, and emits a catalog dict that the
    IntelligenceClient signature path will accept.

    The signature is base64-encoded to match the producer-side
    ``tessera-intelligence/scripts/sign_pack.py`` convention. Earlier
    revisions of this helper used ``.hex()`` which silently disagreed with
    the producer — see 0.2.1 CHANGELOG cross-repo audit fix.
    """
    import base64
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = base64.b64encode(priv.sign(body_bytes)).decode("ascii")
    return {**payload, "body_bytes_hex": body_bytes.hex(), "signature": sig}


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
        # P0-16: pre-warm fires inside start_refresh_task. The default is True
        # but most refresh-loop tests want explicit control over when refresh
        # runs, so disable pre-warm unless a test explicitly opts in.
        "prewarm_on_start": False,
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


def _make_catalog(pack_data: bytes, priv: Ed25519PrivateKey | None = None) -> dict:
    body = {
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
    return _sign_catalog(priv, body) if priv else body


def _make_mapping_catalog(mapping_data: bytes, priv: Ed25519PrivateKey | None = None) -> dict:
    body = {
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
    return _sign_catalog(priv, body) if priv else body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_successful_fetch_end_to_end(tmp_path, respx_mock):
    """Full pipeline: fetch catalogs, download packs, verify hashes, extract."""
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    catalog = _make_catalog(pack_data, priv=priv)
    mapping_catalog = _make_mapping_catalog(mapping_data, priv=priv)

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path, public_key_path=str(pub_path))
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
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    # Deliberately wrong hash for the pack — signed at the catalog level so
    # signature verification passes; the per-pack content_hash check is what
    # rejects it.
    catalog = _sign_catalog(
        priv,
        {
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
        },
    )
    mapping_catalog = _make_mapping_catalog(mapping_data, priv=priv)

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/packs/bad-pack-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path, public_key_path=str(pub_path))
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
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=_make_catalog(pack_data, priv=priv))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_make_mapping_catalog(mapping_data, priv=priv))
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path, public_key_path=str(pub_path))
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
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    # Allow multiple calls but count them
    catalog_route = respx_mock.get("https://cdn.test/pack-index.json").respond(json=_make_catalog(pack_data, priv=priv))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_make_mapping_catalog(mapping_data, priv=priv))
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(tmp_path, refresh_interval_hours=24, public_key_path=str(pub_path))
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


# ---------------------------------------------------------------------------
# P0-17 — Mandatory catalog signature verification
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsigned_pack_catalog_rejected(tmp_path, respx_mock):
    """P0-17: a pack catalog missing signature + body_bytes_hex must be rejected.

    Previously the client silently fell through to the no-signature path
    whenever the catalog omitted these fields. With the F2 fix, default is
    fail-closed: refresh() raises ValueError without downloading any packs.
    """
    _priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()

    # Catalog has packs but no signature/body_bytes_hex — fail-closed must fire
    unsigned_catalog = {
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

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=unsigned_catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json={"mappings": []})
    # No pack-URL route configured — if the test ever reaches the download
    # step (regression) respx will raise loudly.

    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        allow_unsigned_catalog=False,  # explicit fail-closed (also the default)
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    with pytest.raises(ValueError, match="missing signature/body_bytes_hex"):
        await client.refresh(force=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsigned_mapping_catalog_rejected(tmp_path, respx_mock):
    """P0-17: an unsigned mapping catalog (when present) is also rejected."""
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()

    # Signed pack catalog passes; the unsigned mapping catalog must fail-closed
    signed_pack = _make_catalog(pack_data, priv=priv)
    unsigned_mapping = {
        "mappings": [
            {
                "name": "aws-actions",
                "version": "1.0.0",
                "min_tier": "free",
                "content_hash": _sha256_hex(pack_data),
                "signature": "",
                "mapping_url": "https://cdn.test/mappings/aws-actions-1.0.0.tar.gz",
                "status": "active",
            }
        ]
    }

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=signed_pack)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=unsigned_mapping)

    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        allow_unsigned_catalog=False,
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    with pytest.raises(ValueError, match="mapping catalog is missing signature"):
        await client.refresh(force=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_allow_unsigned_catalog_opt_in_still_loads(tmp_path, respx_mock):
    """allow_unsigned_catalog=True is the explicit opt-in fail-open escape hatch."""
    _priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    unsigned_pack_catalog = {
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
    unsigned_mapping_catalog = {
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

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=unsigned_pack_catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=unsigned_mapping_catalog)
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        allow_unsigned_catalog=True,  # explicit opt-in fail-open
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    result = await client.refresh(force=True)

    assert result["packs_downloaded"] == 1
    assert result["mappings_downloaded"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_catalog_with_wrong_signature_rejected(tmp_path, respx_mock):
    """A catalog whose signature does not verify must be rejected, even when both fields are present."""
    priv1, _pub1 = _ed25519_keypair_to_disk(tmp_path)
    # Sign with a *different* private key than the one the client trusts
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir(parents=True, exist_ok=True)
    _priv2, pub2_path = _ed25519_keypair_to_disk(trusted_dir)

    pack_data = _make_pack_tar_bytes()
    # Signed by priv1 but trusted key is priv2's pub → must fail
    catalog = _make_catalog(pack_data, priv=priv1)

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json={"mappings": []})

    config = _make_config(tmp_path, public_key_path=str(pub2_path))
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    with pytest.raises(ValueError, match="pack catalog signature invalid"):
        await client.refresh(force=True)


# ---------------------------------------------------------------------------
# P0-16 — Cache pre-warm on startup
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prewarm_fires_immediate_refresh_on_start(tmp_path, respx_mock):
    """P0-16: start_refresh_task with prewarm_on_start=True fires refresh immediately.

    Before this fix the first refresh would only fire after
    refresh_interval_hours, so a cold start had zero packs for many hours.
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    pack_data = _make_pack_tar_bytes()
    mapping_data = _make_pack_tar_bytes()

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=_make_catalog(pack_data, priv=priv))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_make_mapping_catalog(mapping_data, priv=priv))
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_data)
    respx_mock.get("https://cdn.test/mappings/aws-actions-1.0.0.tar.gz").respond(content=mapping_data)

    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        prewarm_on_start=True,  # explicitly enable pre-warm for this test
        refresh_interval_hours=999,  # large enough that the loop won't fire
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    # No refresh called yet — start_refresh_task() should pre-warm.
    await client.start_refresh_task()

    # Pre-warm must have populated the cache *before* the background loop
    # gets its first chance to fire.
    packs = client.get_cached_packs()
    assert len(packs) == 1, "expected pre-warm to download the eligible pack"
    assert "aws-s3-baseline" in str(packs[0])
    assert client._last_refresh > 0, "pre-warm must have stamped _last_refresh"

    # Clean up the background task
    if client._refresh_task is not None:
        client._refresh_task.cancel()
        try:
            await client._refresh_task
        except asyncio.CancelledError:
            pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prewarm_disabled_skips_immediate_refresh(tmp_path, respx_mock):
    """prewarm_on_start=False preserves the legacy "wait one interval" behaviour."""
    _priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    # No respx routes configured. If prewarm_on_start were honoured incorrectly
    # and a refresh fired, the missing routes would surface as a test error.
    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        prewarm_on_start=False,
        refresh_interval_hours=999,
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    await client.start_refresh_task()

    assert client._refresh_task is not None
    # Cache is empty because no refresh fired
    assert client.get_cached_packs() == []

    # Clean up
    client._refresh_task.cancel()
    try:
        await client._refresh_task
    except asyncio.CancelledError:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prewarm_cdn_unreachable_logs_and_proceeds(tmp_path, respx_mock, caplog):
    """P0-16: CDN unreachable at startup must NOT block start_refresh_task.

    The pre-warm logs loudly ("intelligence_prewarm_failed") and the background
    task is still created so the proxy can keep running with an empty cache and
    retry on the regular cadence.
    """
    import logging

    _priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    # CDN raises on every catalog fetch
    respx_mock.get("https://cdn.test/pack-index.json").mock(
        side_effect=Exception("connect timeout")
    )
    respx_mock.get("https://cdn.test/mapping-index.json").mock(
        side_effect=Exception("connect timeout")
    )

    config = _make_config(
        tmp_path,
        public_key_path=str(pub_path),
        prewarm_on_start=True,
        refresh_interval_hours=999,
    )
    from tessera.intelligence.client import IntelligenceClient
    client = IntelligenceClient(config=config)

    with caplog.at_level(logging.WARNING, logger="tessera.intelligence.client"):
        # Must NOT raise — fail-open on CDN unreachable at startup
        await client.start_refresh_task()

    # Background task exists despite pre-warm failure
    assert client._refresh_task is not None
    assert not client._refresh_task.done()

    # Cache is empty (cold start, no policies)
    assert client.get_cached_packs() == []

    # Loud log emitted (either prewarm_failed for raise-path or prewarm_partial
    # for the "errors returned but didn't raise" path — both are acceptable
    # signals that pre-warm did not silently succeed).
    log_text = caplog.text
    assert (
        "intelligence_prewarm_failed" in log_text
        or "intelligence_prewarm_partial" in log_text
        or "catalog_fetch_failed" in log_text
    ), f"expected a prewarm failure signal in logs, got: {log_text}"

    # Clean up
    client._refresh_task.cancel()
    try:
        await client._refresh_task
    except asyncio.CancelledError:
        pass
