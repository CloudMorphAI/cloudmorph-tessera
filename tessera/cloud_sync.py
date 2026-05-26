"""Sync tenant policies from tessera.cloudmorph.ai → local SQLite cache.

Background loop refreshes every ``refresh_ttl`` seconds (default 300). On
cloud-unreachable, the cache is served unchanged (last-known-good). On
first-run with cloud unreachable, ``load_from_cache`` returns an empty list
— callers decide whether to HALT or proceed with no policies.

The cache lives at ``~/.tessera/policy-cache.db``. It is a single-file SQLite
DB with one table (``policies``) keyed on ``policy_id``. Each refresh runs an
upsert and never deletes — policies that were once cached survive a transient
cloud outage even if they no longer appear in the latest fetch. Hard purge
of removed policies happens on the next successful refresh that returns the
full set (a tombstone column is intentionally not modelled in v0.7.0).

Decision-memoization invalidation: callers should subscribe to refresh events
via ``on_refresh`` so the proxy's :class:`DecisionCache` can clear matching
entries when a policy version bumps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)


_DEFAULT_CACHE_PATH = Path.home() / ".tessera" / "policy-cache.db"
# v0.7.2: auth.tessera.cloudmorph.ai is the ApiMapping for tessera-api-prod
# (the HttpApi that hosts /oauth/* + /api/cli/* + /api/tessera/audit/ingest).
# Up to v0.7.1 this defaulted to https://tessera.cloudmorph.ai which routed
# to the ECS ALB (Tessera Cloud SaaS) and didn't reach the OAuth Lambda.
_DEFAULT_ENDPOINT = "https://auth.tessera.cloudmorph.ai"
_DEFAULT_REFRESH_TTL = 300  # 5 min
_FETCH_TIMEOUT = 10.0


class CloudPolicySync:
    """Periodic puller of tenant policies + SQLite-backed last-known-good cache.

    The remote endpoint is the Tessera-OAuth-authed mirror added in v0.7.0
    Item B: ``GET /api/cli/policies``. The CLI's Bearer token (issued via
    ``tessera login``) is used; the same scope ``tessera:policies:read`` that
    gates the route enforces tenant isolation server-side.
    """

    REMOTE_PATH = "/api/cli/policies"

    def __init__(
        self,
        endpoint: str | None = None,
        oauth_token: str | None = None,
        refresh_ttl: int = _DEFAULT_REFRESH_TTL,
        cache_path: Path | str | None = None,
        on_refresh: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self._endpoint = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")
        self._oauth_token = oauth_token or ""
        self._refresh_ttl = max(30, int(refresh_ttl))
        self._cache_path = Path(cache_path) if cache_path else _DEFAULT_CACHE_PATH
        self._on_refresh = on_refresh
        self._last_fetched_at: float | None = None
        self._init_db()

    # ── DB ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._cache_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policies (
                    policy_id        TEXT PRIMARY KEY,
                    pack_id          TEXT,
                    enabled          INTEGER,
                    priority         INTEGER,
                    action_summary   TEXT,
                    match_summary    TEXT,
                    yaml_text        TEXT,
                    fetched_at_unix  INTEGER NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # ── Public API ──────────────────────────────────────────────────────────

    async def fetch_and_cache(self) -> list[dict[str, Any]]:
        """Fetch the latest policies + write through to the SQLite cache.

        On HTTP errors, raises (callers decide whether to swallow); on a clean
        2xx-with-empty-body, treats it as zero policies (no purge).
        """
        if not self._oauth_token:
            raise RuntimeError(
                "CloudPolicySync.fetch_and_cache requires an oauth_token; "
                "run `tessera login` first"
            )

        url = f"{self._endpoint}{self.REMOTE_PATH}"
        headers = {
            "Authorization": f"Bearer {self._oauth_token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}

        # The cloud endpoint returns {"items": [...]} (mirrors browser shape);
        # some test fixtures use {"policies": [...]}. Accept either.
        raw_items = payload.get("items") or payload.get("policies") or []
        items: list[dict[str, Any]] = [
            self._normalize(item) for item in raw_items if isinstance(item, dict)
        ]
        self._write_through(items)
        self._last_fetched_at = time.time()

        logger.info(
            "event=cloud_policy_sync_refresh count=%d endpoint=%s",
            len(items),
            self._endpoint,
        )
        if self._on_refresh is not None:
            try:
                self._on_refresh(items)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "event=cloud_policy_sync_on_refresh_failed reason=%s",
                    type(exc).__name__,
                )
        return items

    def load_from_cache(self) -> list[dict[str, Any]]:
        """Return all rows currently in the SQLite cache."""
        conn = sqlite3.connect(self._cache_path)
        try:
            rows = conn.execute(
                """SELECT policy_id, pack_id, enabled, priority,
                          action_summary, match_summary, yaml_text, fetched_at_unix
                   FROM policies"""
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "policy_id":      r[0],
                "pack_id":        r[1],
                "enabled":        bool(r[2]),
                "priority":       int(r[3] or 0),
                "action_summary": r[4] or "",
                "match_summary":  r[5] or "",
                "yaml_text":      r[6] or "",
                "fetched_at":     int(r[7]),
            }
            for r in rows
        ]

    async def background_refresh_loop(self) -> None:
        """Run forever, refreshing every ``refresh_ttl`` seconds.

        Failures are swallowed (logged) so a transient cloud outage doesn't
        bring the local proxy down. The cache continues to serve last-known-
        good policies.
        """
        while True:
            try:
                await self.fetch_and_cache()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "event=cloud_policy_sync_refresh_failed reason=%s",
                    type(exc).__name__,
                )
            await asyncio.sleep(self._refresh_ttl)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        """Coerce a cloud-side row into the local schema."""
        return {
            "policy_id":      str(item.get("policy_id") or item.get("policyId") or ""),
            "pack_id":        str(item.get("pack_id") or item.get("packId") or ""),
            "enabled":        bool(item.get("enabled", False)),
            "priority":       int(item.get("priority") or 0),
            "action_summary": str(item.get("action_summary") or item.get("action") or ""),
            "match_summary":  str(item.get("match_summary") or item.get("match") or ""),
            "yaml_text":      str(
                item.get("yaml_text") or item.get("yaml") or item.get("yaml_body") or ""
            ),
        }

    def _write_through(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        now = int(time.time())
        conn = sqlite3.connect(self._cache_path)
        try:
            for item in items:
                if not item.get("policy_id"):
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO policies
                       (policy_id, pack_id, enabled, priority,
                        action_summary, match_summary, yaml_text, fetched_at_unix)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["policy_id"],
                        item["pack_id"],
                        1 if item["enabled"] else 0,
                        item["priority"],
                        item["action_summary"],
                        item["match_summary"],
                        item["yaml_text"],
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
