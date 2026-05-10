"""Unit tests for tessera.policy.engine."""

from __future__ import annotations

from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import Action, MatchSpec, Policy

# ── Helpers ───────────────────────────────────────────────────────────────────


def _policy(
    policy_id: str,
    action: Action = Action.allow,
    priority: int = 0,
    upstream: str = "*",
    tool: str | None = None,
    require_intent: bool = False,
    when: list | None = None,
) -> Policy:
    match_kwargs: dict = {"upstream": upstream, "require_intent": require_intent}
    if tool is not None:
        match_kwargs["tool"] = tool
    return Policy(
        id=policy_id,
        name=f"Policy {policy_id}",
        action=action,
        priority=priority,
        match=MatchSpec(**match_kwargs),
        when=when or [],
    )


def _ctx(
    *,
    name: str = "aws.s3.list_buckets",
    arguments: dict | None = None,
    upstream: str = "aws",
    lockdown: bool = False,
    intent: dict | None = None,
) -> dict:
    return {
        "tool_call": {
            "name": name,
            "arguments": arguments or {},
            "_meta": None,
        },
        "intent": intent,
        "upstream": upstream,
        "runtime": {"lockdown": lockdown},
    }


# ── Lockdown ──────────────────────────────────────────────────────────────────


def test_lockdown_blocks_all() -> None:
    """When runtime.lockdown is True, engine blocks before evaluating policies."""
    engine = PolicyEngine(
        policies=[_policy("allow-all", action=Action.allow)],
        default_action=Action.allow,
    )
    decision = engine.evaluate(_ctx(lockdown=True))
    assert decision.action == Action.block
    assert decision.reason == "lockdown_active"
    assert decision.policy_id is None


# ── First-match-wins ──────────────────────────────────────────────────────────


def test_first_match_wins() -> None:
    """Higher-priority policy is evaluated first and wins."""
    high = _policy("high-priority-block", action=Action.block, priority=10)
    low = _policy("low-priority-allow", action=Action.allow, priority=1)
    # policies list already sorted by caller (descending priority)
    engine = PolicyEngine(policies=[high, low], default_action=Action.allow)
    decision = engine.evaluate(_ctx())
    assert decision.action == Action.block
    assert decision.policy_id == "high-priority-block"


# ── Default action ────────────────────────────────────────────────────────────


def test_default_action_when_no_match() -> None:
    """No policies → default action returned."""
    engine = PolicyEngine(policies=[], default_action=Action.block)
    decision = engine.evaluate(_ctx())
    assert decision.action == Action.block
    assert decision.reason == "default"
    assert decision.policy_id is None


def test_default_action_allow_when_no_match() -> None:
    engine = PolicyEngine(policies=[], default_action=Action.allow)
    decision = engine.evaluate(_ctx())
    assert decision.action == Action.allow


# ── Allow / Block matched ─────────────────────────────────────────────────────


def test_allow_policy_matched() -> None:
    engine = PolicyEngine(
        policies=[_policy("allow-reads", action=Action.allow)],
        default_action=Action.block,
    )
    decision = engine.evaluate(_ctx())
    assert decision.action == Action.allow
    assert decision.policy_id == "allow-reads"


def test_block_policy_matched() -> None:
    engine = PolicyEngine(
        policies=[_policy("block-all", action=Action.block)],
        default_action=Action.allow,
    )
    decision = engine.evaluate(_ctx())
    assert decision.action == Action.block
    assert decision.policy_id == "block-all"


# ── require_intent ────────────────────────────────────────────────────────────


def test_require_intent_skipped_for_intent_blind_agent() -> None:
    """Policy with require_intent=True is skipped when intent is None."""
    intent_policy = _policy("intent-required", action=Action.block, require_intent=True)
    engine = PolicyEngine(
        policies=[intent_policy],
        default_action=Action.allow,
    )
    decision = engine.evaluate(_ctx(intent=None))
    # intent policy skipped → falls through to default allow
    assert decision.action == Action.allow
    assert decision.reason == "default"


def test_require_intent_evaluated_with_intent() -> None:
    """Policy with require_intent=True is NOT skipped when intent is present."""
    intent_policy = _policy("intent-required", action=Action.block, require_intent=True)
    engine = PolicyEngine(
        policies=[intent_policy],
        default_action=Action.allow,
    )
    decision = engine.evaluate(_ctx(intent={"verbs": ["read.list"], "purpose": "test"}))
    assert decision.action == Action.block
    assert decision.policy_id == "intent-required"


# ── Mode-agnostic ─────────────────────────────────────────────────────────────


def test_engine_mode_agnostic() -> None:
    """Engine returns the same Decision regardless of mode field in context."""
    engine = PolicyEngine(
        policies=[_policy("block-all", action=Action.block)],
        default_action=Action.allow,
    )
    ctx_enforcement = {**_ctx(), "mode": "enforcement"}
    ctx_log_only = {**_ctx(), "mode": "log_only"}
    ctx_observation = {**_ctx(), "mode": "observation"}

    d1 = engine.evaluate(ctx_enforcement)
    d2 = engine.evaluate(ctx_log_only)
    d3 = engine.evaluate(ctx_observation)

    assert d1.action == d2.action == d3.action == Action.block
    assert d1.policy_id == d2.policy_id == d3.policy_id == "block-all"
