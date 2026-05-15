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
from typing import TYPE_CHECKING, Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

if TYPE_CHECKING:
    from tessera.config import IntelligenceConfig
    from tessera.cost.price_table import PriceTable
    from tessera.intelligence.license import LicenseStatus, LicenseValidator

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
        self._refresh_task: asyncio.Task[None] | None = None
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

    def _verify_signature(self, data: bytes, signature_hex: str) -> None:
        """Verify Ed25519 signature. Raises cryptography.exceptions.InvalidSignature on failure."""
        sig_bytes = bytes.fromhex(signature_hex)
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
          * Either field missing → raise ``ValueError`` unless
            ``IntelligenceConfig.allow_unsigned_catalog`` is True, in which case
            a warning is logged and verification is skipped.

        The fail-closed default plugs the F2 gap where empty
        ``signature`` / ``body_bytes_hex`` fields silently disabled signature
        verification for the entire catalog.
        """
        sig = catalog_data.get("signature", "") or ""
        body = catalog_data.get("body_bytes_hex", "") or ""
        if sig and body:
            try:
                self._verify_signature(bytes.fromhex(body), sig)
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

    def _verify_tarball_hash(self, tarball_bytes: bytes, expected_sha256: str) -> None:
        """Raise TamperDetected when SHA-256 of tarball_bytes != expected_sha256."""
        from tessera.errors import TamperDetected
        actual = hashlib.sha256(tarball_bytes).hexdigest()
        if actual != expected_sha256:
            raise TamperDetected(
                f"Tarball hash mismatch: expected {expected_sha256!r}, got {actual!r}"
            )

    # ── Catalog parsing ───────────────────────────────────────────────────────

    def _parse_catalog(self, catalog_data: dict[str, Any], kind: str = "pack") -> list[PackManifest]:
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

        # Verify tarball-level SHA-256 if the manifest carries tarball_sha256.
        # This is separate from content_hash — content_hash covers the extracted
        # payload; tarball_sha256 binds the transport artifact to the signed manifest.
        tarball_sha256 = getattr(manifest, "tarball_sha256", None)
        if tarball_sha256:
            try:
                self._verify_tarball_hash(raw, tarball_sha256)
            except Exception as exc:  # noqa: BLE001 — log + skip on TamperDetected
                logger.error(
                    "event=tarball_hash_mismatch name=%s error=%s",
                    manifest.name, exc,
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

            # Step 0: Resolve license JWT for CDN tier-gating.
            # The CDN's CloudFront Function reads `X-Tessera-License` and
            # rejects with 401 when absent. Per status/intelligence-and-licensing.md
            # the JWT must accompany every fetch.
            current_tier = "free"
            license_jwt: str | None = None
            if self._license is not None:
                try:
                    license_status: LicenseStatus = await self._license.check()
                    current_tier = license_status.tier
                    license_jwt = license_status.jwt
                except Exception as exc:  # noqa: BLE001
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

                # Step 4: Download eligible mappings (same authenticated client)
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

    async def start_refresh_task(self) -> None:
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
        if self._config.prewarm_on_start:
            try:
                # force=True so the very first cold-start refresh proceeds even
                # though _last_refresh is still 0. We rely on refresh()'s own
                # lock + httpx timeout to bound the call.
                result = await self.refresh(force=True)
                errors = result.get("errors") or []
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

    def _load_price_tables_from_cache(self) -> None:
        """Scan the mappings cache for price-table JSON artifacts and load them.

        Files matching ``*-prices-*.json`` in any mappings subdirectory are
        treated as price-table artifacts.  Each is loaded into a ``PriceTable``
        instance and stored in ``self._price_tables`` keyed by provider.
        """
        from tessera.cost.price_table import PriceTable

        mappings_dir = self._cache_dir / "mappings"
        if not mappings_dir.exists():
            return

        for json_path in mappings_dir.rglob("*-prices-*.json"):
            try:
                pt = PriceTable(json_path, signature_verified=False)
                provider = pt.provider
                self._price_tables[provider] = pt
                logger.info(
                    "event=price_table_loaded provider=%s version=%s ops=%d path=%s",
                    provider,
                    pt.bundle_version,
                    pt.operation_count,
                    json_path,
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
