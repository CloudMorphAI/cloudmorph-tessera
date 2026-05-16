"""Unit tests — call_aws reverse-resolver in matchers + tool_name_in condition.

Tests verify that policies authored against canonical aws_*_* names (e.g.
aws_iam_PassRole) still fire when an inbound tools/call arrives with
tool_name="call_aws" and args.command resolvable to that canonical name.
"""

from __future__ import annotations

from unittest.mock import patch

from tessera.policy.engine import PolicyEngine
from tessera.policy.matchers import resolve_effective_tool_name
from tessera.policy.schema import (
    Action,
    MatchSpec,
    Policy,
    ToolNameIn,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy_with_tool_name_in(
    policy_id: str,
    tool_names: list[str],
    action: Action = Action.block,
) -> Policy:
    """Build a policy that fires when tool_name_in matches."""
    return Policy(
        id=policy_id,
        name=policy_id,
        match=MatchSpec(upstream="*"),
        when=[ToolNameIn(condition="tool_name_in", values=tool_names)],
        action=action,
    )


def _ctx(
    tool_name: str,
    arguments: dict | None = None,
    upstream: str = "aws",
) -> dict:
    return {
        "tool_call": {
            "name": tool_name,
            "arguments": arguments or {},
            "_meta": None,
        },
        "intent": None,
        "upstream": upstream,
        "runtime": {"lockdown": False},
    }


def _engine(policies: list[Policy], default: Action = Action.allow) -> PolicyEngine:
    return PolicyEngine(policies, default_action=default)


# ---------------------------------------------------------------------------
# Test 1 — Direct canonical match (regression: still works without call_aws)
# ---------------------------------------------------------------------------


def test_direct_canonical_match() -> None:
    """Policy tool_name_in: [aws_iam_PassRole]; incoming name=aws_iam_PassRole → matches."""
    engine = _engine([_policy_with_tool_name_in("p-passrole", ["aws_iam_PassRole"])])
    ctx = _ctx("aws_iam_PassRole", {"RoleArn": "arn:aws:iam::123:role/Admin"})
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "p-passrole"


# ---------------------------------------------------------------------------
# Test 2 — call_aws reverse-resolved match
# ---------------------------------------------------------------------------


def test_call_aws_reverse_resolved_match() -> None:
    """Policy tool_name_in: [aws_iam_PassRole]; incoming call_aws with pass-role command → MATCHES."""
    engine = _engine([_policy_with_tool_name_in("p-passrole", ["aws_iam_PassRole"])])
    ctx = _ctx(
        "call_aws",
        {
            "command": (
                "aws iam pass-role --role-arn arn:aws:iam::123456789012:role/AdministratorAccess"
                " --role-session-name pwned"
            )
        },
    )
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block, (
        f"Expected block via reverse-resolver, got {decision.action} "
        f"(policy_id={decision.policy_id}, reason={decision.reason!r})"
    )
    assert decision.policy_id == "p-passrole"


# ---------------------------------------------------------------------------
# Test 3 — call_aws literal match (explicit policy targeting call_aws itself)
# ---------------------------------------------------------------------------


def test_call_aws_literal_match() -> None:
    """Policy tool_name_in: [call_aws]; incoming call_aws → matches on literal name."""
    engine = _engine([_policy_with_tool_name_in("p-call-aws-literal", ["call_aws"])])
    ctx = _ctx("call_aws", {"command": "aws iam pass-role --role-arn arn:aws:iam::123:role/Admin"})
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "p-call-aws-literal"


# ---------------------------------------------------------------------------
# Test 4 — call_aws unknown command → does NOT match canonical policy
# ---------------------------------------------------------------------------


def test_call_aws_unknown_command_no_match() -> None:
    """Policy targets aws_iam_PassRole; command=aws frobnicate → does NOT match."""
    engine = _engine([_policy_with_tool_name_in("p-passrole", ["aws_iam_PassRole"])])
    ctx = _ctx("call_aws", {"command": "aws frobnicate --some-flag value"})
    decision = engine.evaluate(ctx)
    # Unrecognised command → reverse-resolver returns None → no match → default allow
    assert decision.action == Action.allow
    assert decision.policy_id is None


# ---------------------------------------------------------------------------
# Test 5 — No call_aws overhead on non-AWS tools
# ---------------------------------------------------------------------------


def test_no_cli_translator_call_for_non_aws_tool() -> None:
    """github_create_issue tool → cli_translator code path must NOT run."""
    engine = _engine([_policy_with_tool_name_in("p-github", ["github_create_issue"])])
    ctx = _ctx("github_create_issue", {"title": "test"}, upstream="github")

    # Patch from_call_aws to ensure it is never called for non-call_aws tools
    with patch(
        "tessera.policy.matchers._from_call_aws", side_effect=AssertionError("should not be called")
    ):
        decision = engine.evaluate(ctx)

    assert decision.action == Action.block
    assert decision.policy_id == "p-github"


# ---------------------------------------------------------------------------
# Test 6 — resolve_effective_tool_name caches in context
# ---------------------------------------------------------------------------


def test_resolve_effective_tool_name_caches() -> None:
    """Calling resolve_effective_tool_name twice on the same context returns the cached value."""
    ctx = _ctx("call_aws", {"command": "aws iam pass-role --role-arn arn:aws:iam::123:role/Admin"})

    name1 = resolve_effective_tool_name(ctx)
    assert name1 == "aws_iam_PassRole"
    assert ctx["_effective_tool_name"] == "aws_iam_PassRole"

    # Second call must return cached value (not re-invoke cli_translator)
    with patch(
        "tessera.policy.matchers._from_call_aws",
        side_effect=AssertionError("should use cache"),
    ):
        name2 = resolve_effective_tool_name(ctx)

    assert name2 == "aws_iam_PassRole"


# ---------------------------------------------------------------------------
# Test 7 — resolve_effective_tool_name non-call_aws tool (no-op)
# ---------------------------------------------------------------------------


def test_resolve_effective_tool_name_passthrough_non_aws() -> None:
    """For a non-call_aws tool, effective name equals the literal tool name."""
    ctx = _ctx("github_create_issue", {}, upstream="github")
    with patch(
        "tessera.policy.matchers._from_call_aws",
        side_effect=AssertionError("should not be called"),
    ):
        name = resolve_effective_tool_name(ctx)
    assert name == "github_create_issue"
    assert ctx["_effective_tool_name"] == "github_create_issue"
