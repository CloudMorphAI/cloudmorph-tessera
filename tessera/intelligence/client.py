"""Intelligence client — downloads and caches Tessera intelligence packs.

Verifies Ed25519 signatures on catalog manifests and content hashes on pack archives.
Requires the ``cryptography`` package (included in the ``intelligence`` extra).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.resources
import json
import logging
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

if TYPE_CHECKING:
    from tessera.config import IntelligenceConfig
    from tessera.intelligence.license import LicenseValidator, LicenseStatus

logger = logging.getLogger(__name__)

# Maps tier names to their rank; packs with min_tier <= current tier rank are allowed.
_TIER_ORDER: dict[str, int] = {
    "free": 0,
    "developer": 1,
    "team": 2,
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
    status: str  # "active" | "deprecated" | etc.


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
        self._refresh_task: asyncio.Task | None = None  # type: ignore[type-arg]

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

    def _verify_signature(self, data: bytes, signature_hex: str) -> None:
        """Verify Ed25519 signature. Raises cryptography.exceptions.InvalidSignature on failure."""
        sig_bytes = bytes.fromhex(signature_hex)
        self._public_key.verify(sig_bytes, data)

    def _verify_content_hash(self, data: bytes, expected_hash: str) -> bool:
        """Return True if SHA-256 of data matches expected_hash (hex digest)."""
        actual = hashlib.sha256(data).hexdigest()
        return actual == expected_hash

    # ── Catalog parsing ───────────────────────────────────────────────────────

    def _parse_catalog(self, catalog_data: dict, kind: str = "pack") -> list[PackManifest]:
        """Parse a catalog JSON body into PackManifest list."""
        manifests: list[PackManifest] = []
        items = catalog_data.get("packs" if kind == "pack" else "mappings", [])
        for item in items:
            manifests.append(PackManifest(
                name=item["name"],
                version=item.get("version", "0.0.1"),
                min_tier=item.get("min_tier", "free"),
                content_hash=item.get("content_hash", ""),
                signature=item.get("signature", ""),
                pack_url=item.get("pack_url", item.get("mapping_url", "")),
                status=item.get("status", "active"),
            ))
        return manifests

    # ── Tier check ───────────────────────────────────────────────────────────

    def _tier_allowed(self, pack_tier: str, current_tier: str) -> bool:
        pack_rank = _TIER_ORDER.get(pack_tier, 0)
        current_rank = _TIER_ORDER.get(current_tier, 0)
        return current_rank >= pack_rank

    # ── Pack download + extract ───────────────────────────────────────────────

    async def _download_and_extract(
        self,
        manifest: PackManifest,
        dest_parent: Path,
        client: httpx.AsyncClient,
    ) -> bool:
        """Download, verify, and extract a pack or mapping archive. Returns True on success."""
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

        # Verify content hash
        if manifest.content_hash and not self._verify_content_hash(raw, manifest.content_hash):
            logger.error(
                "event=pack_hash_mismatch name=%s expected=%s",
                manifest.name, manifest.content_hash,
            )
            return False

        # Extract tar archive
        dest = dest_parent / manifest.name / manifest.version
        dest.mkdir(parents=True, exist_ok=True)

        try:
            import io
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                # Security: only extract regular files within the dest directory.
                for member in tf.getmembers():
                    if member.islnk() or member.issym():
                        continue
                    tf.extract(member, path=dest, filter="data")
        except tarfile.TarError as exc:
            # Not a tar archive — write raw file directly (for simple single-file packs)
            logger.debug("event=pack_not_tar name=%s writing raw error=%s", manifest.name, exc)
            dest_file = dest / f"{manifest.name}.json"
            dest_file.write_bytes(raw)

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
        """Download and verify all eligible packs and mappings.

        Returns a summary dict: {"packs_downloaded": N, "mappings_downloaded": M, "errors": [...]}.
        """
        async with self._refresh_lock:
            now = time.time()
            interval_seconds = self._config.refresh_interval_hours * 3600
            if not force and (now - self._last_refresh) < interval_seconds:
                return {"packs_downloaded": 0, "mappings_downloaded": 0, "errors": [], "skipped": True}

            errors: list[str] = []
            packs_downloaded = 0
            mappings_downloaded = 0

            async with httpx.AsyncClient(timeout=30) as client:
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

                # Step 2: Verify catalog signatures
                catalog_sig = catalog_data.get("signature", "")
                catalog_body = catalog_data.get("body_bytes_hex", "")
                if catalog_sig and catalog_body:
                    try:
                        self._verify_signature(bytes.fromhex(catalog_body), catalog_sig)
                    except Exception as exc:  # noqa: BLE001
                        raise ValueError(f"Pack catalog signature invalid: {exc}") from exc

                mapping_sig = mapping_data.get("signature", "")
                mapping_body = mapping_data.get("body_bytes_hex", "")
                if mapping_sig and mapping_body:
                    try:
                        self._verify_signature(bytes.fromhex(mapping_body), mapping_sig)
                    except Exception as exc:  # noqa: BLE001
                        raise ValueError(f"Mapping catalog signature invalid: {exc}") from exc

                # Step 3: Get current tier
                current_tier = "free"
                if self._license is not None:
                    try:
                        license_status: LicenseStatus = await self._license.check()
                        current_tier = license_status.tier
                    except Exception as exc:  # noqa: BLE001
                        if self._config.fail_closed_on_license_check:
                            raise ValueError(f"License check failed (fail_closed=True): {exc}") from exc
                        logger.warning("event=license_check_failed_fallback error=%s", exc)

                # Step 4: Download eligible packs
                packs_dir = self._cache_dir / "packs"
                packs_dir.mkdir(parents=True, exist_ok=True)
                pack_manifests = self._parse_catalog(catalog_data, kind="pack")

                for manifest in pack_manifests:
                    if manifest.status != "active":
                        continue
                    if not self._tier_allowed(manifest.min_tier, current_tier):
                        continue
                    ok = await self._download_and_extract(manifest, packs_dir, client)
                    if ok:
                        packs_downloaded += 1
                    else:
                        errors.append(f"Pack download failed: {manifest.name}")

                # Step 5: Download eligible mappings
                mappings_dir = self._cache_dir / "mappings"
                mappings_dir.mkdir(parents=True, exist_ok=True)
                mapping_manifests = self._parse_catalog(mapping_data, kind="mapping")

                for manifest in mapping_manifests:
                    if manifest.status != "active":
                        continue
                    if not self._tier_allowed(manifest.min_tier, current_tier):
                        continue
                    ok = await self._download_and_extract(manifest, mappings_dir, client)
                    if ok:
                        mappings_downloaded += 1
                    else:
                        errors.append(f"Mapping download failed: {manifest.name}")

            # Step 6: Persist last_known_good
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

    async def start_refresh_task(self) -> None:
        """Start the background refresh loop. Fires every refresh_interval_hours."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._config.refresh_interval_hours * 3600)
                try:
                    await self.refresh(force=True)
                except Exception as exc:  # noqa: BLE001
                    logger.error("event=intelligence_refresh_task_error error=%s", exc)

        self._refresh_task = asyncio.create_task(_loop())
        logger.info("event=intelligence_refresh_task_started interval_hours=%d", self._config.refresh_interval_hours)

    # ── Cache accessors ───────────────────────────────────────────────────────

    def get_cached_packs(self) -> list[Path]:
        """Return list of currently-cached pack directories (name/version pairs)."""
        packs_dir = self._cache_dir / "packs"
        if not packs_dir.exists():
            return []
        result: list[Path] = []
        for name_dir in sorted(packs_dir.iterdir()):
            if name_dir.is_dir():
                for version_dir in sorted(name_dir.iterdir()):
                    if version_dir.is_dir():
                        result.append(version_dir)
        return result

    def get_cached_mappings_dir(self) -> Path:
        """Return the root mappings cache directory for aws_mapping.load_extended_mappings()."""
        return self._cache_dir / "mappings"
