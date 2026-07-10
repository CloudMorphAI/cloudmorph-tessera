"""Cloud-backed policy loader — enforce your Tessera Cloud dashboard policies locally.

This closes the loop the product describes: a policy authored in the web dashboard,
synced down with ``tessera config sync``, is enforced by the local ``tessera serve``
proxy against Cursor / Claude / Claude Code tool calls. Until this loader shipped,
``tessera serve`` only ever loaded local YAML from ``policies.dir`` — the synced
cache at ``~/.tessera/policy-cache.db`` was written but never read.

Selection: a drop-in replacement for :class:`~tessera.policy.loader.FilesystemPolicyLoader`.
Activate it with ``tessera serve --cloud`` or
``TESSERA_POLICY_LOADER=tessera.policy.cloud_loader:CloudBackedPolicyLoader``. It exposes
the same ``load_all`` / ``watch`` / ``stop`` / ``state`` surface, so
:func:`tessera.proxy.create_app` needs no changes — it already resolves the loader
class through that env var and already rebuilds the engine + clears the
``DecisionCache`` on every reload callback.

Layering: local YAML in ``policies.dir`` is the base; **enabled** cloud policies from
the sync cache are layered on top. On an id collision the cloud (dashboard) version
wins, so a dashboard edit overrides a local stub. A cloud policy that fails to parse
is isolated (logged + surfaced in ``state()``) and never blocks startup — a broken
*local* file is the operator's own and should still hard-fail the boot, but a broken
*remote* policy must not take the proxy down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from tessera.errors import PolicyError
from tessera.policy.loader import (
    FilesystemPolicyLoader,
    _compile_regex_fields,
    _sort_conditions,
)
from tessera.policy.schema import Policy

logger = logging.getLogger(__name__)

_OAUTH_TOKEN_FILE = Path.home() / ".tessera" / "oauth.json"
_DEFAULT_CACHE_PATH = Path.home() / ".tessera" / "policy-cache.db"
_DEFAULT_REFRESH_TTL = 300  # seconds


def _read_oauth_tokens() -> dict[str, Any] | None:
    """Read ``~/.tessera/oauth.json`` (written by ``tessera login``); None if absent/bad.

    Duplicated minimally from cli.py to avoid a cli → proxy → loader import cycle.
    """
    if not _OAUTH_TOKEN_FILE.exists():
        return None
    try:
        return json.loads(_OAUTH_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_policy_yaml(yaml_text: str, *, source: str) -> Policy:
    """Parse a policy body string into a validated, regex-compiled, sorted Policy.

    Mirrors :meth:`FilesystemPolicyLoader._load_file` minus the file IO, so a cloud
    policy is treated identically to an on-disk one by the engine.
    """
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise PolicyError(
            f"policy body must be a YAML mapping, got {type(raw).__name__}",
            path=source,
        )
    policy = Policy.model_validate(raw)
    _compile_regex_fields(policy)
    policy.when = _sort_conditions(policy.when)
    return policy


class CloudBackedPolicyLoader:
    """FilesystemPolicyLoader + enabled Tessera Cloud policies from the sync cache."""

    def __init__(
        self,
        policy_dir: str | Path,
        reload_mode: str = "none",
        *,
        cache_path: str | Path | None = None,
        oauth_tokens: dict[str, Any] | None = None,
        endpoint: str | None = None,
        refresh_ttl: int = _DEFAULT_REFRESH_TTL,
        sync_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._fs = FilesystemPolicyLoader(policy_dir, reload_mode=reload_mode)
        self._reload_mode = reload_mode
        # Precedence: explicit arg > TESSERA_POLICY_CACHE env (Docker mounts) > default.
        self._cache_path = Path(
            cache_path or os.environ.get("TESSERA_POLICY_CACHE") or _DEFAULT_CACHE_PATH
        )
        # token/endpoint drive the optional background refresh only; reading the
        # cache works offline with no token.
        self._tokens = oauth_tokens if oauth_tokens is not None else _read_oauth_tokens()
        self._endpoint = endpoint
        self._refresh_ttl = max(30, int(refresh_ttl))
        self._sync_factory = sync_factory  # injectable for tests
        self._callbacks: list[Callable[[list[Policy]], None]] = []
        self._cloud_errors: dict[str, str] = {}
        self._cloud_count = 0
        self._stop_evt: threading.Event | None = None
        self._refresh_thread: threading.Thread | None = None

    # ── Public API (mirrors FilesystemPolicyLoader) ──────────────────────────────

    def load_all(self, scope: str = "default") -> list[Policy]:
        """Local YAML ∪ enabled cloud-cache policies, merged and sorted.

        Startup errors on **local** policies still propagate (hard-fail the boot);
        cloud policy errors are isolated.
        """
        local = self._fs.load_all(scope)
        cloud = self._load_cloud_policies()
        merged = self._merge(local, cloud)
        logger.info(
            "event=cloud_loader_loaded local=%d cloud=%d merged=%d cache=%s",
            len(local), self._cloud_count, len(merged), self._cache_path,
        )
        return merged

    def watch(self, scope: str, callback: Callable[[list[Policy]], None]) -> None:
        """Register *callback*; re-fire it on local file changes and cloud refreshes."""
        self._callbacks.append(callback)
        # A local file change must re-merge cloud too, so wrap the fs callback.
        self._fs.watch(scope, lambda _local_only: self._fire(scope))
        if self._reload_mode == "watch":
            self._start_background_refresh(scope)

    def stop(self) -> None:
        if self._stop_evt is not None:
            self._stop_evt.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=5)
            self._refresh_thread = None
        self._fs.stop()

    def state(self) -> dict[str, Any]:
        st = self._fs.state()
        st["cloud_loaded"] = self._cloud_count
        st["cloud_errored"] = [
            {"policy_id": k, "error": v} for k, v in self._cloud_errors.items()
        ]
        return st

    # ── Internals ────────────────────────────────────────────────────────────────

    def _load_cloud_policies(self) -> list[Policy]:
        rows = self._read_cache_rows()
        out: list[Policy] = []
        self._cloud_errors = {}
        for row in rows:
            pid = row.get("policy_id") or "<unknown>"
            if not bool(row.get("enabled")):
                continue  # disabled in the dashboard → not enforced
            body = (row.get("yaml_text") or "").strip()
            if not body:
                # Metadata-only row: the current backend /api/cli/policies omits
                # yaml_text, so a synced policy can arrive body-less. Surface it in
                # state() rather than silently enforcing nothing.
                self._cloud_errors[pid] = "empty yaml_text (no policy body synced from cloud)"
                continue
            try:
                out.append(_parse_policy_yaml(body, source=f"cloud:{pid}"))
            except (PolicyError, ValidationError, yaml.YAMLError, ValueError) as exc:
                self._cloud_errors[pid] = str(exc)
                logger.error(
                    "event=cloud_policy_parse_failed policy_id=%s error=%s", pid, exc
                )
        self._cloud_count = len(out)
        return out

    def _read_cache_rows(self) -> list[dict[str, Any]]:
        if not self._cache_path.exists():
            return []
        try:
            conn = sqlite3.connect(self._cache_path)
        except sqlite3.Error as exc:
            logger.warning(
                "event=cloud_cache_open_failed path=%s error=%s", self._cache_path, exc
            )
            return []
        try:
            cur = conn.execute("SELECT policy_id, enabled, yaml_text FROM policies")
            return [
                {"policy_id": r[0], "enabled": bool(r[1]), "yaml_text": r[2]}
                for r in cur.fetchall()
            ]
        except sqlite3.Error as exc:
            logger.warning("event=cloud_cache_read_failed error=%s", exc)
            return []
        finally:
            conn.close()

    @staticmethod
    def _merge(local: list[Policy], cloud: list[Policy]) -> list[Policy]:
        by_id: dict[str, Policy] = {p.id: p for p in local}
        for p in cloud:
            by_id[p.id] = p  # cloud (dashboard) overrides local on id collision
        # Same ordering contract as FilesystemPolicyLoader: desc priority, asc id.
        return sorted(by_id.values(), key=lambda p: (-p.priority, p.id))

    def _fire(self, scope: str) -> None:
        try:
            merged = self.load_all(scope)
        except PolicyError as exc:
            logger.error("event=cloud_reload_failed error=%s", exc)
            return
        for cb in self._callbacks:
            try:
                cb(merged)
            except Exception as exc:  # noqa: BLE001
                logger.error("event=cloud_reload_callback_error error=%s", exc)

    def _start_background_refresh(self, scope: str) -> None:
        token = (self._tokens or {}).get("access_token")
        if not token:
            logger.info(
                "event=cloud_refresh_disabled reason=not_logged_in message="
                "'serving cached cloud policies only; run `tessera login` + `tessera config sync` to refresh'"
            )
            return
        endpoint = self._endpoint or (self._tokens or {}).get("issuer")
        self._stop_evt = threading.Event()

        if self._sync_factory is not None:
            sync = self._sync_factory(
                endpoint=endpoint, oauth_token=token, cache_path=self._cache_path
            )
        else:
            from tessera.cloud_sync import CloudPolicySync
            sync = CloudPolicySync(
                endpoint=endpoint, oauth_token=token, cache_path=self._cache_path
            )

        def _worker() -> None:
            assert self._stop_evt is not None
            # Wait first: load_all already seeded from cache (and `config sync` may
            # have too), so the first network pull can wait one interval.
            while not self._stop_evt.wait(self._refresh_ttl):
                try:
                    asyncio.run(sync.fetch_and_cache())
                    self._fire(scope)  # engine rebuild + DecisionCache clear
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "event=cloud_refresh_failed reason=%s message='serving last-known-good cache'",
                        type(exc).__name__,
                    )

        t = threading.Thread(target=_worker, name="tessera-cloud-refresh", daemon=True)
        t.start()
        self._refresh_thread = t
