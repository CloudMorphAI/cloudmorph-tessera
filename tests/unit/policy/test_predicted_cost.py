"""Unit tests for the predicted_cost condition."""

from __future__ import annotations

import pytest

from tessera.cost.types import CostResult
from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import PredictedCost

# ── Helpers ───────────────────────────────────────────────────────────────────


def _cr(price_usd: float, band: str = "high", source: str = "price_table") -> CostResult:
    """Build a CostResult for test cost_cache entries."""
    return CostResult(
        price_usd=price_usd,
        unit="hour",
        confidence_band=band,
        source=source,  # type: ignore[arg-type]
        operation="aws_ec2_RunInstances",
    )


def _ctx(
    tool_name: str = "aws_ec2_RunInstances",
    args: dict | None = None,
    cost_cache: dict | None = None,
    cost_backend=None,
    aws_mapping=None,
) -> dict:
    return {
        "tool_call": {"name": tool_name, "arguments": args or {"InstanceType": "t3.micro", "region": "us-east-1"}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "cost_cache": cost_cache if cost_cache is not None else {},
        "cost_backend": cost_backend,
        "aws_mapping": aws_mapping,
        "scope": "test",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_high_band_over_threshold_blocks():
    """predicted_cost blocks when cost > threshold (band=high, operator=greater_than)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.05, band="high")
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": _cr(0.10)})  # 0.10 * 1.0 = 0.10 > 0.05
    assert evaluate_condition(cond, ctx) is True


def test_high_band_under_threshold_does_not_block():
    """predicted_cost allows when cost < threshold (band=high)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.20, band="high")
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": _cr(0.10)})  # 0.10 < 0.20
    assert evaluate_condition(cond, ctx) is False


def test_ceiling_band_uncertainty_triggers_block():
    """predicted_cost with band=ceiling applies 3x multiplier."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.25, band="ceiling")
    # 0.10 * 3.0 = 0.30 > 0.25
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": _cr(0.10)})
    assert evaluate_condition(cond, ctx) is True


def test_missing_cache_entry_fails_closed_allow():
    """predicted_cost fails-closed (False) when tool_name not in cost_cache."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    # Empty cost_cache — no entry for the tool
    ctx = _ctx(cost_cache={})
    assert evaluate_condition(cond, ctx) is False


def test_miss_source_fails_closed_allow():
    """predicted_cost fails-closed when source == 'miss'."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": CostResult.miss("aws_ec2_RunInstances")})
    assert evaluate_condition(cond, ctx) is False


def test_cost_backend_none_fails_closed_allow():
    """predicted_cost fails-closed when cost_cache is empty (no backend configured)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    ctx = _ctx(cost_backend=None, aws_mapping=None, cost_cache={})
    assert evaluate_condition(cond, ctx) is False


def test_operator_less_than():
    """predicted_cost with operator=less_than: cost 0.01 < 0.05 → True."""
    cond = PredictedCost(
        condition="predicted_cost", usd_threshold=0.05, band="high", operator="less_than"
    )
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": _cr(0.01)})
    assert evaluate_condition(cond, ctx) is True


def test_operator_between_within_range():
    """predicted_cost with operator=between matches when cost is within range."""
    cond = PredictedCost(
        condition="predicted_cost",
        usd_threshold=0.05,
        band="high",
        operator="between",
        usd_threshold_upper=0.20,
    )
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": _cr(0.10)})  # 0.05 <= 0.10 <= 0.20
    assert evaluate_condition(cond, ctx) is True


def test_multiple_regions_handled():
    """predicted_cost reads region from args correctly (us-west-2)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.05, band="high")
    ctx = _ctx(
        args={"InstanceType": "t3.micro", "region": "us-west-2"},
        cost_cache={"aws_ec2_RunInstances": _cr(0.10)},
    )
    assert evaluate_condition(cond, ctx) is True


def test_assert_raises_when_cost_cache_absent():
    """_evaluate_predicted_cost asserts if cost_cache key is missing entirely."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    ctx = {
        "tool_call": {"name": "aws_ec2_RunInstances", "arguments": {}, "_meta": None},
        "upstream": "aws",
        # Intentionally no "cost_cache" key
    }
    with pytest.raises(AssertionError, match="cost_cache"):
        evaluate_condition(cond, ctx)


def test_infracost_live_source_is_respected():
    """CostResult with source='infracost_live' is evaluated against threshold."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=5.0, band="high")
    live_result = CostResult(
        price_usd=10.0,
        unit="hour",
        confidence_band="high",
        source="infracost_live",
        operation="aws_ec2_RunInstances",
    )
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": live_result})
    assert evaluate_condition(cond, ctx) is True
