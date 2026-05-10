"""Upstream and tool name matching."""

from __future__ import annotations

import fnmatch

import regex as re  # type: ignore[import-untyped]


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
    except Exception:
        return False
