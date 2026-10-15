"""Intelligence client — downloads and caches Tessera intelligence packs.

Verifies Ed25519 signatures on catalog manifests and content hashes on pack archives.
Requires the ``cryptography`` package (included in the ``intelligence`` extra).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.resources
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

if TYPE_CHECKING:
    from tessera.config import IntelligenceConfig
    from tessera.cost.price_table import PriceTable
    from tessera.intelligence.license import LicenseStatus, LicenseValidator

logger = logging.getLogger(__name__)

# Maps tier names to their rank; packs with min_tier <= current tier rank are allowed.
# "scale" is the canonical name (matches pricing.cloudmorph.ai + the license server).
# "team" is kept as a backward-compat alias so customers on 0.2.0 → 0.2.1 don't break.
_TIER_ORDER: dict[str, int] = {
    "free": 0,
    "developer": 1,
    "team": 2,  # legacy alias for "scale" — same rank
    "scale": 2,
    "enterprise": 3,
}


@dataclass
class PackManifest:
    name: str
    version: str
    min_tier: str
    content_hash: str
    signature: str
    pack_url: str
    status: str  # "active" | "production" | "deprecated" | "pre-signed" | etc.
    manifest_url: str = ""  # URL of the per-pack signed manifest.json (packs only)
    tarball_sha256: str = ""  # Populated after fetching+verifying manifest_url


class IntelligenceClient:
    """Downloads and caches signed intelligence packs from the CloudMorph CDN.

    Signature verification uses the bundled Ed25519 public key.  Content-hash
    verification uses SHA-256 as declared in the catalog.
    """

    def __init__(
        self,
        config: IntelligenceConfig,
        license_validator: LicenseValidator | None = None,
    ) -> None:
        self._config = config
        self._license = license_validator
        self._cache_dir = Path(config.cache_dir).expanduser()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh: float = 0.0
        self._refresh_task: asyncio.Task[None] | None = None
        self._active_refreshes = 0
        # A cache is exposed to policy loading only after a signed catalog has
        # been reconciled during this process. This prevents stale on-disk packs
        # from bypassing current identity/tier checks after a failed refresh.
        self._cached_pack_catalog: dict[tuple[str, str], PackManifest] | None = None
        self._cached_pack_tier: str | None = None
        self._current_license_status: LicenseStatus | None = None
        self._refresh_callback: Callable[[dict[str, object] | None, Exception | None], None] | None = None
        # Populated by refresh() when a price-table artifact is present in the cache.
        self._price_tables: dict[str, PriceTable] = {}  # provider → PriceTable

    # ── Public key ────────────────────────────────────────────────────────────

    @property
    def _public_key(self) -> Ed25519PublicKey:
        if not hasattr(self, "_cached_public_key"):
            if self._config.public_key_path == "bundled":
                pkg = importlib.resources.files("tessera.intelligence")
                pem_bytes = (pkg / "public_key.pem").read_bytes()
            else:
                pem_bytes = Path(self._config.public_key_path).read_bytes()
            key = load_pem_public_key(pem_bytes)
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("Public key is not an Ed25519 key")
            self._cached_public_key: Ed25519PublicKey = key
        return self._cached_public_key

    # ── Verification helpers ──────────────────────────────────────────────────

    def _verify_signature(self, data: bytes, signature: str) -> None:
        """Verify Ed25519 signature.

        ``signature`` is **base64-encoded** per the producer-side ``sign_pack.py``
        (which uses ``base64.b64encode``).  Earlier versions of this method used
        ``bytes.fromhex`` which silently failed on real producer output — that bug
        was masked because the catalog signature path was opt-in until P0-17
        landed.  Raises ``cryptography.exceptions.InvalidSignature`` on failure.
        """
        sig_bytes = base64.b64decode(signature)
        self._public_key.verify(sig_bytes, data)

    def _require_or_skip_catalog_sig(
        self,
        kind: str,
        catalog_data: dict[str, Any],
    ) -> None:
        """Enforce mandatory catalog signature verification (P0-17).

        Behaviour:
          * Both ``signature`` and ``body_bytes_hex`` present and valid → return silently.
          * Signature present but invalid → raise ``ValueError``.
          * Both fields absent/empty → raise ``ValueError`` unless
            ``IntelligenceConfig.allow_unsigned_catalog`` is True, in which case
            a warning is logged and verification is skipped.

        The fail-closed default plugs the F2 gap where empty
        ``signature`` / ``body_bytes_hex`` fields silently disabled signature
        verification for the entire catalog.
        """
        sig = catalog_data.get("signature")
        body = catalog_data.get("body_bytes_hex")
        if (sig is not None and not isinstance(sig, str)) or (
            body is not None and not isinstance(body, str)
        ):
            raise ValueError(f"{kind} catalog has malformed signed envelope")
        if sig or body:
            try:
                if not isinstance(sig, str) or not isinstance(body, str) or not sig or not body:
                    raise ValueError("incomplete or malformed signed envelope")
                signed_body = bytes.fromhex(body)
                canonical = dict(catalog_data)
                canonical["signature"] = None
                canonical["body_bytes_hex"] = None
                expected_body = json.dumps(
                    canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                ).encode("utf-8")
                legacy = dict(catalog_data)
                legacy.pop("signature", None)
                legacy.pop("body_bytes_hex", None)
                legacy_body = json.dumps(
                    legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                ).encode("utf-8")
                if signed_body not in (expected_body, legacy_body):
                    raise ValueError("signed body does not match catalog fields")
                base64.b64decode(sig, validate=True)
                self._verify_signature(signed_body, sig)
            except Exception as exc:  # noqa: BLE001 — re-raised below with context
                raise ValueError(f"{kind} catalog signature invalid: {exc}") from exc
            return
        if self._config.allow_unsigned_catalog:
            logger.warning(
                "event=catalog_unsigned_accepted kind=%s allow_unsigned_catalog=true",
                kind,
            )
            return
        raise ValueError(
            f"{kind} catalog is missing signature/body_bytes_hex and "
            f"allow_unsigned_catalog is False — refusing to fail-open"
        )

    def _verify_content_hash(self, data: bytes, expected_hash: str) -> bool:
        """Return True if SHA-256 of data matches expected_hash (hex digest)."""
        actual = hashlib.sha256(data).hexdigest()
        return actual == expected_hash

    def _compute_manifest_content_hash(self, manifest_data: dict[str, Any]) -> str:
        """Recompute the canonical-JSON SHA-256 of a manifest with signed fields zeroed.

        Mirrors ``tessera-intelligence/scripts/sign_pack.py:compute_content_hash``
        so the consumer can detect a tampered manifest at refresh time.
        Returns the hex digest (matching the producer-side convention).
        """
        canonical = dict(manifest_data)
        canonical["content_hash"] = None
        canonical["signature"] = None
        canonical["signed_at"] = None
        serialized = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalized_version(version: str) -> str:
        """Normalize the producer's optional leading ``v`` version prefix."""
        return version.removeprefix("v").removeprefix("V")

    @staticmethod
    def _tiers_agree(catalog_tier: str, signed_tier: str) -> bool:
        if not isinstance(catalog_tier, str) or not isinstance(signed_tier, str):
            return False
        if catalog_tier not in _TIER_ORDER or signed_tier not in _TIER_ORDER:
            return False
        if catalog_tier == signed_tier:
            return True
        # The only legacy tier alias is team/scale; both are explicitly rank 2.
        return ({catalog_tier, signed_tier} == {"team", "scale"}
                and _TIER_ORDER.get(catalog_tier) == _TIER_ORDER.get(signed_tier))

    def _verify_signed_manifest_data(self, manifest_data: dict[str, Any], source: str) -> None:
        """Validate the unchanged signed-manifest wire format and signature."""
        from tessera.errors import TamperDetected

        stored_hash = manifest_data.get("content_hash", "") or ""
        stored_sig = manifest_data.get("signature", "") or ""
        if not stored_hash or not stored_sig:
            raise ValueError(f"manifest at {source} is missing content_hash or signature")
        if stored_hash.startswith("PLACEHOLDER") or stored_sig.startswith("PLACEHOLDER"):
            raise ValueError(f"manifest at {source} carries placeholder values (pack not signed)")
        recomputed = self._compute_manifest_content_hash(manifest_data)
        if recomputed != stored_hash:
            raise TamperDetected(
                f"manifest content_hash mismatch at {source}: "
                f"stored={stored_hash[:16]}... recomputed={recomputed[:16]}...",
            )
        sig_bytes = base64.b64decode(stored_sig)
        try:
            self._public_key.verify(sig_bytes, stored_hash.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"manifest signature Ed25519 verify failed at {source}: {exc}") from exc

    @classmethod
    def _verify_manifest_catalog_agreement(
        cls, signed_manifest: dict[str, Any], catalog_entry: PackManifest,
    ) -> None:
        """Fail closed when the signed pack identity or tier differs from catalog."""
        if signed_manifest.get("pack_name") != catalog_entry.name:
            raise ValueError("catalog/manifest pack name mismatch")
        signed_version = signed_manifest.get("version")
        if (not isinstance(signed_version, str) or not isinstance(catalog_entry.version, str)
                or cls._normalized_version(signed_version) != cls._normalized_version(catalog_entry.version)):
            raise ValueError("catalog/manifest version mismatch")
        signed_hash = signed_manifest.get("content_hash")
        signed_signature = signed_manifest.get("signature")
        if catalog_entry.content_hash or catalog_entry.signature:
            if not catalog_entry.content_hash or not catalog_entry.signature:
                raise ValueError("catalog manifest signing metadata is incomplete")
            if signed_hash != catalog_entry.content_hash:
                raise ValueError("catalog/manifest content-hash mismatch")
            if signed_signature != catalog_entry.signature:
                raise ValueError("catalog/manifest signature mismatch")
        signed_tier = signed_manifest.get("min_tier")
        if (not isinstance(signed_tier, str)
                or not cls._tiers_agree(catalog_entry.min_tier, signed_tier)):
            raise ValueError("catalog/manifest minimum-tier mismatch")

    async def _fetch_and_verify_signed_manifest(
        self,
        manifest_url: str,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Fetch a pack's signed ``manifest.json`` and verify its Ed25519 signature.

        The signature scheme matches the producer-side ``sign_pack.py``:

        1. Producer computes SHA-256 of canonical-JSON of the manifest with
           ``content_hash`` / ``signature`` / ``signed_at`` zeroed.
        2. Producer signs the hex-digest UTF-8 bytes with Ed25519, base64-encodes.
        3. Producer writes ``content_hash``, ``signature``, ``signed_at`` back into
           the manifest along with ``tarball_sha256`` (bound by ``--tarball-hash``).

        Consumer reverses:

        1. Recompute canonical-JSON hash with signed fields zeroed.
        2. Assert recomputed == ``content_hash`` (catches manifest-level tamper).
        3. ``base64.b64decode`` the signature, ``Ed25519.verify`` against the
           ``content_hash`` UTF-8 bytes.

        Returns the verified manifest dict. Caller is expected to read
        ``tarball_sha256`` to verify the tarball it just downloaded.

        Raises:
            ValueError: signature missing, placeholder, or verify failed.
            TamperDetected: recomputed hash != stored content_hash.
        """
        resp = await client.get(manifest_url)
        resp.raise_for_status()
        manifest_data: dict[str, Any] = resp.json()
        self._verify_signed_manifest_data(manifest_data, manifest_url)
        return manifest_data

    def _verify_tarball_hash(self, tarball_bytes: bytes, expected_sha256: str) -> None:
        """Raise TamperDetected when SHA-256 of tarball_bytes != expected_sha256."""
        from tessera.errors import TamperDetected
        actual = hashlib.sha256(tarball_bytes).hexdigest()
        if actual != expected_sha256:
            raise TamperDetected(
                f"Tarball hash mismatch: expected {expected_sha256!r}, got {actual!r}"
            )

    @staticmethod
    def _valid_sha256(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))

    @staticmethod
    def _safe_cache_component(value: str) -> bool:
        return (isinstance(value, str)
                and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", value))
                and value not in {".", ".."})

    @staticmethod
    def _safe_archive_member(name: str) -> bool:
        if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            return False
        path = PurePosixPath(name)
        return ".." not in path.parts

    # ── Catalog parsing ───────────────────────────────────────────────────────

    def _parse_catalog(self, catalog_data: dict[str, Any], kind: str = "pack") -> list[PackManifest]:
        """Parse a catalog JSON body into PackManifest list.

        Pack catalog entries carry ``pack_url`` + ``manifest_url``.  Mapping
        catalog entries carry ``bundle_url`` (NOT ``mapping_url`` — the producer
        emits ``bundle_url`` per ``catalogs/mapping-index.json``).  The earlier
        ``mapping_url`` fallback was a silent no-op for every mapping bundle.
        """
        manifests: list[PackManifest] = []
        # Producer-side catalogs use top-level "packs" or "mapping_bundles".
        if kind == "pack":
            items = catalog_data.get("packs", [])
        else:
            items = catalog_data.get("mapping_bundles") or catalog_data.get("mappings", [])
        if not isinstance(items, list):
            raise ValueError(f"Invalid {kind} catalog: expected a list of entries")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid {kind} catalog: entry must be an object")
            min_tier = item.get("min_tier")
            if not isinstance(min_tier, str) or min_tier.strip().lower() not in _TIER_ORDER:
                raise ValueError(
                    f"Invalid {kind} catalog entry {item.get('name', '<unknown>')!r}: "
                    f"missing or unknown min_tier {min_tier!r}"
                )
            # URL fallback chain: pack_url (packs), then bundle_url (current
            # mapping-index.json producer), then mapping_url (legacy fixture
            # field — kept so the existing test corpus that mocks "mapping_url"
            # still resolves to a usable URL until those tests are migrated to
            # the producer-correct "bundle_url").
            url = (
                item.get("pack_url")
                or item.get("bundle_url")
                or item.get("mapping_url")
                or ""
            )
            manifests.append(PackManifest(
                name=item["name"],
                version=item.get("version", "0.0.1"),
                min_tier=min_tier.strip().lower(),
                content_hash=item.get("content_hash", ""),
                signature=item.get("signature", ""),
                pack_url=url,
                status=item.get("status", "active"),
                manifest_url=item.get("manifest_url", ""),
            ))
        return manifests

    # ── Tier check ───────────────────────────────────────────────────────────

    def _tier_allowed(self, pack_tier: str, current_tier: str) -> bool:
        pack_rank = _TIER_ORDER.get(pack_tier.strip().lower())
        current_rank = _TIER_ORDER.get(current_tier.strip().lower())
        if pack_rank is None or current_rank is None:
            return False
        return current_rank >= pack_rank

    # ── Sibling signed-manifest verification (P0-9) ──────────────────────────

    async def _fetch_and_verify_sibling_signed_json(
        self,
        bundle_url: str,
        client: httpx.AsyncClient,
    ) -> str | None:
        """Fetch and Ed25519-verify the sibling ``.signed.json`` for a bundle.

        The sibling URL is ``<bundle_url>.signed.json``.  Format::

            {
              "kind": "mapping_bundle"|"blast_radius_bundle"|"combination_bundle",
              "target_file": "<filename>.tar.gz",
              "content_hash": "<sha256-hex-of-tarball>",
              "signature": "<base64-ed25519-sig-over-content_hash-utf8>",
              "signed_at": "<ISO-timestamp>"
            }

        Returns the verified ``content_hash`` hex string on success.
        Returns ``None`` when the sibling is absent (404) — caller falls back
        to catalog ``content_hash``.  Raises on any verification failure so
        the caller can fail-closed.
        """
        sibling_url = bundle_url + ".signed.json"
        try:
            resp = await client.get(sibling_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "event=sibling_signed_json_fetch_error url=%s error=%s",
                sibling_url, exc,
            )
            return None

        if resp.status_code == 404:
            # Sibling absent — caller will warn and fall back.
            return None

        resp.raise_for_status()
        meta: dict[str, Any] = resp.json()

        content_hash = meta.get("content_hash", "") or ""
        signature = meta.get("signature", "") or ""

        if not content_hash or not signature:
            raise ValueError(
                f"sibling .signed.json at {sibling_url} missing content_hash or signature"
            )
        if not self._valid_sha256(content_hash):
            raise ValueError(f"sibling .signed.json at {sibling_url} has invalid content_hash")

        # Ed25519-verify: sign_pack.py signs content_hash.encode("utf-8")
        try:
            sig_bytes = base64.b64decode(signature)
            self._public_key.verify(sig_bytes, content_hash.encode("utf-8"))
        except Exception as exc:
            raise ValueError(
                f"sibling .signed.json Ed25519 verify failed at {sibling_url}: {exc}"
            ) from exc

        logger.debug(
            "event=sibling_signed_json_verified url=%s content_hash=%s",
            sibling_url, content_hash[:16],
        )
        return content_hash

    # ── Pack download + extract ───────────────────────────────────────────────

    async def _download_and_extract(
        self,
        manifest: PackManifest,
        dest_parent: Path,
        client: httpx.AsyncClient,
    ) -> bool:
        """Download, verify, and extract a pack or mapping archive. Returns True on success.

        Verification flow (P0-9 updated):

        * If the catalog entry carries ``manifest_url`` (packs), fetch + Ed25519-verify
          the signed manifest first.  The verified manifest's ``tarball_sha256`` is
          authoritative for the downloaded tarball.
        * If ``manifest_url`` is absent (mapping/blast-radius/combination bundles),
          try to fetch the sibling ``<pack_url>.signed.json`` (P0-9).  If found,
          Ed25519-verify the ``content_hash`` field and use it to verify the tarball.
          If absent (404), require a usable catalog-declared SHA-256 digest.
        * Extract into staging and replace the prior cache only after all archive
          members pass validation. Raw fallback accepts valid JSON only.
        """
        if (not self._safe_cache_component(manifest.name)
                or not self._safe_cache_component(manifest.version)):
            logger.error("event=pack_cache_identity_unsafe name=%r version=%r", manifest.name, manifest.version)
            return False

        # ── Step A: fetch + verify the signed manifest (packs only) ──
        verified_tarball_sha256: str = ""
        verified_manifest: dict[str, Any] | None = None
        if manifest.manifest_url:
            try:
                verified_manifest = await self._fetch_and_verify_signed_manifest(
                    manifest.manifest_url, client,
                )
                self._verify_manifest_catalog_agreement(verified_manifest, manifest)
                verified_tarball_sha256 = verified_manifest.get("tarball_sha256", "") or ""
                if not self._valid_sha256(verified_tarball_sha256):
                    raise ValueError("signed manifest has no usable tarball_sha256")
                # Promote the verified hash onto the live PackManifest so callers
                # / tests inspecting `manifest.tarball_sha256` see the authoritative value.
                manifest.tarball_sha256 = verified_tarball_sha256
            except Exception as exc:  # noqa: BLE001 — verify failure = fail-closed
                logger.error(
                    "event=manifest_verify_failed name=%s url=%s error=%s",
                    manifest.name, manifest.manifest_url, exc,
                )
                return False

        # ── Step A2: P0-9 sibling .signed.json (mapping/blast-radius/combination) ──
        sibling_verified_hash: str = ""
        if not manifest.manifest_url and manifest.pack_url:
            try:
                result = await self._fetch_and_verify_sibling_signed_json(
                    manifest.pack_url, client,
                )
                if result is not None:
                    sibling_verified_hash = result
                else:
                    # Sibling absent — warn, fall back to catalog content_hash.
                    logger.warning(
                        "event=sibling_signed_json_absent name=%s url=%s "
                        "falling_back_to=catalog_content_hash",
                        manifest.name, manifest.pack_url + ".signed.json",
                    )
            except Exception as exc:  # noqa: BLE001 — verify failure = fail-closed
                logger.error(
                    "event=sibling_signed_json_verify_failed name=%s error=%s",
                    manifest.name, exc,
                )
                return False

        # ── Step B: fetch tarball ──
        try:
            response = await client.get(manifest.pack_url)
            response.raise_for_status()
            raw = response.content
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=pack_download_failed name=%s url=%s error=%s",
                manifest.name, manifest.pack_url, exc,
            )
            return False

        # ── Step C: verify tarball SHA-256 ──
        if verified_tarball_sha256:
            # Pack path: use manifest-bound tarball hash.
            try:
                self._verify_tarball_hash(raw, verified_tarball_sha256)
            except Exception as exc:  # noqa: BLE001 — TamperDetected
                logger.error(
                    "event=tarball_hash_mismatch name=%s error=%s",
                    manifest.name, exc,
                )
                return False
        elif sibling_verified_hash:
            # P0-9 bundle path: sibling .signed.json content_hash IS the tarball hash.
            try:
                self._verify_tarball_hash(raw, sibling_verified_hash)
            except Exception as exc:  # noqa: BLE001 — TamperDetected
                logger.error(
                    "event=tarball_hash_mismatch name=%s error=%s",
                    manifest.name, exc,
                )
                return False
        elif manifest.content_hash and not manifest.content_hash.startswith("PLACEHOLDER"):
            # Legacy fallback: sibling absent + no manifest_url.  Use catalog
            # content_hash only when it is a usable SHA-256 binding.
            if not self._valid_sha256(manifest.content_hash) or not self._verify_content_hash(raw, manifest.content_hash):
                logger.error(
                    "event=pack_hash_mismatch name=%s expected=%s",
                    manifest.name, manifest.content_hash[:16],
                )
                return False
        else:
            logger.error("event=pack_integrity_binding_missing name=%s", manifest.name)
            return False

        # Extract into a sibling staging directory. The current cache is left
        # untouched until the complete archive and signed marker are ready.
        pack_parent = dest_parent / manifest.name
        dest = pack_parent / manifest.version
        staging: Path | None = None
        backup: Path | None = None
        try:
            import io
            pack_parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=pack_parent))
            try:
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                    seen_members: set[str] = set()
                    for member in tf.getmembers():
                        if not self._safe_archive_member(member.name):
                            raise ValueError(f"unsafe archive member: {member.name!r}")
                        if member.name in {
                            ".tessera-manifest.signed.json",
                            ".tessera-verified.tar.gz",
                        }:
                            raise ValueError(f"archive member uses reserved cache name: {member.name!r}")
                        if member.name in seen_members:
                            raise ValueError(f"duplicate archive member: {member.name!r}")
                        seen_members.add(member.name)
                        if member.isdir():
                            continue
                        if not member.isfile():
                            raise ValueError(f"unsupported archive member type: {member.name!r}")
                        tf.extract(member, path=staging, filter="data")
            except tarfile.ReadError:
                # Historical single-file JSON packs are the only supported raw
                # fallback. Corrupt tar/security errors never become raw data.
                try:
                    json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("download is neither a valid tar archive nor legacy JSON pack") from exc
                (staging / f"{manifest.name}.json").write_bytes(raw)

            if manifest.manifest_url and verified_manifest is not None:
                (staging / ".tessera-manifest.signed.json").write_text(
                    json.dumps(verified_manifest), encoding="utf-8",
                )
                (staging / ".tessera-verified.tar.gz").write_bytes(raw)

            if dest.exists():
                backup = pack_parent / f".backup-{uuid.uuid4().hex}"
                os.replace(dest, backup)
            try:
                os.replace(staging, dest)
                staging = None
            except Exception:
                if backup is not None and backup.exists() and not dest.exists():
                    os.replace(backup, dest)
                    backup = None
                raise
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
                backup = None
        except Exception as exc:  # noqa: BLE001 — extraction/cache write fails closed
            logger.error("event=pack_extract_failed name=%s error=%s", manifest.name, exc)
            return False
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if backup is not None and backup.exists() and not dest.exists():
                os.replace(backup, dest)

        logger.info("event=pack_extracted name=%s version=%s dest=%s", manifest.name, manifest.version, dest)
        return True

    # ── Persistence ──────────────────────────────────────────────────────────

    def _persist_last_known_good(self, tier: str) -> None:
        lkg_path = self._cache_dir / "last_known_good.json"
        lkg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"timestamp": time.time(), "tier": tier}
        lkg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Main refresh pipeline ─────────────────────────────────────────────────

    async def refresh(self, force: bool = False) -> dict[str, object]:
        """Refresh intelligence state and notify the proxy after each attempt."""
        self._active_refreshes += 1
        try:
            result = await self._refresh_impl(force=force)
        except BaseException as exc:
            self._active_refreshes = max(0, self._active_refreshes - 1)
            self._notify_refresh_callback(
                None,
                exc if isinstance(exc, Exception) else RuntimeError("refresh cancelled"),
            )
            raise
        self._active_refreshes = max(0, self._active_refreshes - 1)
        self._notify_refresh_callback(result, None)
        return result

    def set_refresh_callback(
        self,
        callback: Callable[[dict[str, object] | None, Exception | None], None] | None,
    ) -> None:
        """Set a synchronous callback invoked after each completed refresh attempt."""
        self._refresh_callback = callback

    @property
    def current_license_status(self) -> LicenseStatus | None:
        return self._current_license_status

    @property
    def refresh_in_progress(self) -> bool:
        return self._active_refreshes > 0

    def _notify_refresh_callback(
        self,
        result: dict[str, object] | None,
        error: Exception | None,
    ) -> None:
        if self._refresh_callback is None:
            return
        try:
            self._refresh_callback(result, error)
        except Exception as exc:  # noqa: BLE001 — callback cannot break refresh
            logger.error("event=intelligence_refresh_callback_failed error=%s", exc)

    async def _refresh_impl(self, force: bool = False) -> dict[str, object]:
        """Download and verify all eligible packs and mappings.

        Returns a summary dict: {"packs_downloaded": N, "mappings_downloaded": M, "errors": [...]}.
        """
        async with self._refresh_lock:
            now = time.time()
            interval_seconds = self._config.refresh_interval_hours * 3600
            if not force and (now - self._last_refresh) < interval_seconds:
                return {"packs_downloaded": 0, "mappings_downloaded": 0, "errors": [], "skipped": True}

            self._notify_refresh_callback({"refresh_started": True}, None)
            # A failed catalog fetch must not leave a stale cache authorization
            # from an earlier refresh available to callers.
            self._cached_pack_catalog = None
            self._cached_pack_tier = None

            errors: list[str] = []
            packs_downloaded = 0
            mappings_downloaded = 0

            # Step 0: Resolve license JWT for CDN tier-gating.
            # The CDN's CloudFront Function reads `X-Tessera-License` and
            # rejects with 401 when absent. Per status/intelligence-and-licensing.md
            # the JWT must accompany every fetch.
            current_tier = "free"
            license_jwt: str | None = None
            if self._license is not None:
                try:
                    if self._config.selected_packs:
                        license_status = await self._license.check(force=True)
                    else:
                        license_status = await self._license.check()
                    self._current_license_status = license_status
                    current_tier = license_status.tier
                    license_jwt = license_status.jwt
                except Exception as exc:  # noqa: BLE001
                    self._current_license_status = None
                    if self._config.fail_closed_on_license_check:
                        raise ValueError(f"License check failed (fail_closed=True): {exc}") from exc
                    logger.warning("event=license_check_failed_fallback error=%s", exc)

            cdn_headers: dict[str, str] = (
                {"X-Tessera-License": license_jwt} if license_jwt else {}
            )

            async with httpx.AsyncClient(timeout=30, headers=cdn_headers) as client:
                # Step 1: Fetch catalogs
                try:
                    catalog_resp = await client.get(self._config.catalog_url)
                    catalog_resp.raise_for_status()
                    catalog_data = catalog_resp.json()
                except Exception as exc:  # noqa: BLE001
                    msg = f"Failed to fetch pack catalog: {exc}"
                    logger.error("event=catalog_fetch_failed url=%s error=%s", self._config.catalog_url, exc)
                    errors.append(msg)
                    return {"packs_downloaded": 0, "mappings_downloaded": 0, "errors": errors}

                try:
                    mapping_resp = await client.get(self._config.mapping_url)
                    mapping_resp.raise_for_status()
                    mapping_data = mapping_resp.json()
                except Exception as exc:  # noqa: BLE001
                    msg = f"Failed to fetch mapping catalog: {exc}"
                    logger.warning("event=mapping_catalog_fetch_failed url=%s error=%s", self._config.mapping_url, exc)
                    errors.append(msg)
                    mapping_data = {}

                # Step 2: Verify catalog signatures (P0-17: mandatory by default).
                # Missing signature or body_bytes_hex raises unless
                # IntelligenceConfig.allow_unsigned_catalog is True. The mapping
                # catalog is only checked when it was actually fetched —
                # mapping fetch failure earlier sets mapping_data = {} and we
                # tolerate that path without forcing a signature on an empty doc.
                self._require_or_skip_catalog_sig("pack", catalog_data)
                if mapping_data:
                    self._require_or_skip_catalog_sig("mapping", mapping_data)

                # Step 3: Download eligible packs (license JWT is on the client headers)
                packs_dir = self._cache_dir / "packs"
                packs_dir.mkdir(parents=True, exist_ok=True)
                pack_manifests = self._parse_catalog(catalog_data, kind="pack")
                self._cached_pack_catalog = {
                    (item.name, self._normalized_version(item.version)): item
                    for item in pack_manifests
                }
                self._cached_pack_tier = current_tier

                for manifest in pack_manifests:
                    # CRITICAL-1 (code-audit-2026-05-22): accept "production" — prod catalog
                    # uses that value, not "active". Without this, zero packs ever downloaded.
                    if manifest.status not in ("active", "production"):
                        continue
                    if not self._tier_allowed(manifest.min_tier, current_tier):
                        continue
                    ok = await self._download_and_extract(manifest, packs_dir, client)
                    if ok:
                        packs_downloaded += 1
                    else:
                        errors.append(f"Pack download failed: {manifest.name}")

                # Step 4: Download eligible mappings (same authenticated client)
                mappings_dir = self._cache_dir / "mappings"
                mappings_dir.mkdir(parents=True, exist_ok=True)
                mapping_manifests = self._parse_catalog(mapping_data, kind="mapping")

                for manifest in mapping_manifests:
                    # CRITICAL-1 (code-audit-2026-05-22): same as packs above.
                    if manifest.status not in ("active", "production"):
                        continue
                    if not self._tier_allowed(manifest.min_tier, current_tier):
                        continue
                    ok = await self._download_and_extract(manifest, mappings_dir, client)
                    if ok:
                        mappings_downloaded += 1
                    else:
                        errors.append(f"Mapping download failed: {manifest.name}")

            # Step 6: Load price-table artifacts from the mappings cache
            self._load_price_tables_from_cache()

            # Step 7: Persist last_known_good
            self._persist_last_known_good(current_tier)
            self._last_refresh = time.time()

            logger.info(
                "event=intelligence_refresh_complete packs=%d mappings=%d errors=%d",
                packs_downloaded, mappings_downloaded, len(errors),
            )
            return {
                "packs_downloaded": packs_downloaded,
                "mappings_downloaded": mappings_downloaded,
                "errors": errors,
            }

    # ── Background task ───────────────────────────────────────────────────────

    async def start_refresh_task(self, *, prewarm: bool | None = None) -> None:
        """Start the background refresh loop. Fires every refresh_interval_hours.

        P0-16: when ``IntelligenceConfig.prewarm_on_start`` is True (default),
        an immediate refresh is fired before scheduling the background loop so
        the cache is populated before the proxy starts serving traffic. The
        pre-warm is bounded by ``refresh()``'s own 30 s per-request timeout and
        is best-effort: a CDN unreachable at startup is logged loudly but does
        NOT block startup — the proxy runs with whatever is already in
        ``cache_dir`` (or with no policies on a true cold start) and the
        background loop will retry on the regular cadence.
        """
        should_prewarm = self._config.prewarm_on_start if prewarm is None else prewarm
        if should_prewarm:
            try:
                # force=True so the very first cold-start refresh proceeds even
                # though _last_refresh is still 0. We rely on refresh()'s own
                # lock + httpx timeout to bound the call.
                result = await self.refresh(force=True)
                errors_raw = result.get("errors") or []
                errors: list[object] = errors_raw if isinstance(errors_raw, list) else []
                if errors:
                    logger.warning(
                        "event=intelligence_prewarm_partial packs=%s mappings=%s errors=%d "
                        "— proxy continues with whatever is on disk",
                        result.get("packs_downloaded", 0),
                        result.get("mappings_downloaded", 0),
                        len(errors),
                    )
                else:
                    logger.info(
                        "event=intelligence_prewarm_complete packs=%s mappings=%s",
                        result.get("packs_downloaded", 0),
                        result.get("mappings_downloaded", 0),
                    )
            except Exception as exc:  # noqa: BLE001
                # Loud + visible: this is the "CDN unreachable at startup"
                # branch. We fail open (no enforced policies) but never block
                # the proxy from accepting traffic.
                logger.error(
                    "event=intelligence_prewarm_failed error=%s "
                    "— proxy is starting with empty/stale intelligence cache",
                    exc,
                )

        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._config.refresh_interval_hours * 3600)
                try:
                    await self.refresh(force=True)
                except Exception as exc:  # noqa: BLE001
                    logger.error("event=intelligence_refresh_task_error error=%s", exc)

        self._refresh_task = asyncio.create_task(_loop())
        logger.info("event=intelligence_refresh_task_started interval_hours=%d", self._config.refresh_interval_hours)

    # ── Price-table loading ───────────────────────────────────────────────────

    def _verify_price_table_signature(self, json_path: Path) -> bool:
        """Verify the Ed25519 signature on a price-table JSON artifact.

        Looks for a sibling ``.signed.json`` file produced by the tessera-intelligence
        signing pipeline. The ``.signed.json`` carries:
          * ``content_hash`` -- hex SHA-256 of the raw file bytes
          * ``signature``    -- base64 Ed25519 over ``content_hash.encode("utf-8")``

        Returns True when the signature verifies, False otherwise (logs the reason).
        The caller loads the price table regardless so the proxy is not hard-blocked
        on a missing sidecar during development.
        """
        signed_path = json_path.parent / (json_path.name + ".signed.json")
        if not signed_path.exists():
            logger.warning(
                "event=price_table_no_signed_json path=%s â loading unverified",
                json_path,
            )
            return False

        try:
            signed_data: dict[str, Any] = json.loads(signed_path.read_text(encoding="utf-8"))
            stored_hash: str = signed_data.get("content_hash", "") or ""
            stored_sig: str = signed_data.get("signature", "") or ""
            if not stored_hash or not stored_sig:
                logger.warning(
                    "event=price_table_signed_json_incomplete path=%s missing=content_hash/signature",
                    signed_path,
                )
                return False

            # Hash the raw JSON file bytes and compare to stored content_hash.
            raw_bytes = json_path.read_bytes()
            actual_hash = hashlib.sha256(raw_bytes).hexdigest()
            if actual_hash != stored_hash:
                logger.error(
                    "event=price_table_hash_mismatch path=%s expected=%.16s actual=%.16s",
                    json_path,
                    stored_hash,
                    actual_hash,
                )
                return False

            # Ed25519 verify: signature is over the hex content_hash UTF-8 bytes.
            sig_bytes = base64.b64decode(stored_sig)
            self._public_key.verify(sig_bytes, stored_hash.encode("utf-8"))
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "event=price_table_sig_verify_failed path=%s error=%s",
                json_path,
                exc,
            )
            return False

    def _load_price_tables_from_cache(self) -> None:
        """Scan the mappings cache for price-table JSON artifacts and load them.

        Files matching ``*-prices-*.json`` in any mappings subdirectory are
        treated as price-table artifacts.  Covers all three providers (aws,
        azure, gcp) — the glob is provider-agnostic.

        Each artifact is:
          1. Signature-verified via a sibling ``.signed.json`` (best-effort).
          2. Loaded into a ``PriceTable`` instance.
          3. Stored in ``self._price_tables`` keyed by provider.
          4. Registered in the ``tessera.cost`` module-level registry via
             ``register_price_table()`` so ``cost_for_call()`` can resolve it.
        """
        from tessera.cost import register_price_table  # noqa: PLC0415
        from tessera.cost.price_table import PriceTable  # noqa: PLC0415

        mappings_dir = self._cache_dir / "mappings"
        if not mappings_dir.exists():
            return

        for json_path in mappings_dir.rglob("*-prices-*.json"):
            try:
                sig_ok = self._verify_price_table_signature(json_path)
                pt = PriceTable(json_path, signature_verified=sig_ok)
                provider = pt.provider
                self._price_tables[provider] = pt
                register_price_table(provider, pt)
                logger.info(
                    "event=price_table_loaded provider=%s version=%s ops=%d sig_verified=%s",
                    provider,
                    pt.bundle_version,
                    pt.operation_count,
                    sig_ok,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "event=price_table_load_failed path=%s error=%s", json_path, exc
                )

    # ── Public price-table accessor ───────────────────────────────────────────

    def get_price_table(self, provider: str = "aws") -> PriceTable | None:
        """Return the loaded price-table for a provider, or None if not yet refreshed."""
        return self._price_tables.get(provider)

    # ── Cache accessors ───────────────────────────────────────────────────────

    def get_cached_packs(self) -> list[Path]:
        """Return verified, currently eligible cached pack directories.

        Signed-manifest packs retain their signed manifest beside extracted
        content so disk cache reads repeat the identity check performed on
        download. A fresh signed catalog from this process supplies the expected
        identity/tier contract; without it, cached packs are not exposed.
        """
        packs_dir = self._cache_dir / "packs"
        if not packs_dir.exists() or self._cached_pack_catalog is None or self._cached_pack_tier is None:
            return []
        result: list[Path] = []
        for name_dir in sorted(packs_dir.iterdir()):
            if name_dir.is_dir():
                for version_dir in sorted(name_dir.iterdir()):
                    if version_dir.is_dir():
                        catalog_entry = self._cached_pack_catalog.get(
                            (name_dir.name, self._normalized_version(version_dir.name)),
                        )
                        if (catalog_entry is None or catalog_entry.status not in ("active", "production")
                                or not self._tier_allowed(catalog_entry.min_tier, self._cached_pack_tier)):
                            continue
                        marker = version_dir / ".tessera-manifest.signed.json"
                        archive = version_dir / ".tessera-verified.tar.gz"
                        if catalog_entry.manifest_url or marker.exists():
                            try:
                                if (not marker.is_file() or marker.is_symlink()
                                        or not archive.is_file() or archive.is_symlink()):
                                    raise ValueError("verified manifest or archive missing")
                                signed_manifest = json.loads(marker.read_text(encoding="utf-8"))
                                if not isinstance(signed_manifest, dict):
                                    raise ValueError("cached manifest must be an object")
                                self._verify_signed_manifest_data(signed_manifest, str(marker))
                                self._verify_manifest_catalog_agreement(signed_manifest, catalog_entry)
                                if not self._verify_content_hash(
                                    archive.read_bytes(), signed_manifest.get("tarball_sha256", ""),
                                ):
                                    raise ValueError("cached verified archive hash mismatch")
                            except Exception as exc:  # noqa: BLE001 — cached content fails closed
                                logger.warning("event=cached_pack_manifest_rejected path=%s error=%s", marker, exc)
                                continue
                        result.append(version_dir)
        return result

    def load_selected_policies(self) -> list[Any]:
        """Load only exact, currently entitled selections from verified archives.

        Persistent extracted files are deliberately ignored: they are editable
        cache contents and the signed pack manifest carries only paths, not
        per-file hashes. Each activation rehashes the retained signed tarball,
        then parses policy bytes directly from that archive.
        """
        import io
        import yaml

        from tessera.policy.loader import _compile_regex_fields, _sort_conditions
        from tessera.policy.schema import Policy

        selections = self._config.selected_packs
        if not selections:
            return []
        if self._config.allow_unsigned_catalog:
            raise ValueError("selected packs require signed catalogs")
        if self._cached_pack_catalog is None or self._cached_pack_tier is None:
            raise ValueError("selected packs require a freshly verified catalog")

        license_status = self._current_license_status
        if license_status is None or license_status.tier != self._cached_pack_tier:
            raise ValueError("selected packs require a current authenticated license status")
        if self._cached_pack_tier != "free":
            expires_at = license_status.expires_at
            if expires_at is None or expires_at.tzinfo is None:
                raise ValueError("paid selected packs require a bounded license expiry")
            from datetime import UTC, datetime
            if datetime.now(UTC) >= expires_at.astimezone(UTC):
                raise ValueError("selected-pack license has expired")

        loaded: list[Policy] = []
        seen_ids: set[str] = set()
        seen_selections: set[tuple[str, str]] = set()
        for selection in sorted(selections):
            name, separator, version = selection.partition("@")
            if not separator or not name or not version:
                raise ValueError(f"invalid selected pack pin: {selection!r}")
            key = (name, self._normalized_version(version))
            if key in seen_selections:
                raise ValueError(f"duplicate selected pack pin: {selection!r}")
            seen_selections.add(key)
            catalog_entry = self._cached_pack_catalog.get(key)
            if catalog_entry is None:
                raise ValueError(f"selected pack is absent from current catalog: {selection}")
            if catalog_entry.status not in ("active", "production"):
                raise ValueError(f"selected pack is not production: {selection}")
            if not self._tier_allowed(catalog_entry.min_tier, self._cached_pack_tier):
                raise ValueError(f"license tier does not allow selected pack: {selection}")
            if not catalog_entry.manifest_url:
                raise ValueError(f"selected pack has no signed manifest URL: {selection}")

            version_dir = self._cache_dir / "packs" / name / catalog_entry.version
            marker = version_dir / ".tessera-manifest.signed.json"
            archive_path = version_dir / ".tessera-verified.tar.gz"
            if (version_dir.is_symlink() or version_dir.parent.is_symlink()
                    or not marker.is_file() or marker.is_symlink()
                    or not archive_path.is_file() or archive_path.is_symlink()):
                raise ValueError(f"selected pack cache is incomplete; refresh required: {selection}")
            signed_manifest = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(signed_manifest, dict):
                raise ValueError(f"selected pack signed manifest is malformed: {selection}")
            self._verify_signed_manifest_data(signed_manifest, str(marker))
            self._verify_manifest_catalog_agreement(signed_manifest, catalog_entry)
            if signed_manifest.get("status") not in ("active", "production"):
                raise ValueError(f"signed pack manifest is not production: {selection}")

            archive_bytes = archive_path.read_bytes()
            expected_hash = signed_manifest.get("tarball_sha256", "")
            if not self._valid_sha256(expected_hash) or not self._verify_content_hash(archive_bytes, expected_hash):
                raise ValueError(f"selected pack retained archive failed integrity check: {selection}")

            declared_files = signed_manifest.get("files")
            policy_entries = signed_manifest.get("policies")
            if (not isinstance(declared_files, list) or not all(isinstance(p, str) for p in declared_files)
                    or not isinstance(policy_entries, list)):
                raise ValueError(f"selected pack manifest file list is malformed: {selection}")
            declared = set(declared_files)
            if len(declared) != len(declared_files):
                raise ValueError(f"selected pack manifest has duplicate file paths: {selection}")

            archive_files: dict[str, bytes] = {}
            total_size = 0
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not self._safe_archive_member(member.name):
                        raise ValueError(f"unsafe selected-pack archive path: {member.name!r}")
                    if member.isdir():
                        continue
                    if not member.isfile() or member.name in archive_files:
                        raise ValueError(f"unsupported or duplicate selected-pack member: {member.name!r}")
                    total_size += member.size
                    if total_size > 64 * 1024 * 1024:
                        raise ValueError(f"selected-pack archive expands beyond limit: {selection}")
                    if member.name not in declared:
                        raise ValueError(f"archive file is absent from signed manifest: {member.name!r}")
                    stream = tf.extractfile(member)
                    if stream is None:
                        raise ValueError(f"selected-pack archive member cannot be read: {member.name!r}")
                    archive_files[member.name] = stream.read()
            if set(archive_files) != declared:
                raise ValueError(f"signed manifest and archive file list differ: {selection}")

            for entry in policy_entries:
                if not isinstance(entry, dict):
                    raise ValueError(f"selected pack policy record is malformed: {selection}")
                policy_id = entry.get("id")
                filename = entry.get("file")
                if not isinstance(policy_id, str) or not isinstance(filename, str) or filename not in archive_files:
                    raise ValueError(f"selected pack policy file is missing: {selection}")
                if policy_id in seen_ids:
                    raise ValueError(f"duplicate policy id across selected packs: {policy_id}")
                raw = yaml.safe_load(archive_files[filename])
                if not isinstance(raw, dict):
                    raise ValueError(f"selected policy must be a YAML mapping: {filename}")
                policy = Policy.model_validate(raw)
                if policy.id != policy_id:
                    raise ValueError(f"signed policy id differs from policy file: {filename}")
                if entry.get("action") != policy.action.value:
                    raise ValueError(f"signed policy action differs from policy file: {filename}")
                _compile_regex_fields(policy)
                policy.when = _sort_conditions(policy.when)
                seen_ids.add(policy.id)
                loaded.append(policy)
        return sorted(loaded, key=lambda p: (-p.priority, p.id))

    def get_cached_mappings_dir(self) -> Path:
        """Return the root directory containing cached mapping bundle artifacts."""
        return self._cache_dir / "mappings"
