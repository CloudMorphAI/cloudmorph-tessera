"""Unit tests for the predicted_cost condition."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import PredictedCost
from tessera.cost.infracost import SkuResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_mapping(usd_per_unit: float | None = 0.10):
    """Return a mock aws_mapping that returns an InfracostQuery with fixed cost."""
    from tessera.cost.aws_mapping import InfracostQuery
    mapping = MagicMock()
    if usd_per_unit is not None:
        query = InfracostQuery(
            service="Compute Instance",
            region="us-east-1",
            attributes={"instanceType": "t3.micro"},
            confidence_band="high",
        )
        mapping.map_request.return_value = query
    else:
        mapping.map_request.return_value = None
    return mapping


def _make_backend(usd_per_unit: float | None = 0.10):
    """Return a mock InfracostClient that returns a fixed SkuResult."""
    backend = MagicMock()
    if usd_per_unit is not None:
        sku = SkuResult(usd_per_unit=usd_per_unit, unit="Hrs")
        backend.query_sku = AsyncMock(return_value=sku)
    else:
        backend.query_sku = AsyncMock(return_value=None)
    return backend


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
        "cost_cache": cost_cache,
        "cost_backend": cost_backend,
        "aws_mapping": aws_mapping,
        "scope": "test",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_high_band_over_threshold_blocks():
    """predicted_cost blocks when cost > threshold (band=high, operator=greater_than)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.05, band="high")
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.10})  # 0.10 * 1.0 = 0.10 > 0.05
    assert evaluate_condition(cond, ctx) is True


def test_high_band_under_threshold_does_not_block():
    """predicted_cost allows when cost < threshold (band=high)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.20, band="high")
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.10})  # 0.10 < 0.20
    assert evaluate_condition(cond, ctx) is False


def test_ceiling_band_uncertainty_triggers_block():
    """predicted_cost with band=ceiling applies 3x multiplier."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.25, band="ceiling")
    # 0.10 * 3.0 = 0.30 > 0.25
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.10})
    assert evaluate_condition(cond, ctx) is True


def test_missing_mapping_fails_closed_allow():
    """predicted_cost fails-closed (False) when aws_mapping returns None."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    mapping = _make_mapping(usd_per_unit=None)  # map_request returns None
    backend = _make_backend(0.10)
    ctx = _ctx(cost_backend=backend, aws_mapping=mapping)
    # No cost_cache → falls back to backend path; mapping returns None → fail-closed
    assert evaluate_condition(cond, ctx) is False


def test_cost_backend_none_fails_closed_allow():
    """predicted_cost fails-closed when cost_backend is missing from context."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.01, band="high")
    ctx = _ctx(cost_backend=None, aws_mapping=None)
    assert evaluate_condition(cond, ctx) is False


def test_operator_less_than():
    """predicted_cost with operator=less_than: cost 0.01 < 0.05 → True."""
    cond = PredictedCost(
        condition="predicted_cost", usd_threshold=0.05, band="high", operator="less_than"
    )
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.01})
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
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.10})  # 0.05 <= 0.10 <= 0.20
    assert evaluate_condition(cond, ctx) is True


def test_multiple_regions_handled():
    """predicted_cost reads region from args correctly (us-west-2)."""
    cond = PredictedCost(condition="predicted_cost", usd_threshold=0.05, band="high")
    ctx = _ctx(
        args={"InstanceType": "t3.micro", "region": "us-west-2"},
        cost_cache={"aws_ec2_RunInstances": 0.10},
    )
    assert evaluate_condition(cond, ctx) is True
