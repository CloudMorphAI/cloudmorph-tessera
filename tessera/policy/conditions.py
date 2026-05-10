"""Condition evaluators — one function per condition type."""

from __future__ import annotations

import json
import threading
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import regex  # type: ignore[import-untyped]

from tessera.policy.action_verbs import verbs_for
from tessera.policy.schema import (
    ActionClassIn,
    AnyOf,
    ArgContainsPattern,
    ArgEquals,
    ArgGreaterThan,
    ArgInSet,
    ArgLessThan,
    ArgMatchesRegex,
    ArgSizeGreaterThan,
    ConditionType,
    IntentClassIn,
    IntentPurposeMatches,
    MetaFieldEquals,
    NoneOf,
    RegionIn,
    TimeOfDayOutside,
    ToolNameIn,
)

# Patchable in tests: replace with a lambda returning a fixed datetime
_now_fn = datetime.now

_REGEX_TIMEOUT = 0.1  # 100ms

# Thread-local storage for decision errors (regex timeout side-channel)
_decision_ctx = threading.local()


def get_decision_errors() -> list[str]:
    """Return accumulated decision errors from the current thread."""
    return getattr(_decision_ctx, "errors", [])


def clear_decision_errors() -> None:
    """Clear decision errors for current thread."""
    _decision_ctx.errors = []


def _add_error(error: str) -> None:
    if not hasattr(_decision_ctx, "errors"):
        _decision_ctx.errors = []
    _decision_ctx.errors.append(error)


def _match_regex(pattern: str, text: str, policy_id: str | None = None) -> bool:
    """Match regex with 100ms timeout. On timeout: return False and tag error."""
    try:
        compiled = regex.compile(pattern, regex.VERSION1)
        return compiled.search(text, timeout=_REGEX_TIMEOUT) is not None
    except (TimeoutError, Exception) as e:
        if "timeout" in str(e).lower() or isinstance(e, TimeoutError):
            _add_error(f"regex_timeout:{policy_id or 'unknown'}")
            return False
        return False


def _get_arg(arguments: dict[str, Any], arg: str) -> tuple[bool, Any]:
    """Retrieve arg value from arguments dict. Returns (found, value)."""
    if arg not in arguments:
        return False, None
    return True, arguments[arg]


def _dot_path_get(obj: Any, path: str) -> tuple[bool, Any]:
    """Walk a dot-separated path in a nested dict. Returns (found, value)."""
    parts = path.split(".")
    current = obj
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def evaluate_condition(cond: ConditionType, context: dict[str, Any]) -> bool:
    """Evaluate a single condition against the request context.

    context shape:
    {
        "tool_call": {"name": str, "arguments": dict, "_meta": dict | None},
        "intent": dict | None,  # extracted intent or None
        "upstream": str,
        "runtime": {"lockdown": bool},
        "policy_id": str | None,  # for error tagging
    }

    Returns True/False. Missing args fail-closed (return False).
    arg="*" iterates all top-level argument values.
    """
    tool_call = context.get("tool_call", {})
    arguments: dict[str, Any] = tool_call.get("arguments", {})
    intent = context.get("intent")
    policy_id = context.get("policy_id")

    if isinstance(cond, ArgEquals):
        if cond.arg == "*":
            return any(v == cond.value for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return bool(val == cond.value)

    if isinstance(cond, ArgGreaterThan):
        if cond.arg == "*":
            for v in arguments.values():
                try:
                    if float(v) > float(cond.value):
                        return True
                except (TypeError, ValueError):
                    pass
            return False
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        try:
            return float(val) > float(cond.value)
        except (TypeError, ValueError):
            return False

    if isinstance(cond, ArgLessThan):
        if cond.arg == "*":
            for v in arguments.values():
                try:
                    if float(v) < float(cond.value):
                        return True
                except (TypeError, ValueError):
                    pass
            return False
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        try:
            return float(val) < float(cond.value)
        except (TypeError, ValueError):
            return False

    if isinstance(cond, ArgMatchesRegex):
        if cond.arg == "*":
            return any(_match_regex(cond.pattern, str(v), policy_id) for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return _match_regex(cond.pattern, str(val), policy_id)

    if isinstance(cond, ArgInSet):
        if cond.arg == "*":
            return any(v in cond.values for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return val in cond.values

    if isinstance(cond, ArgContainsPattern):
        # Alias of arg_matches_regex
        if cond.arg == "*":
            return any(_match_regex(cond.pattern, str(v), policy_id) for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return _match_regex(cond.pattern, str(val), policy_id)

    if isinstance(cond, ArgSizeGreaterThan):
        if cond.arg == "*":
            return any(len(json.dumps(v)) > cond.bytes for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return len(json.dumps(val)) > cond.bytes

    if isinstance(cond, ToolNameIn):
        tool_name = tool_call.get("name", "")
        return tool_name in cond.values

    if isinstance(cond, ActionClassIn):
        tool_name = tool_call.get("name", "")
        tool_verbs = verbs_for(tool_name)
        return bool(tool_verbs & set(cond.values))

    if isinstance(cond, IntentClassIn):
        if intent is None:
            return False
        intent_verbs = set(intent.get("verbs", []))
        return bool(intent_verbs & set(cond.values))

    if isinstance(cond, IntentPurposeMatches):
        if intent is None:
            return False
        purpose = intent.get("purpose")
        if purpose is None:
            return False
        return _match_regex(cond.pattern, str(purpose), policy_id)

    if isinstance(cond, RegionIn):
        if cond.arg == "*":
            return any(any(str(v).startswith(r) for r in cond.regions) for v in arguments.values())
        found, val = _get_arg(arguments, cond.arg)
        if not found:
            return False
        return any(str(val).startswith(r) for r in cond.regions)

    if isinstance(cond, TimeOfDayOutside):
        try:
            tz = ZoneInfo(cond.tz)
        except (ZoneInfoNotFoundError, Exception):
            return False
        now = _now_fn(tz).time().replace(second=0, microsecond=0)
        start_parts = cond.start.split(":")
        end_parts = cond.end.split(":")
        start = time(int(start_parts[0]), int(start_parts[1]))
        end = time(int(end_parts[0]), int(end_parts[1]))
        if start <= end:
            # Normal range: outside means before start OR after end
            return not (start <= now <= end)
        # Wraps midnight: inside means >= start OR <= end
        return not (now >= start or now <= end)

    if isinstance(cond, MetaFieldEquals):
        meta = tool_call.get("_meta")
        if meta is None:
            return False
        found, val = _dot_path_get(meta, cond.key)
        if not found:
            return False
        return bool(val == cond.value)

    if isinstance(cond, AnyOf):
        return any(evaluate_condition(c, context) for c in cond.conditions)

    if isinstance(cond, NoneOf):
        return not any(evaluate_condition(c, context) for c in cond.conditions)

    # Unknown condition type — fail-closed
    return False  # type: ignore[unreachable]


def evaluate_conditions(conds: list[ConditionType], context: dict[str, Any]) -> bool:
    """Evaluate all conditions (AND'd). Short-circuit on first False."""
    return all(evaluate_condition(c, context) for c in conds)
