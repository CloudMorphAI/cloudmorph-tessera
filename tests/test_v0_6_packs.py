"""Engine-side tests for v0.6.0 tri-cloud policy packs.

Verifies that the 3 new packs (tri-cloud-cost-explosion-defense,
tri-cloud-blast-radius-defense, multi-cloud-data-exfiltration-defense)
parse cleanly via the loader, that their condition types are recognised by the
engine, and that policy chains execute correctly under synthetic call streams.
"""

from __future__ import annotations

import os
import pytest
import yaml

from tessera.cost.combinations import CombinationTracker, CombinationDef, TriggerOp
from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import (
    CombinationAggregateCostUsdGt,
    CombinationIdMatches,
    CombinationOpsCountGt,
    CombinationWindowSecondsLt,
)

REPO_TESSERA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_INTEL = os.path.abspath(os.path.join(REPO_TESSERA, "..", "tessera-intelligence"))

PACK_NAMES = (
    "tri-cloud-cost-explosion-defense",
    "tri-cloud-blast-radius-defense",
    "multi-cloud-data-exfiltration-defense",
)


def _pack_dir(name):
    return os.path.join(REPO_INTEL, "packs", name, "v1.0.0")


def _policies_dir(name):
    return os.path.join(_pack_dir(name), "policies")


def _require_intel():
    if not os.path.isdir(REPO_INTEL):
        pytest.skip("tessera-intelligence sibling repo not present")


# ── Loading ──────────────────────────────────────────────────────────────────


def test_all_3_packs_present():
    _require_intel()
    for name in PACK_NAMES:
        assert os.path.isdir(_pack_dir(name)), "Missing pack %s" % name
        assert os.path.isfile(os.path.join(_pack_dir(name), "manifest.json"))


def test_all_policy_yamls_load():
    _require_intel()
    for name in PACK_NAMES:
        for fname in os.listdir(_policies_dir(name)):
            if not fname.endswith(".yaml"):
                continue
            with open(os.path.join(_policies_dir(name), fname)) as f:
                data = yaml.safe_load(f)
            assert isinstance(data, dict)
            assert "when" in data


def test_policy_conditions_match_engine_schema():
    """Every condition referenced by the new packs must be in the v0.6.0 dispatch table."""
    _require_intel()
    known = {
        "arg_equals", "arg_greater_than", "arg_less_than", "arg_matches_regex",
        "arg_in_set", "arg_contains_pattern", "arg_size_greater_than", "tool_name_in",
        "action_class_in", "intent_class_in", "intent_purpose_matches", "region_in",
        "time_of_day_outside", "meta_field_equals", "any_of", "none_of",
        "predicted_cost", "blast_radius", "affected_resource_count", "data_volume",
        "cumulative_spend_today", "arg_path_matches_regex", "sts_chain_depth_greater_than",
        "combination_aggregate_cost_usd_gt", "combination_ops_count_gt",
        "combination_window_seconds_lt", "combination_id_matches",
    }
    for name in PACK_NAMES:
        for fname in os.listdir(_policies_dir(name)):
            if not fname.endswith(".yaml"):
                continue
            with open(os.path.join(_policies_dir(name), fname)) as f:
                data = yaml.safe_load(f)
            for cond in data["when"]:
                assert cond["condition"] in known, (
                    "%s/%s: unknown condition %r" % (name, fname, cond["condition"])
                )


# ── Synthetic evaluation ──────────────────────────────────────────────────────


def _setup_tracker(combinations):
    return CombinationTracker(combinations=combinations)


def test_synthetic_chain_fires_cost_explosion_pack_policy():
    """Simulate an EC2+EBS+S3+Bedrock chain firing > $100; verify the cap policy condition triggers."""
    defn = CombinationDef(
        combination_id="aws_ec2_ebs_s3_bedrock_chain",
        name="chain",
        description="",
        cloud="aws",
        trigger_ops=[
            TriggerOp(tool_name="aws_ec2_RunInstances", role="provision"),
            TriggerOp(tool_name="aws_bedrock_InvokeModel", role="compute"),
        ],
        window_seconds=3600,
        cost_runaway_severity="high",
        blast_radius_severity="medium",
        ops_count=2,
    )
    tracker = _setup_tracker([defn])
    tracker.record_op("t1", "s1", "aws_ec2_RunInstances", per_op_cost_usd=20.0)
    tracker.record_op("t1", "s1", "aws_bedrock_InvokeModel", per_op_cost_usd=95.0)

    cond_id = CombinationIdMatches(
        condition="combination_id_matches", combination_id="aws_ec2_ebs_s3_bedrock_chain"
    )
    cond_cost = CombinationAggregateCostUsdGt(
        condition="combination_aggregate_cost_usd_gt",
        threshold=100.0,
        combination_id="aws_ec2_ebs_s3_bedrock_chain",
    )
    ctx = {"tenant_id": "t1", "scope_id": "s1", "combination_tracker": tracker}
    assert evaluate_condition(cond_id, ctx) is True
    assert evaluate_condition(cond_cost, ctx) is True


def test_synthetic_fanout_fires_ops_count_policy():
    """Simulate Lambda fanout > 100 ops; verify combination_ops_count_gt triggers."""
    defn = CombinationDef(
        combination_id="aws_lambda_fanout_overspend",
        name="fanout",
        description="",
        cloud="aws",
        trigger_ops=[TriggerOp(tool_name="aws_lambda_InvokeFunction", role="invoke")],
        window_seconds=300,
        cost_runaway_severity="high",
        blast_radius_severity="low",
        ops_count=1,
    )
    tracker = _setup_tracker([defn])
    for _ in range(150):
        tracker.record_op("t1", "s1", "aws_lambda_InvokeFunction", per_op_cost_usd=0.0001)
    cond = CombinationOpsCountGt(
        condition="combination_ops_count_gt", threshold=100, combination_id="aws_lambda_fanout_overspend"
    )
    ctx = {"tenant_id": "t1", "scope_id": "s1", "combination_tracker": tracker}
    assert evaluate_condition(cond, ctx) is True


def test_no_active_chain_does_not_trigger_policy():
    """Empty tracker should not trigger combination_id_matches."""
    tracker = CombinationTracker()
    cond = CombinationIdMatches(condition="combination_id_matches", combination_id="anything")
    ctx = {"tenant_id": "t1", "scope_id": "s1", "combination_tracker": tracker}
    assert evaluate_condition(cond, ctx) is False


# ── Audit emission shape (light — full audit covered in pack tests) ──────────


def test_packs_have_descriptive_reasons():
    """Each policy in the new packs should have a non-trivial 'reason' field."""
    _require_intel()
    for name in PACK_NAMES:
        for fname in os.listdir(_policies_dir(name)):
            if not fname.endswith(".yaml"):
                continue
            with open(os.path.join(_policies_dir(name), fname)) as f:
                data = yaml.safe_load(f)
            reason = data.get("reason", "")
            assert isinstance(reason, str) and len(reason) >= 10, (
                "%s/%s: reason too short" % (name, fname)
            )
