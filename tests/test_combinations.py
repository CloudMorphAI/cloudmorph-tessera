"""Unit + integration + concurrency tests for v0.6.0 CombinationTracker.

Covers:
- CombinationTracker basic record/get/expire
- Aggregate cost computation
- Chain expiry after window_seconds
- Integration with policy conditions
- Concurrency: 100 agents firing chains concurrently
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any

import pytest

from tessera.cost.combinations import (
    ActiveChain,
    CombinationDef,
    CombinationTracker,
    TriggerOp,
    get_global_tracker,
    set_global_tracker,
)
from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import (
    CombinationAggregateCostUsdGt,
    CombinationIdMatches,
    CombinationOpsCountGt,
    CombinationWindowSecondsLt,
)


def make_combo(cid: str, ops: list[str], window: int = 3600) -> CombinationDef:
    return CombinationDef(
        combination_id=cid,
        name=cid,
        description="test",
        cloud="aws",
        trigger_ops=[TriggerOp(tool_name=op, role="op_%d" % i) for i, op in enumerate(ops)],
        window_seconds=window,
        cost_runaway_severity="high",
        blast_radius_severity="medium",
        ops_count=len(ops),
    )


# ── Basic mechanics ──────────────────────────────────────────────────────────


def test_tracker_records_op_starts_chain():
    defn = make_combo("c1", ["op_a", "op_b"])
    t = CombinationTracker(combinations=[defn])
    chains = t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=1.0)
    assert len(chains) == 1
    assert chains[0].combination_id == "c1"
    assert chains[0].aggregate_cost_usd == 1.0
    assert chains[0].ops_count() == 1


def test_tracker_accumulates_cost_across_ops():
    defn = make_combo("c1", ["op_a", "op_b"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=1.0)
    t.record_op("tenant1", "scope1", "op_b", per_op_cost_usd=2.5)
    cost = t.aggregate_cost_usd("tenant1", "scope1", "c1")
    assert cost == 3.5
    assert t.ops_count("tenant1", "scope1", "c1") == 2


def test_tracker_isolates_tenants():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=10.0)
    t.record_op("tenant2", "scope1", "op_a", per_op_cost_usd=20.0)
    assert t.aggregate_cost_usd("tenant1", "scope1", "c1") == 10.0
    assert t.aggregate_cost_usd("tenant2", "scope1", "c1") == 20.0


def test_tracker_unknown_op_returns_no_chains():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    chains = t.record_op("tenant1", "scope1", "unknown_op", per_op_cost_usd=1.0)
    assert chains == []


def test_chain_expires_after_window():
    fake_now = [1000.0]

    def now():
        return fake_now[0]

    defn = make_combo("c1", ["op_a", "op_b"], window=60)
    t = CombinationTracker(combinations=[defn], time_fn=now)
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=1.0)
    # Within window
    assert t.get_active_chain("tenant1", "scope1", "c1") is not None
    # Past window
    fake_now[0] += 120
    assert t.get_active_chain("tenant1", "scope1", "c1") is None


def test_op_after_window_starts_new_chain():
    fake_now = [1000.0]

    def now():
        return fake_now[0]

    defn = make_combo("c1", ["op_a", "op_b"], window=60)
    t = CombinationTracker(combinations=[defn], time_fn=now)
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=1.0)
    fake_now[0] += 120
    chains = t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=2.0)
    # New chain starts; aggregate cost = 2.0 (not 3.0)
    assert chains[0].aggregate_cost_usd == 2.0


def test_cleanup_expired_removes_stale_chains():
    fake_now = [1000.0]

    def now():
        return fake_now[0]

    defn = make_combo("c1", ["op_a"], window=60)
    t = CombinationTracker(combinations=[defn], time_fn=now)
    t.record_op("tenant1", "scope1", "op_a")
    t.record_op("tenant2", "scope2", "op_a")
    fake_now[0] += 120
    removed = t.cleanup_expired()
    assert removed == 2


def test_lru_eviction_caps_per_tenant():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn], max_chains_per_tenant=5)
    for i in range(10):
        t.record_op("tenant1", "scope_%d" % i, "op_a", per_op_cost_usd=1.0)
    # Tenant1 should have only 5 chains
    chains = t.all_active_chains(tenant_id="tenant1")
    assert len(chains) == 5


# ── Loading from YAML dicts ──────────────────────────────────────────────────


def test_load_from_yaml_docs():
    docs = [
        {
            "combination_id": "test_yaml_combo",
            "name": "YAML loaded",
            "description": "test",
            "cloud": "aws",
            "trigger_ops": [{"tool_name": "op_a"}, {"tool_name": "op_b"}],
            "window_seconds": 1800,
            "risk_profile": {"cost_runaway_severity": "high", "blast_radius_severity": "low"},
            "policy_primitives": {"ops_count": 2},
        }
    ]
    t = CombinationTracker()
    t.load_from_yaml_docs(docs)
    assert t.get_definition("test_yaml_combo") is not None
    assert "test_yaml_combo" in t.known_combination_ids()


def test_load_from_yaml_skips_invalid_entries():
    docs = [
        {"combination_id": "good_combo", "trigger_ops": [{"tool_name": "x"}], "window_seconds": 100,
         "risk_profile": {}, "policy_primitives": {}},
        {"no_id_here": True},  # ignored
    ]
    t = CombinationTracker()
    t.load_from_yaml_docs(docs)
    assert t.known_combination_ids() == ["good_combo"]


# ── Integration with policy conditions ───────────────────────────────────────


def make_context(tracker, **extra):
    ctx = {"tenant_id": "tenant1", "scope_id": "scope1", "combination_tracker": tracker}
    ctx.update(extra)
    return ctx


def test_condition_aggregate_cost_above_threshold():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=150.0)
    cond = CombinationAggregateCostUsdGt(
        condition="combination_aggregate_cost_usd_gt", threshold=100.0, combination_id="c1"
    )
    assert evaluate_condition(cond, make_context(t)) is True


def test_condition_aggregate_cost_below_threshold():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=50.0)
    cond = CombinationAggregateCostUsdGt(
        condition="combination_aggregate_cost_usd_gt", threshold=100.0, combination_id="c1"
    )
    assert evaluate_condition(cond, make_context(t)) is False


def test_condition_aggregate_cost_no_combination_id_any_match():
    """When combination_id is None, any active chain over threshold triggers."""
    defn1 = make_combo("c1", ["op_a"])
    defn2 = make_combo("c2", ["op_b"])
    t = CombinationTracker(combinations=[defn1, defn2])
    t.record_op("tenant1", "scope1", "op_a", per_op_cost_usd=10.0)
    t.record_op("tenant1", "scope1", "op_b", per_op_cost_usd=500.0)
    cond = CombinationAggregateCostUsdGt(
        condition="combination_aggregate_cost_usd_gt", threshold=100.0
    )
    assert evaluate_condition(cond, make_context(t)) is True


def test_condition_ops_count_above_threshold():
    defn = make_combo("c1", ["op_a", "op_b", "op_c"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a")
    t.record_op("tenant1", "scope1", "op_b")
    t.record_op("tenant1", "scope1", "op_c")
    cond = CombinationOpsCountGt(
        condition="combination_ops_count_gt", threshold=2, combination_id="c1"
    )
    assert evaluate_condition(cond, make_context(t)) is True


def test_condition_window_seconds_lt():
    fake_now = [1000.0]

    def now():
        return fake_now[0]

    defn = make_combo("c1", ["op_a"], window=3600)
    t = CombinationTracker(combinations=[defn], time_fn=now)
    t.record_op("tenant1", "scope1", "op_a")
    fake_now[0] += 10  # 10 seconds elapsed
    cond = CombinationWindowSecondsLt(
        condition="combination_window_seconds_lt", threshold=60.0, combination_id="c1"
    )
    assert evaluate_condition(cond, make_context(t)) is True


def test_condition_id_matches_when_active():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    t.record_op("tenant1", "scope1", "op_a")
    cond = CombinationIdMatches(condition="combination_id_matches", combination_id="c1")
    assert evaluate_condition(cond, make_context(t)) is True


def test_condition_id_matches_false_when_inactive():
    defn = make_combo("c1", ["op_a"])
    t = CombinationTracker(combinations=[defn])
    cond = CombinationIdMatches(condition="combination_id_matches", combination_id="c1")
    assert evaluate_condition(cond, make_context(t)) is False


def test_condition_returns_false_without_tracker():
    """Missing tracker = fail-closed don't-block."""
    cond = CombinationAggregateCostUsdGt(
        condition="combination_aggregate_cost_usd_gt", threshold=10.0, combination_id="c1"
    )
    assert evaluate_condition(cond, {}) is False


# ── Concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_record_ops_no_crash():
    defn = make_combo("c_concurrent", ["op_a", "op_b"], window=3600)
    t = CombinationTracker(combinations=[defn])

    def worker(idx):
        for _ in range(10):
            t.record_op("tenant_%d" % (idx % 5), "scope_%d" % idx, "op_a", per_op_cost_usd=0.1)
            t.record_op("tenant_%d" % (idx % 5), "scope_%d" % idx, "op_b", per_op_cost_usd=0.2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(worker, range(100)))

    # Sanity: should have many active chains
    total = sum(len(t.all_active_chains(tenant_id="tenant_%d" % i)) for i in range(5))
    assert total > 0


def test_global_tracker_setter():
    original = get_global_tracker()
    try:
        defn = make_combo("c_global", ["op_a"])
        tracker = CombinationTracker(combinations=[defn])
        set_global_tracker(tracker)
        assert get_global_tracker() is tracker
        # Condition without explicit context tracker uses global
        tracker.record_op("tenant_g", "scope_g", "op_a", per_op_cost_usd=200.0)
        cond = CombinationAggregateCostUsdGt(
            condition="combination_aggregate_cost_usd_gt", threshold=100.0, combination_id="c_global"
        )
        # Provide tenant/scope but NOT combination_tracker
        assert evaluate_condition(cond, {"tenant_id": "tenant_g", "scope_id": "scope_g"}) is True
    finally:
        set_global_tracker(original)


# ── Oracle fixtures integration ──────────────────────────────────────────────


def test_oracle_fixture_loadable_per_combination_id():
    """For each fixture, verify combination_id maps to a definition the tracker can parse."""
    import json
    import os
    import yaml

    repo_intel = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "tessera-intelligence")
    )
    if not os.path.isdir(repo_intel):
        pytest.skip("tessera-intelligence sibling repo not present")

    combinations_root = os.path.join(repo_intel, "combinations")
    fixtures_root = os.path.join(repo_intel, "tests", "fixtures", "combinations")
    if not os.path.isdir(combinations_root) or not os.path.isdir(fixtures_root):
        pytest.skip("tessera-intelligence combinations not yet present")

    # Load all combinations
    docs = []
    for cloud in ["aws", "azure", "gcp"]:
        d = os.path.join(combinations_root, cloud, "v1.0.0")
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname.endswith(".yaml"):
                with open(os.path.join(d, fname)) as f:
                    docs.append(yaml.safe_load(f))

    tracker = CombinationTracker()
    tracker.load_from_yaml_docs(docs)

    fixture_count = 0
    for cloud in ["aws", "azure", "gcp"]:
        d = os.path.join(fixtures_root, cloud, "v1.0.0")
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.endswith(".fixture.json"):
                continue
            with open(os.path.join(d, fname)) as f:
                fx = json.load(f)
            cid = fx["combination_id"]
            defn = tracker.get_definition(cid)
            assert defn is not None, "%s: combination_id not loaded" % cid
            fixture_count += 1

    assert fixture_count >= 45, "Expected at least 45 fixtures, got %d" % fixture_count
