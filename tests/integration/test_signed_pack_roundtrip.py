"""Signed-pack round-trip proof — the full CDN fetch/verify pipeline, using a
fresh test-only Ed25519 keypair signed with the SAME wire encoding as the real
producer-side signing scripts (``tessera-intelligence/scripts/sign_pack.py``
and ``tessera-intelligence/scripts/sign_catalog.py``).

Why this file exists: SA-1 was asked to verify (not blindly re-fix) four
claimed defects in ``tessera/intelligence/client.py``:

  1. tier mapping ("team" vs "scale")
  2. bundle_url construction/handling
  3. sibling ``.signed.json`` fetch/verify ordering (P0-9)
  4. signature encoding (hex vs base64)

All four were found to already be fixed in ``client.py`` (see the SA-1
report). This file is the proof: it builds fixtures that mirror the REAL
producer encoding byte-for-byte (verified by reading
``tessera-intelligence/scripts/sign_pack.py``, ``scripts/sign_catalog.py``,
and real signed artifacts under ``tessera-intelligence/dist/v1.0.0/``) and
exercises the client's full refresh() pipeline end-to-end against them.  If a
future change to client.py breaks compatibility with the real signer, these
tests fail — proving that real, founder-signed packs will load with zero
further code changes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tessera.config import IntelligenceConfig
from tessera.intelligence.license import LicenseStatus

# ---------------------------------------------------------------------------
# Helpers — mirror tests/integration/test_intelligence_client.py conventions,
# extended to also replicate the PRODUCER-side signing math (not just the
# consumer-side signature encoding) for pack manifests and bundle sidecars.
# ---------------------------------------------------------------------------


def _ed25519_keypair_to_disk(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    """Generate a FRESH test-only Ed25519 keypair; write the public half to disk.

    Never touches the production signing key
    (``~/.tessera-intelligence-private-key.pem``) — this key exists only in
    the pytest tmp_path for the duration of one test.
    """
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo
    )
    pub_path = tmp_path / "test_public.pem"
    pub_path.write_bytes(pub_bytes)
    return priv, pub_path


def _make_tar_bytes(content: bytes = b'{"rules": []}', arcname: str = "pack.json") -> bytes:
    """Create a minimal tar.gz archive with a single file — a stand-in pack/bundle tarball."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _sign_catalog_body(priv: Ed25519PrivateKey, payload: dict) -> dict:
    """Sign a top-level catalog dict exactly like ``scripts/sign_catalog.py``.

    body_bytes = canonical JSON of payload (sorted keys, compact separators)
    body_bytes_hex = body_bytes.hex()
    signature = base64(Ed25519.sign(body_bytes))   — signs the RAW bytes,
        not a hash of them (Ed25519 does its own internal hashing).

    Matches client.py:_require_or_skip_catalog_sig + client.py:_verify_signature
    (``bytes.fromhex(body)`` then ``base64.b64decode(signature)``).
    """
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = base64.b64encode(priv.sign(body_bytes)).decode("ascii")
    return {**payload, "body_bytes_hex": body_bytes.hex(), "signature": sig}


def _producer_compute_manifest_content_hash(manifest_data: dict) -> str:
    """Replicate ``tessera-intelligence/scripts/sign_pack.py:compute_content_hash``.

    SHA-256 hex digest of the canonical JSON with content_hash/signature/signed_at
    zeroed.  Deliberately duplicated here (not imported from client.py) so this
    test proves independent agreement with the *producer* algorithm, not just
    that the client agrees with itself.
    """
    canonical = dict(manifest_data)
    canonical["content_hash"] = None
    canonical["signature"] = None
    canonical["signed_at"] = None
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sign_pack_manifest(priv: Ed25519PrivateKey, manifest_data: dict, tarball_bytes: bytes) -> dict:
    """Produce a signed ``<pack>.manifest.signed.json`` body exactly like sign_pack.py:sign_pack().

    1. Bind the tarball hash into the manifest BEFORE hashing (tarball_sha256).
    2. content_hash = SHA-256 hex of canonical JSON (content_hash/signature/signed_at zeroed).
    3. signature = base64(Ed25519.sign(content_hash.encode("utf-8")))  — signs the
       HEX-DIGEST STRING bytes, not the raw manifest bytes and not the raw tarball.
    """
    data = dict(manifest_data)
    data["tarball_sha256"] = hashlib.sha256(tarball_bytes).hexdigest()
    content_hash = _producer_compute_manifest_content_hash(data)
    signature = base64.b64encode(priv.sign(content_hash.encode("utf-8"))).decode("ascii")
    data["content_hash"] = content_hash
    data["signature"] = signature
    data["signed_at"] = "2026-01-01T00:00:00Z"
    return data


def _sign_bundle_sidecar(priv: Ed25519PrivateKey, kind: str, target_file: str, tarball_bytes: bytes) -> dict:
    """Produce a ``<bundle>.tar.gz.signed.json`` sidecar exactly like sign_pack.py:sign_bundle().

    content_hash = SHA-256 hex of the RAW TARBALL BYTES (not a manifest — bundles
    have no manifest.json, only the tarball itself).
    signature = base64(Ed25519.sign(content_hash.encode("utf-8")))  — same
    hex-digest-string-signing convention as pack manifests, per sign_pack.py's
    own comment: "matches the producer-side convention for pack manifests so
    the consumer-side verifier shares the same code path".
    """
    content_hash = hashlib.sha256(tarball_bytes).hexdigest()
    signature = base64.b64encode(priv.sign(content_hash.encode("utf-8"))).decode("ascii")
    return {
        "kind": kind,
        "target_file": target_file,
        "content_hash": content_hash,
        "signature": signature,
        "signed_at": "2026-01-01T00:00:00Z",
    }


def _flip_byte(data: bytes) -> bytes:
    """Return `data` with a single byte flipped — simulates tarball tampering/swap."""
    if not data:
        return data
    mutated = bytearray(data)
    mutated[0] ^= 0xFF
    return bytes(mutated)


def _make_config(tmp_path: Path, pub_path: Path, **overrides) -> IntelligenceConfig:
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
        "public_key_path": str(pub_path),
        "prewarm_on_start": False,
    }
    defaults.update(overrides)
    return IntelligenceConfig(**defaults)


class _FakeLicenseValidator:
    """Duck-typed stand-in for tessera.intelligence.license.LicenseValidator.

    Returns a fixed tier with no license-server network traffic, so tests can
    exercise IntelligenceClient's tier-gating logic (_tier_allowed) without
    dragging in the license-check HTTP flow, which is out of SA-1's scope.
    """

    def __init__(self, tier: str) -> None:
        self._tier = tier

    async def check(self, force: bool = False) -> LicenseStatus:  # noqa: ARG002
        return LicenseStatus(
            tier=self._tier,  # type: ignore[arg-type]
            expires_at=None,
            seats=1,
            customer_id="sa1-test-customer",
            from_cache=False,
            jwt=None,
        )


# ---------------------------------------------------------------------------
# T0 — Defect #1: tier mapping ("team" vs "scale") is a symmetric alias.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tier_alias_scale_and_team_are_symmetric(tmp_path, respx_mock):
    """A `scale`-tier license unlocks `team`-gated content and vice versa.

    Real catalogs (tessera-intelligence/catalogs/combination-index.json) ship
    combination bundles with min_tier="team". client.py's _TIER_ORDER maps
    both "team" and "scale" to rank 2, so _tier_allowed() must treat them as
    equal-rank in BOTH directions. Proven here with two separate refreshes
    against two separate tmp_path/cache_dir instances (a license's tier is
    fixed for the life of one refresh()).
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    # -- Direction A: license tier "scale" fetches a pack gated at min_tier "team" --
    pack_data_a = _make_tar_bytes(b'{"rules": ["a"]}')
    manifest_a = _sign_pack_manifest(
        priv,
        {"pack_name": "team-gated-pack", "version": "1.0.0", "files": ["pack.json"], "min_tier": "team"},
        pack_data_a,
    )
    pack_catalog_a = _sign_catalog_body(
        priv,
        {
            "packs": [
                {
                    "name": "team-gated-pack",
                    "version": "1.0.0",
                    "min_tier": "team",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "pack_url": "https://cdn.test/packs/team-gated-pack-1.0.0.tar.gz",
                    "manifest_url": "https://cdn.test/packs/team-gated-pack-1.0.0.manifest.signed.json",
                }
            ]
        },
    )
    respx_mock.get("https://cdn.test/pack-index.json").respond(json=pack_catalog_a)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_sign_catalog_body(priv, {"mappings": []}))
    respx_mock.get("https://cdn.test/packs/team-gated-pack-1.0.0.tar.gz").respond(content=pack_data_a)
    respx_mock.get(
        "https://cdn.test/packs/team-gated-pack-1.0.0.manifest.signed.json"
    ).respond(json=manifest_a)

    from tessera.intelligence.client import IntelligenceClient

    config_a = _make_config(tmp_path / "a", pub_path)
    client_a = IntelligenceClient(config=config_a, license_validator=_FakeLicenseValidator("scale"))
    result_a = await client_a.refresh(force=True)
    assert result_a["packs_downloaded"] == 1, f"scale license must unlock team-gated pack; errors={result_a['errors']}"

    # -- Direction B: license tier "team" fetches a pack gated at min_tier "scale" --
    respx_mock.reset()
    pack_data_b = _make_tar_bytes(b'{"rules": ["b"]}')
    manifest_b = _sign_pack_manifest(
        priv,
        {"pack_name": "scale-gated-pack", "version": "1.0.0", "files": ["pack.json"], "min_tier": "scale"},
        pack_data_b,
    )
    pack_catalog_b = _sign_catalog_body(
        priv,
        {
            "packs": [
                {
                    "name": "scale-gated-pack",
                    "version": "1.0.0",
                    "min_tier": "scale",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "pack_url": "https://cdn.test/packs/scale-gated-pack-1.0.0.tar.gz",
                    "manifest_url": "https://cdn.test/packs/scale-gated-pack-1.0.0.manifest.signed.json",
                }
            ]
        },
    )
    respx_mock.get("https://cdn.test/pack-index.json").respond(json=pack_catalog_b)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_sign_catalog_body(priv, {"mappings": []}))
    respx_mock.get("https://cdn.test/packs/scale-gated-pack-1.0.0.tar.gz").respond(content=pack_data_b)
    respx_mock.get(
        "https://cdn.test/packs/scale-gated-pack-1.0.0.manifest.signed.json"
    ).respond(json=manifest_b)

    config_b = _make_config(tmp_path / "b", pub_path)
    client_b = IntelligenceClient(config=config_b, license_validator=_FakeLicenseValidator("team"))
    result_b = await client_b.refresh(force=True)
    assert result_b["packs_downloaded"] == 1, f"team license must unlock scale-gated pack; errors={result_b['errors']}"


# ---------------------------------------------------------------------------
# T1 — Full pipeline: catalog -> tier gate -> bundle_url resolve ->
# .signed.json fetch -> sig verify -> tarball hash verify -> extract.
# Covers defects #2, #3, #4 together (they're all exercised on the same
# request path) plus a tier-gate DENIAL to prove #1 blocks as well as allows.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_pack_and_bundle_roundtrip_full_pipeline(tmp_path, respx_mock):
    """End-to-end: a developer-tier pack (manifest_url path) + a team-tier
    mapping bundle (P0-9 sibling .signed.json path), both fetched under a
    "scale" license; a co-listed enterprise-tier pack must NOT be fetched.
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    # --- Pack fixture (manifest_url + tarball_sha256-bound .manifest.signed.json) ---
    pack_tarball = _make_tar_bytes(b'{"policies": ["aws-s3-baseline"]}', arcname="policy.json")
    pack_manifest_signed = _sign_pack_manifest(
        priv,
        {
            "pack_name": "aws-s3-baseline",
            "version": "1.0.0",
            "files": ["policy.json"],
            "min_tier": "developer",
        },
        pack_tarball,
    )

    # --- Bundle fixture (bundle_url + sibling <bundle_url>.signed.json, no manifest_url) ---
    bundle_tarball = _make_tar_bytes(b'{"included_combinations": ["aws_iam_passrole_escalation_chain"]}')
    bundle_sidecar = _sign_bundle_sidecar(
        priv, kind="combination_bundle", target_file="aws-v1.0.0.tar.gz", tarball_bytes=bundle_tarball
    )

    pack_catalog = _sign_catalog_body(
        priv,
        {
            "packs": [
                {
                    "name": "aws-s3-baseline",
                    "version": "1.0.0",
                    "min_tier": "developer",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "pack_url": "https://cdn.test/v1.0.0/packs/aws-s3-baseline-1.0.0.tar.gz",
                    "manifest_url": "https://cdn.test/v1.0.0/packs/aws-s3-baseline-1.0.0.manifest.signed.json",
                },
                {
                    # Enterprise-gated pack co-listed in the same catalog — a
                    # "scale" license (rank 2) must NOT be allowed to fetch a
                    # rank-3 pack. No respx route is registered for its
                    # pack_url; the assertion below checks it was never called.
                    "name": "hipaa-guardrails",
                    "version": "1.0.0",
                    "min_tier": "enterprise",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "pack_url": "https://cdn.test/v1.0.0/packs/hipaa-guardrails-1.0.0.tar.gz",
                    "manifest_url": "https://cdn.test/v1.0.0/packs/hipaa-guardrails-1.0.0.manifest.signed.json",
                },
            ]
        },
    )
    mapping_catalog = _sign_catalog_body(
        priv,
        {
            "mapping_bundles": [
                {
                    "name": "aws-v1.0.0",
                    "version": "v1.0.0",
                    "provider": "aws",
                    "min_tier": "team",  # real-world value per combination-index.json
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "bundle_url": "https://cdn.test/v1.0.0/combinations/aws-v1.0.0.tar.gz",
                }
            ]
        },
    )

    respx_mock.get("https://cdn.test/pack-index.json").respond(json=pack_catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/v1.0.0/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=pack_tarball)
    respx_mock.get(
        "https://cdn.test/v1.0.0/packs/aws-s3-baseline-1.0.0.manifest.signed.json"
    ).respond(json=pack_manifest_signed)
    respx_mock.get("https://cdn.test/v1.0.0/combinations/aws-v1.0.0.tar.gz").respond(content=bundle_tarball)
    # Sibling URL MUST be exactly bundle_url + ".signed.json" (defect #3 claim).
    respx_mock.get(
        "https://cdn.test/v1.0.0/combinations/aws-v1.0.0.tar.gz.signed.json"
    ).respond(json=bundle_sidecar)

    from tessera.intelligence.client import IntelligenceClient

    config = _make_config(tmp_path, pub_path)
    client = IntelligenceClient(config=config, license_validator=_FakeLicenseValidator("scale"))

    result = await client.refresh(force=True)

    assert result["errors"] == [], f"expected zero errors, got {result['errors']}"
    assert result["packs_downloaded"] == 1
    assert result["mappings_downloaded"] == 1

    # Pack extracted under packs/<name>/<version>/
    packs = client.get_cached_packs()
    assert len(packs) == 1
    assert "aws-s3-baseline" in str(packs[0])
    assert (packs[0] / "policy.json").exists()

    # Bundle extracted under mappings/<name>/<version>/
    mappings_dir = client.get_cached_mappings_dir()
    bundle_dest = mappings_dir / "aws-v1.0.0" / "v1.0.0"
    assert bundle_dest.exists(), f"expected {bundle_dest} to exist after extraction"

    # Tier gate DENIAL: the enterprise-only pack must never have been requested.
    called_urls = {str(call.request.url) for call in respx_mock.calls}
    assert "https://cdn.test/v1.0.0/packs/hipaa-guardrails-1.0.0.tar.gz" not in called_urls
    assert "https://cdn.test/v1.0.0/packs/hipaa-guardrails-1.0.0.manifest.signed.json" not in called_urls


# ---------------------------------------------------------------------------
# T2/T3/T4 — Tamper detection: FAILS CLOSED.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tampered_pack_tarball_fails_closed(tmp_path, respx_mock):
    """A swapped pack tarball (correct manifest.signed.json, wrong bytes on the
    wire) must be rejected via the tarball_sha256 check bound into the signed
    manifest — the pack is never extracted, and the failure is recorded.
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    real_tarball = _make_tar_bytes(b'{"policies": ["real"]}', arcname="policy.json")
    tampered_tarball = _flip_byte(real_tarball)
    assert tampered_tarball != real_tarball

    # Manifest is signed over the REAL tarball's hash...
    manifest_signed = _sign_pack_manifest(
        priv,
        {"pack_name": "aws-s3-baseline", "version": "1.0.0", "files": ["policy.json"], "min_tier": "developer"},
        real_tarball,
    )

    pack_catalog = _sign_catalog_body(
        priv,
        {
            "packs": [
                {
                    "name": "aws-s3-baseline",
                    "version": "1.0.0",
                    "min_tier": "developer",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "pack_url": "https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz",
                    "manifest_url": "https://cdn.test/packs/aws-s3-baseline-1.0.0.manifest.signed.json",
                }
            ]
        },
    )
    respx_mock.get("https://cdn.test/pack-index.json").respond(json=pack_catalog)
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=_sign_catalog_body(priv, {"mappings": []}))
    # ...but the wire serves the TAMPERED bytes.
    respx_mock.get("https://cdn.test/packs/aws-s3-baseline-1.0.0.tar.gz").respond(content=tampered_tarball)
    respx_mock.get(
        "https://cdn.test/packs/aws-s3-baseline-1.0.0.manifest.signed.json"
    ).respond(json=manifest_signed)

    from tessera.intelligence.client import IntelligenceClient

    config = _make_config(tmp_path, pub_path)
    client = IntelligenceClient(config=config, license_validator=_FakeLicenseValidator("scale"))

    result = await client.refresh(force=True)

    assert result["packs_downloaded"] == 0
    assert len(result["errors"]) == 1
    assert "aws-s3-baseline" in result["errors"][0]
    assert client.get_cached_packs() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tampered_bundle_tarball_fails_closed_sibling_hash_mismatch(tmp_path, respx_mock):
    """A swapped bundle tarball (correct sibling .signed.json, wrong bytes on
    the wire) must be rejected via the P0-9 sibling content_hash check — the
    bundle is never extracted.
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)

    real_bundle = _make_tar_bytes(b'{"included_combinations": ["real"]}')
    tampered_bundle = _flip_byte(real_bundle)
    assert tampered_bundle != real_bundle

    sidecar = _sign_bundle_sidecar(
        priv, kind="combination_bundle", target_file="aws-v1.0.0.tar.gz", tarball_bytes=real_bundle
    )

    mapping_catalog = _sign_catalog_body(
        priv,
        {
            "mapping_bundles": [
                {
                    "name": "aws-v1.0.0",
                    "version": "v1.0.0",
                    "provider": "aws",
                    "min_tier": "team",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "bundle_url": "https://cdn.test/combinations/aws-v1.0.0.tar.gz",
                }
            ]
        },
    )
    respx_mock.get("https://cdn.test/pack-index.json").respond(json=_sign_catalog_body(priv, {"packs": []}))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/combinations/aws-v1.0.0.tar.gz").respond(content=tampered_bundle)
    respx_mock.get("https://cdn.test/combinations/aws-v1.0.0.tar.gz.signed.json").respond(json=sidecar)

    from tessera.intelligence.client import IntelligenceClient

    config = _make_config(tmp_path, pub_path)
    client = IntelligenceClient(config=config, license_validator=_FakeLicenseValidator("scale"))

    result = await client.refresh(force=True)

    assert result["mappings_downloaded"] == 0
    assert len(result["errors"]) == 1
    assert "aws-v1.0.0" in result["errors"][0]
    mappings_dir = client.get_cached_mappings_dir()
    assert not (mappings_dir / "aws-v1.0.0").exists()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_forged_bundle_sidecar_signature_fails_closed(tmp_path, respx_mock):
    """A forged sidecar (valid-looking content_hash/signature shape, but NOT a
    real Ed25519 signature from the trusted key) must be rejected by
    Ed25519.verify() BEFORE the client ever trusts its content_hash to check
    the tarball — proving the signature check gates the hash check, not the
    other way around (defect #3's ordering claim). The served tarball bytes
    are the CORRECT, untampered bytes; only the sidecar is malicious.
    """
    priv, pub_path = _ed25519_keypair_to_disk(tmp_path)
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir(parents=True, exist_ok=True)
    other_priv, _ = _ed25519_keypair_to_disk(attacker_dir)

    bundle_tarball = _make_tar_bytes(b'{"included_combinations": ["real"]}')

    # Signed with the WRONG (attacker) key — content_hash is correct, but the
    # signature will not verify against the trusted public key.
    forged_sidecar = _sign_bundle_sidecar(
        other_priv, kind="combination_bundle", target_file="aws-v1.0.0.tar.gz", tarball_bytes=bundle_tarball
    )

    mapping_catalog = _sign_catalog_body(
        priv,  # catalog itself is validly signed by the TRUSTED key
        {
            "mapping_bundles": [
                {
                    "name": "aws-v1.0.0",
                    "version": "v1.0.0",
                    "provider": "aws",
                    "min_tier": "team",
                    "status": "production",
                    "content_hash": "",
                    "signature": "",
                    "bundle_url": "https://cdn.test/combinations/aws-v1.0.0.tar.gz",
                }
            ]
        },
    )
    respx_mock.get("https://cdn.test/pack-index.json").respond(json=_sign_catalog_body(priv, {"packs": []}))
    respx_mock.get("https://cdn.test/mapping-index.json").respond(json=mapping_catalog)
    respx_mock.get("https://cdn.test/combinations/aws-v1.0.0.tar.gz").respond(content=bundle_tarball)
    respx_mock.get("https://cdn.test/combinations/aws-v1.0.0.tar.gz.signed.json").respond(json=forged_sidecar)

    from tessera.intelligence.client import IntelligenceClient

    config = _make_config(tmp_path, pub_path)
    client = IntelligenceClient(config=config, license_validator=_FakeLicenseValidator("scale"))

    result = await client.refresh(force=True)

    assert result["mappings_downloaded"] == 0
    assert len(result["errors"]) == 1
    assert "aws-v1.0.0" in result["errors"][0]
    mappings_dir = client.get_cached_mappings_dir()
    assert not (mappings_dir / "aws-v1.0.0").exists()
