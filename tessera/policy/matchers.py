"""Upstream and tool name matching."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

import regex as re

# cli_translator is pure Python (no boto3 dep) — safe to import unconditionally.
try:
    from tessera.integrations.aws.cli_translator import from_call_aws as _from_call_aws
except ImportError:  # pragma: no cover
    _from_call_aws = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CALL_AWS_TOOL_NAME = "call_aws"


def resolve_effective_tool_name(context: dict[str, Any]) -> str:
    """Return the effective tool name for policy matching, caching in context.

    When the inbound ``tool_call.name`` is ``"call_aws"``, attempt to
    reverse-resolve ``args.command`` back to the canonical aws_*_* name via
    ``cli_translator.from_call_aws()``.  The resolved name (or the literal
    ``"call_aws"`` when resolution fails) is stored in
    ``context["_effective_tool_name"]`` so downstream evaluators (e.g.
    ``_evaluate_tool_name_in`` in conditions.py) don't re-resolve on every
    condition check within the same request.

    For any tool other than ``call_aws`` the function is a no-op that just
    returns the literal tool name — no cli_translator code path runs.
    """
    # Return cached value if already resolved this request
    if "_effective_tool_name" in context:
        return str(context["_effective_tool_name"])

    tool_call = context.get("tool_call", {})
    tool_name: str = tool_call.get("name", "")

    if tool_name == _CALL_AWS_TOOL_NAME and _from_call_aws is not None:
        args = tool_call.get("arguments", {})
        resolved = _from_call_aws(args)
        effective = resolved if resolved is not None else tool_name
    else:
        effective = tool_name

    context["_effective_tool_name"] = effective
    return effective


def match_upstream(policy_upstream: str, request_upstream: str) -> bool:
    """Return True if request_upstream matches the policy's upstream spec.

    "*" always matches. Otherwise exact string match (no glob in upstream).
    """
    if policy_upstream == "*":
        return True
    return policy_upstream == request_upstream


def match_tool(
    policy_tool: str | None,
    policy_tool_pattern: str | None,
    tool_name: str,
) -> bool:
    """Return True if tool_name matches.

    policy_tool: glob pattern ("*" = all, "aws_*" = prefix glob) via fnmatch.
    policy_tool_pattern: regex (via `regex` lib, 100ms timeout).
    If both None: True (no restriction).
    If tool is "*": True.
    """
    if policy_tool is None and policy_tool_pattern is None:
        return True

    if policy_tool is not None:
        if policy_tool == "*":
            return True
        return fnmatch.fnmatch(tool_name, policy_tool)

    # policy_tool_pattern is set
    try:
        compiled = re.compile(policy_tool_pattern, re.VERSION1)
        result = compiled.search(tool_name, timeout=0.1)
        return result is not None
    except Exception as exc:  # noqa: BLE001
        # Pattern was already passed through regex_safety.validate_pattern at
        # load time so reaching here is unusual; log at debug for diagnosability
        # without spamming logs in the typical case.
        logger.debug(
            "event=tool_pattern_match_failed pattern=%r tool=%r error=%s",
            policy_tool_pattern,
            tool_name,
            exc,
        )
        return False


def match_tool_dual(
    policy_tool: str | None,
    policy_tool_pattern: str | None,
    tool_name: str,
    effective_tool_name: str,
) -> bool:
    """Return True if EITHER the literal tool_name OR effective_tool_name matches.

    Used by the engine when ``tool_name == "call_aws"`` so that policies
    authored against the canonical name (e.g. ``aws_iam_PassRole``) still
    fire, while policies that explicitly target ``"call_aws"`` also fire.

    When ``tool_name == effective_tool_name`` (no reverse-resolution happened,
    or the call is not ``call_aws``), this degenerates to a single
    ``match_tool()`` call.
    """
    if tool_name == effective_tool_name:
        return match_tool(policy_tool, policy_tool_pattern, tool_name)
    # Try literal name first (cheaper — lets "call_aws" policies match quickly)
    if match_tool(policy_tool, policy_tool_pattern, tool_name):
        return True
    # Try resolved canonical name
    return match_tool(policy_tool, policy_tool_pattern, effective_tool_name)
