"""Unit tests for the affected_resource_count condition."""

from __future__ import annotations

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import AffectedResourceCount

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(args: dict | None = None) -> dict:
    return {
        "tool_call": {"name": "aws_ec2_TerminateInstances", "arguments": args or {}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_count_greater_than_threshold_blocks():
    """5 instance IDs > threshold 3 → True."""
    cond = AffectedResourceCount(
        condition="affected_resource_count",
        arg="InstanceIds",
        count_threshold=3,
        operator="greater_than",
    )
    ctx = _ctx({"InstanceIds": ["i-1", "i-2", "i-3", "i-4", "i-5"]})
    assert evaluate_condition(cond, ctx) is True


def test_count_under_threshold_allows():
    """2 instance IDs <= threshold 3 → False."""
    cond = AffectedResourceCount(
        condition="affected_resource_count",
        arg="InstanceIds",
        count_threshold=3,
        operator="greater_than",
    )
    ctx = _ctx({"InstanceIds": ["i-1", "i-2"]})
    assert evaluate_condition(cond, ctx) is False


def test_missing_jmespath_key_returns_false():
    """Missing JMESPath key → empty list → count 0 → False for greater_than."""
    cond = AffectedResourceCount(
        condition="affected_resource_count",
        arg="InstanceIds",
        count_threshold=1,
        operator="greater_than",
    )
    ctx = _ctx({"Filters": []})  # no InstanceIds key
    assert evaluate_condition(cond, ctx) is False


def test_operator_less_than():
    """1 ID < threshold 5 → True for less_than."""
    cond = AffectedResourceCount(
        condition="affected_resource_count",
        arg="InstanceIds",
        count_threshold=5,
        operator="less_than",
    )
    ctx = _ctx({"InstanceIds": ["i-1"]})
    assert evaluate_condition(cond, ctx) is True
