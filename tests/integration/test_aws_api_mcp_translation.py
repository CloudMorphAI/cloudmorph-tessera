"""Integration test — call_aws round-trip with bundled aws-mcp-passrole-guard.

Verifies that the reverse-resolver in matchers.py + engine.py allows the
bundled aws-mcp-passrole-guard policy (authored against canonical name
aws_iam_PassRole via tool_pattern) to fire when the inbound tools/call
arrives as {"name": "call_aws", "arguments": {"command": "aws iam pass-role ..."}}
from the official awslabs/mcp/aws-api-mcp-server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action, MatchSpec, Policy, ToolNameIn

# Path to tessera's bundled default policies
_POLICIES_DEFAULT = Path(__file__).parent.parent.parent / "tessera" / "policies_default"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_only(policy_id: str) -> PolicyEngine:
    """Load only the named bundled policy into an engine with default=allow."""
    loader = FilesystemPolicyLoader(_POLICIES_DEFAULT)
    all_policies = loader.load_all("default")
    matching = [p for p in all_policies if p.id == policy_id]
    assert matching, f"Bundled policy {policy_id!r} not found in {_POLICIES_DEFAULT}"
    return PolicyEngine(matching, default_action=Action.allow)


def _call_aws_ctx(command: str, extra_args: dict | None = None) -> dict:
    """Build a minimal context for an inbound call_aws tools/call."""
    args: dict = {"command": command}
    if extra_args:
        args.update(extra_args)
    return {
        "tool_call": {
            "name": "call_aws",
            "arguments": args,
            "_meta": None,
        },
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
    }


# ---------------------------------------------------------------------------
# Round-trip: bundled aws-mcp-passrole-guard policy
#
# The policy uses tool_pattern="^aws_iam_(PassRole|pass_role)$" at match scope.
# With the reverse-resolver, call_aws + "aws iam pass-role ..." is matched
# because effective_tool_name resolves to "aws_iam_PassRole".
#
# Condition firing: the passrole guard's `when` clause checks RoleArn via
# arg_matches_regex OR fires blast_radius fail-closed (no backend). Since
# the raw call_aws args don't have a top-level RoleArn key, we pass RoleArn
# as a structured arg alongside `command` — this mirrors the contract where
# both the CLI string (for cli_translator) and the structured args (for
# conditions) are present in the arguments dict.
# ---------------------------------------------------------------------------


def test_passrole_guard_fires_for_call_aws_admin_role() -> None:
    """Bundled passrole guard requires approval when call_aws resolves to aws_iam_PassRole."""
    engine = _load_only("aws-mcp-passrole-guard")

    ctx = _call_aws_ctx(
        command=(
            "aws iam pass-role"
            " --role-arn arn:aws:iam::123456789012:role/AdministratorAccess"
            " --role-session-name pwned"
        ),
        extra_args={
            # Structured args let the arg_matches_regex condition fire.
            # This mirrors the dual-format contract: cli string for translation,
            # structured keys for policy conditions.
            "RoleArn": "arn:aws:iam::123456789012:role/AdministratorAccess",
        },
    )

    decision = engine.evaluate(ctx)

    assert decision.action == Action.require_approval, (
        f"Expected require_approval from passrole guard, got {decision.action} "
        f"(policy_id={decision.policy_id!r}, reason={decision.reason!r})"
    )
    assert decision.policy_id == "aws-mcp-passrole-guard"

    # Effective tool name must be cached in context from the reverse-resolver
    assert ctx.get("_effective_tool_name") == "aws_iam_PassRole", (
        f"Expected _effective_tool_name='aws_iam_PassRole', got {ctx.get('_effective_tool_name')!r}"
    )
    # TODO(SA-3D): once audit-event fields are wired, assert:
    #   audit_event["effective_tool_name"] == "aws_iam_PassRole"
    #   audit_event["canonical_tool_name"] == "call_aws"


def test_passrole_guard_does_not_fire_for_unrelated_call_aws() -> None:
    """Bundled passrole guard does not trigger for unrecognised call_aws commands."""
    engine = _load_only("aws-mcp-passrole-guard")

    ctx = _call_aws_ctx(command="aws s3 list-buckets")
    decision = engine.evaluate(ctx)

    # list-buckets resolves to aws_s3_ListBuckets — doesn't match passrole guard
    assert decision.action == Action.allow, (
        f"Expected allow for list-buckets, got {decision.action} (policy_id={decision.policy_id!r})"
    )


def test_passrole_guard_fires_for_canonical_name_directly() -> None:
    """Passrole guard still fires when called with canonical name (regression)."""
    engine = _load_only("aws-mcp-passrole-guard")

    ctx = {
        "tool_call": {
            "name": "aws_iam_PassRole",
            "arguments": {
                "RoleArn": "arn:aws:iam::123456789012:role/AdministratorAccess",
            },
            "_meta": None,
        },
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
    }

    decision = engine.evaluate(ctx)
    assert decision.action == Action.require_approval
    assert decision.policy_id == "aws-mcp-passrole-guard"


# ---------------------------------------------------------------------------
# Round-trip: tool_name_in condition against call_aws
#
# Separate from the bundled policy — tests the condition-level reverse-resolver
# end-to-end: engine resolves effective name, caches in context, condition reads it.
# ---------------------------------------------------------------------------


def _inline_policy(policy_id: str, tool_names: list[str], action: Action = Action.block) -> Policy:
    return Policy(
        id=policy_id,
        name=policy_id,
        match=MatchSpec(upstream="*"),
        when=[ToolNameIn(condition="tool_name_in", values=tool_names)],
        action=action,
    )


def test_tool_name_in_condition_fires_via_call_aws_resolver() -> None:
    """tool_name_in condition fires for call_aws when resolver returns matching canonical."""
    engine = PolicyEngine(
        [_inline_policy("p-passrole-in", ["aws_iam_PassRole"])],
        default_action=Action.allow,
    )
    ctx = _call_aws_ctx(
        command=(
            "aws iam pass-role"
            " --role-arn arn:aws:iam::123456789012:role/AdministratorAccess"
            " --role-session-name pwned"
        )
    )
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "p-passrole-in"
    assert ctx["_effective_tool_name"] == "aws_iam_PassRole"


def test_tool_name_in_literal_call_aws_still_works() -> None:
    """A policy targeting call_aws literally still fires via condition."""
    engine = PolicyEngine(
        [_inline_policy("p-catch-all", ["call_aws"])],
        default_action=Action.allow,
    )
    ctx = _call_aws_ctx(command="aws iam pass-role --role-arn arn:aws:iam::123:role/Admin")
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "p-catch-all"
