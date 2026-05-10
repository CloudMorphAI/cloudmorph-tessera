"""Policy loader with per-file reload error isolation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from tessera.errors import PolicyError
from tessera.policy.action_verbs import ACTION_VERBS, load_user_mappings, merge_mappings
from tessera.policy.regex_safety import validate_pattern
from tessera.policy.schema import (
    ArgContainsPattern,
    ArgMatchesRegex,
    IntentPurposeMatches,
    Policy,
)

logger = logging.getLogger(__name__)


def _regex_fields_in_policy(policy: Policy) -> list[str]:
    """Collect all regex pattern strings from a Policy that need safety validation."""
    patterns: list[str] = []
    if policy.match.tool_pattern is not None:
        patterns.append(policy.match.tool_pattern)
    for cond in _iter_conditions(policy.when):
        if isinstance(cond, (ArgMatchesRegex, ArgContainsPattern, IntentPurposeMatches)):
            patterns.append(cond.pattern)
    return patterns


def _iter_conditions(conditions: list[Any]) -> list[Any]:
    """Flatten nested conditions (AnyOf/NoneOf) into a single iterable."""
    from tessera.policy.schema import AnyOf, NoneOf

    result: list[Any] = []
    for cond in conditions:
        result.append(cond)
        if isinstance(cond, (AnyOf, NoneOf)):
            result.extend(_iter_conditions(cond.conditions))
    return result


class FilesystemPolicyLoader:
    """Loads policies from a directory. Implements per-file reload error isolation."""

    def __init__(self, policy_dir: str | Path, reload_mode: str = "none") -> None:
        self._dir = Path(policy_dir)
        self._reload_mode = reload_mode
        self._policies: dict[str, Policy] = {}  # str(path) -> Policy
        self._errors: dict[str, str] = {}  # str(path) -> error message
        self._action_verbs: dict[str, Any] = {}  # merged user mappings
        self._callbacks: list[Callable[[list[Policy]], None]] = []
        self._observer: Any = None  # watchdog Observer (if any)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_all(self, scope: str = "default") -> list[Policy]:
        """Load/reload all policies from the policy directory.

        On first call (startup): any single failure raises PolicyError immediately.
        On subsequent calls (reload): failures are isolated — prior version kept,
        error recorded; removed files are dropped from the registry.

        Returns policies sorted: descending priority, ascending id.
        """
        is_startup = not self._policies and not self._errors

        yaml_files = set(self._dir.glob("*.yaml")) | set(self._dir.glob("*.yml"))

        # Track which paths are still present on disk
        seen_paths: set[str] = set()

        for path in sorted(yaml_files):
            fname = path.name

            # _action_verbs.yaml is a config file, not a policy
            if fname == "_action_verbs.yaml":
                try:
                    user = load_user_mappings(path)
                    self._action_verbs = merge_mappings(ACTION_VERBS, user)
                except Exception as exc:
                    msg = f"failed to load _action_verbs.yaml: {exc}"
                    logger.error("event=action_verbs_load_failed path=%s error=%s", path, exc)
                    if is_startup:
                        raise PolicyError(msg, path=str(path)) from exc
                continue

            # Skip all other files starting with _
            if fname.startswith("_"):
                continue

            seen_paths.add(str(path))
            self._load_file(path, is_startup=is_startup)

        # Drop policies whose files no longer exist on disk
        for gone_path in list(self._policies.keys()):
            if gone_path not in seen_paths:
                logger.info("event=policy_removed path=%s", gone_path)
                del self._policies[gone_path]
        # Also clean up stale errors for removed files
        for gone_path in list(self._errors.keys()):
            if gone_path not in seen_paths:
                del self._errors[gone_path]

        # Duplicate id check — same id in two different files
        seen_ids: dict[str, str] = {}
        for path_str, policy in self._policies.items():
            if policy.id in seen_ids:
                msg = f"duplicate policy id {policy.id!r}: found in {seen_ids[policy.id]!r} and {path_str!r}"
                if is_startup:
                    raise PolicyError(msg)
                logger.error(
                    "event=policy_duplicate_id policy_id=%s paths=%s,%s",
                    policy.id,
                    seen_ids[policy.id],
                    path_str,
                )
            else:
                seen_ids[policy.id] = path_str

        return self._sorted_policies()

    def watch(self, scope: str, callback: Callable[[list[Policy]], None]) -> None:
        """Start watching for file changes.

        Only active when reload_mode == 'watch'. Uses watchdog's
        PollingObserver so it works on any filesystem (including containers
        with mounted volumes where inotify may be unavailable).
        """
        self._callbacks.append(callback)
        if self._reload_mode != "watch":
            return

        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers.polling import PollingObserver
        except ImportError:
            logger.warning(
                "event=watchdog_unavailable message='watchdog not installed; file-watch reload disabled'"
            )
            return

        loader_ref = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                src = getattr(event, "src_path", "")
                if not src.endswith((".yaml", ".yml")):
                    return
                logger.info("event=policy_file_changed path=%s", src)
                try:
                    updated = loader_ref.load_all(scope)
                except PolicyError as exc:
                    logger.error("event=reload_failed error=%s", exc)
                    return
                for cb in loader_ref._callbacks:
                    try:
                        cb(updated)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("event=reload_callback_error error=%s", exc)

        observer = PollingObserver()
        observer.schedule(_Handler(), str(self._dir), recursive=False)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """Stop watchdog observer if running."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    def state(self) -> dict[str, Any]:
        """Return {loaded: int, errored: [{path: str, error: str}]}."""
        return {
            "loaded": len(self._policies),
            "errored": [{"path": k, "error": v} for k, v in self._errors.items()],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_file(self, path: Path, *, is_startup: bool) -> None:
        """Parse, validate, and regex-check a single policy file.

        On failure during startup, raises PolicyError.
        On failure during reload, records error and keeps prior version.
        """
        path_str = str(path)
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if not isinstance(raw, dict):
                raise PolicyError(
                    f"policy file must be a YAML mapping, got {type(raw).__name__}",
                    path=path_str,
                )
            policy = Policy.model_validate(raw)

            # Run regex safety checks on all pattern fields
            for pattern in _regex_fields_in_policy(policy):
                validate_pattern(pattern)

            # Success — store and clear any prior error
            self._policies[path_str] = policy
            self._errors.pop(path_str, None)
            logger.debug("event=policy_loaded path=%s id=%s", path, policy.id)

        except (PolicyError, ValidationError, yaml.YAMLError, ValueError) as exc:
            error_msg = str(exc)
            logger.error(
                "event=%s path=%s error=%s",
                "policy_validation_failed" if is_startup else "policy_reload_skipped",
                path,
                exc,
            )
            if is_startup:
                raise PolicyError(error_msg, path=path_str) from exc
            # Reload: keep prior version, record error
            self._errors[path_str] = error_msg

    def _sorted_policies(self) -> list[Policy]:
        """Return policies sorted by descending priority then ascending id."""
        return sorted(
            self._policies.values(),
            key=lambda p: (-p.priority, p.id),
        )
