"""Tessera Protocol-based event hook registry.

Hooks run async fire-and-forget; individual failures are logged + swallowed
so they never block the hot path.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class OnDecision(Protocol):
    """Hook called after every policy decision is made."""

    async def __call__(self, decision: Any, context: dict[str, Any]) -> None:
        ...


class OnAuditEmit(Protocol):
    """Hook called after every audit event is emitted."""

    async def __call__(self, event: dict[str, Any]) -> None:
        ...


_on_decision_hooks: list[OnDecision] = []
_on_audit_hooks: list[OnAuditEmit] = []


def register_on_decision(hook: OnDecision) -> None:
    """Register a hook to be called after each policy decision."""
    _on_decision_hooks.append(hook)


def register_on_audit_emit(hook: OnAuditEmit) -> None:
    """Register a hook to be called after each audit event is emitted."""
    _on_audit_hooks.append(hook)


def clear_hooks() -> None:
    """Reset all registered hooks. Test helper."""
    _on_decision_hooks.clear()
    _on_audit_hooks.clear()


async def fire_on_decision(decision: Any, context: dict[str, Any]) -> None:
    """Fire all OnDecision hooks. Failures logged + swallowed; never blocks hot path."""
    for hook in _on_decision_hooks:
        try:
            await hook(decision, context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=on_decision_hook_failed error=%s", exc)


async def fire_on_audit_emit(event: dict[str, Any]) -> None:
    """Fire all OnAuditEmit hooks. Failures logged + swallowed."""
    for hook in _on_audit_hooks:
        try:
            await hook(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=on_audit_hook_failed error=%s", exc)
