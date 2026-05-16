"""Integration tests for PolicyEngine v0.2.0 — new conditions, nesting, perf baseline."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import (
    Action,
    AffectedResourceCount,
    AnyOf,
    BlastRadius,
    CumulativeSpendToday,
    DataVolume,
    NoneOf,
    Policy,
    PredictedCost,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _policy(
    id: str,
    conditions: list,
    action: Action = Action.block,
    priority: int = 0,
) -> Policy:
    return Policy(
        id=id,
        name=id,
        when=conditions,
        action=action,
        priority=priority,
    )


def _make_engine(policies: list[Policy], default: Action = Action.allow) -> PolicyEngine:
    return PolicyEngine(policies, default_action=default)


def _ctx(
    tool_name: str = "aws_ec2_RunInstances",
    args: dict | None = None,
    state_backend=None,
    blast_backend=None,
    cost_cache: dict | None = None,
    scope: str = "test",
) -> dict:
    # v0.3.0 — wrap raw-float cost_cache entries into CostResult so the
    # _evaluate_predicted_cost contract (CostResult-only) holds.
    cc = cost_cache or {}
    if cc:
        from tessera.cost.types import CostResult

        for tool, value in list(cc.items()):
            if isinstance(value, CostResult):
                continue
            if isinstance(value, (int, float)):
                cc[tool] = CostResult(
                    price_usd=float(value),
                    unit="hour",
                    confidence_band="high",
                    source="price_table",
                    operation=tool,
                )
    return {
        "tool_call": {"name": tool_name, "arguments": args or {}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "scope": scope,
        "state_backend": state_backend,
        "blast_radius_backend": blast_backend,
        "cost_backend": None,
        "cost_cache": cc,
        "aws_mapping": None,
    }


# ── New condition integration tests ───────────────────────────────────────────


def test_predicted_cost_inside_any_of():
    """PredictedCost inside AnyOf correctly triggers when cost is high."""
    high_cost = PredictedCost(
        condition="predicted_cost", usd_threshold=0.05, band="high", operator="greater_than"
    )
    big_args = AffectedResourceCount(
        condition="affected_resource_count",
        arg="InstanceIds",
        count_threshold=10,
        operator="greater_than",
    )
    combined = AnyOf(condition="any_of", conditions=[high_cost, big_args])

    engine = _make_engine([_policy("p-any", [combined], Action.block)])
    # Only high_cost fires (10 instances not in context)
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.10})
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "p-any"


def test_none_of_nesting_with_new_conditions():
    """NoneOf wrapping PredictedCost + BlastRadius: fires when neither is true."""
    high_cost = PredictedCost(
        condition="predicted_cost", usd_threshold=1.00, band="high"
    )
    high_blast = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=100,
        operator="greater_than",
    )
    none_of = NoneOf(condition="none_of", conditions=[high_cost, high_blast])

    # cost is low AND blast_radius_backend is None (fail-closed=True) → blast is True
    # NoneOf([False, True]) → not(False or True) = False → policy does NOT match
    blast_backend = None  # missing → blast evaluator returns True
    engine = _make_engine([_policy("p-none", [none_of], Action.block)])
    ctx = _ctx(cost_cache={"aws_ec2_RunInstances": 0.001}, blast_backend=blast_backend)
    decision = engine.evaluate(ctx)
    # none_of → False → no policy match → default allow
    assert decision.action == Action.allow


def test_priority_sorting_respected():
    """Higher-priority policy fires first."""
    low = _policy("low-priority", [
        AffectedResourceCount(
            condition="affected_resource_count", arg="InstanceIds", count_threshold=1, operator="greater_than"
        )
    ], Action.allow, priority=0)
    high = _policy("high-priority", [
        AffectedResourceCount(
            condition="affected_resource_count", arg="InstanceIds", count_threshold=1, operator="greater_than"
        )
    ], Action.block, priority=100)

    # PolicyEngine trusts the loader's sort order (first-match-wins, no internal sort).
    # FilesystemPolicyLoader sorts by (-priority, id). Mirror that order here.
    engine = _make_engine(sorted([low, high], key=lambda p: (-p.priority, p.id)))
    ctx = _ctx(args={"InstanceIds": ["i-1", "i-2"]})
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block
    assert decision.policy_id == "high-priority"


def test_cumulative_spend_condition_integration():
    """CumulativeSpendToday integrates correctly with engine."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=50.00,
        operator="greater_than",
    )
    engine = _make_engine([_policy("spend-limit", [cond], Action.block)])

    state = MagicMock()
    state.get_today_spend.return_value = 75.00

    ctx = _ctx(state_backend=state)
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block


def test_data_volume_condition_integration():
    """DataVolume static_arg_size integrates with engine."""
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=100,
        operator="greater_than",
        estimator="static_arg_size",
    )
    engine = _make_engine([_policy("data-vol", [cond], Action.block)])
    large_args = {"payload": "x" * 1000}
    ctx = _ctx(args=large_args)
    decision = engine.evaluate(ctx)
    assert decision.action == Action.block


def test_engine_evaluation_baseline():
    """Microbench: 50 policies × 100 evaluations complete in ≤ 100ms."""
    conditions = [
        AffectedResourceCount(
            condition="affected_resource_count",
            arg="InstanceIds",
            count_threshold=5,
            operator="greater_than",
        )
    ]
    policies = [
        _policy(f"p-{i:03d}", conditions, Action.block, priority=i)
        for i in range(50)
    ]
    engine = _make_engine(policies)
    ctx = _ctx(args={"InstanceIds": ["i-1", "i-2"]})

    start = time.monotonic()
    for _ in range(100):
        engine.evaluate(ctx)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 100, f"Engine too slow: {elapsed_ms:.1f}ms for 100 evaluations"
