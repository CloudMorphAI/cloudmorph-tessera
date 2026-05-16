"""Unit tests for the sts_chain_depth_greater_than condition (v0.5.0)."""

from __future__ import annotations

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import StsChainDepthGreaterThan


def _ctx(
    name: str = "aws_sts_AssumeRole",
    arguments: dict | None = None,
    meta: dict | None = None,
) -> dict:
    return {
        "tool_call": {
            "name": name,
            "arguments": arguments or {},
            "_meta": meta,
        },
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "policy_id": None,
    }


# ── Basic threshold behaviour ─────────────────────────────────────────────────


def test_chain_depth_exceeds_threshold() -> None:
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=2
    )
    ctx = _ctx(meta={"aws_session_chain": ["role-a", "role-b", "role-c"]})
    assert evaluate_condition(cond, ctx) is True


def test_chain_depth_equals_threshold_returns_false() -> None:
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=3
    )
    ctx = _ctx(meta={"aws_session_chain": ["role-a", "role-b", "role-c"]})
    assert evaluate_condition(cond, ctx) is False


def test_chain_depth_below_threshold_returns_false() -> None:
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=5
    )
    ctx = _ctx(meta={"aws_session_chain": ["role-a", "role-b"]})
    assert evaluate_condition(cond, ctx) is False


def test_single_hop_chain_at_threshold_1() -> None:
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=1
    )
    ctx = _ctx(meta={"aws_session_chain": ["role-a", "role-b"]})
    assert evaluate_condition(cond, ctx) is True


# ── Fail-closed (don't-block) when meta absent ────────────────────────────────


def test_meta_none_returns_false() -> None:
    """Absent _meta → fail-closed don't-block (False)."""
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=0
    )
    ctx = _ctx(meta=None)
    assert evaluate_condition(cond, ctx) is False


def test_meta_missing_chain_key_returns_false() -> None:
    """_meta present but no aws_session_chain key → fail-closed don't-block."""
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=0
    )
    ctx = _ctx(meta={"other_key": "value"})
    assert evaluate_condition(cond, ctx) is False


def test_meta_chain_not_a_list_returns_false() -> None:
    """aws_session_chain is not a list → fail-closed don't-block."""
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=0
    )
    ctx = _ctx(meta={"aws_session_chain": "arn:aws:iam::123:role/foo"})
    assert evaluate_condition(cond, ctx) is False


def test_empty_chain_does_not_exceed_zero_threshold() -> None:
    """Empty list has length 0 — not > 0."""
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=0
    )
    ctx = _ctx(meta={"aws_session_chain": []})
    assert evaluate_condition(cond, ctx) is False


def test_empty_chain_exceeds_negative_threshold() -> None:
    """Threshold of -1: any non-negative chain length exceeds it.

    Pathological but verifies the strict greater-than comparison.
    """
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=-1
    )
    ctx = _ctx(meta={"aws_session_chain": []})
    assert evaluate_condition(cond, ctx) is True


# ── No _meta at tool_call level ───────────────────────────────────────────────


def test_tool_call_no_meta_key_returns_false() -> None:
    """Tool call without _meta entirely → fail-closed."""
    cond = StsChainDepthGreaterThan(
        condition="sts_chain_depth_greater_than", threshold=2
    )
    ctx = {
        "tool_call": {"name": "aws_sts_AssumeRole", "arguments": {}},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "policy_id": None,
    }
    assert evaluate_condition(cond, ctx) is False
