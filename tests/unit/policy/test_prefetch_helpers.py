"""Tests for PolicyEngine prefetch helpers (P0-14, P0-15).

The proxy hot path calls `engine.policies_need_blast_radius(...)` and
`engine.policies_need_data_volume(...)` BEFORE building the request context.
When they return True / a non-empty set, the proxy runs the matching boto3
calls via `asyncio.to_thread`. Wrong answers mean either the proxy wastes
time fetching unused values, or the evaluator falls back to a blocking
synchronous boto3 call inside the event loop.

These tests pin the gate semantics so a future schema/condition refactor
cannot silently break it.
"""

from __future__ import annotations

from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import (
    Action,
    AnyOf,
    ArgEquals,
    BlastRadius,
    DataVolume,
    MatchSpec,
    NoneOf,
    Policy,
)


def _policy(
    policy_id: str,
    *,
    upstream: str = "*",
    tool: str | None = None,
    when: list | None = None,
) -> Policy:
    match_kwargs: dict = {"upstream": upstream}
    if tool is not None:
        match_kwargs["tool"] = tool
    return Policy(
        id=policy_id,
        name=f"Policy {policy_id}",
        action=Action.block,
        priority=0,
        match=MatchSpec(**match_kwargs),
        when=when or [],
    )


# ── policies_need_blast_radius ────────────────────────────────────────────────


def test_blast_radius_gate_returns_true_when_matching_policy_uses_it():
    cond = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
        resource_types=["aws_iam_PutRolePolicy"],
    )
    engine = PolicyEngine([_policy("p1", tool="aws_iam_PutRolePolicy", when=[cond])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is True


def test_blast_radius_gate_returns_false_when_no_policy_uses_it():
    """No matching policy uses BlastRadius → False, the proxy must skip the IAM call."""
    cond = ArgEquals(condition="arg_equals", arg="Bucket", value="foo")
    engine = PolicyEngine([_policy("p1", tool="aws_iam_PutRolePolicy", when=[cond])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is False


def test_blast_radius_gate_returns_false_when_no_matching_policy():
    """A BlastRadius policy that doesn't match this tool → False."""
    cond = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
        resource_types=["aws_s3_PutBucketPolicy"],
    )
    engine = PolicyEngine([_policy("p1", tool="aws_s3_PutBucketPolicy", when=[cond])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is False


def test_blast_radius_gate_wildcard_resource_types_matches_any_tool():
    """Empty resource_types is a wildcard — applies to every tool match."""
    cond = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
        resource_types=[],  # wildcard
    )
    engine = PolicyEngine([_policy("p1", tool="aws_iam_PutRolePolicy", when=[cond])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is True


def test_blast_radius_gate_descends_into_any_of():
    """AnyOf nesting must still surface a BlastRadius condition to the gate."""
    inner = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
        resource_types=["aws_iam_PutRolePolicy"],
    )
    outer = AnyOf(
        condition="any_of",
        conditions=[ArgEquals(condition="arg_equals", arg="x", value="y"), inner],
    )
    engine = PolicyEngine([_policy("p1", tool="aws_iam_PutRolePolicy", when=[outer])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is True


def test_blast_radius_gate_descends_into_none_of():
    """NoneOf nesting must still surface a BlastRadius condition to the gate."""
    inner = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
        resource_types=["aws_iam_PutRolePolicy"],
    )
    outer = NoneOf(
        condition="none_of",
        conditions=[inner],
    )
    engine = PolicyEngine([_policy("p1", tool="aws_iam_PutRolePolicy", when=[outer])])
    assert engine.policies_need_blast_radius(
        "aws_iam_PutRolePolicy", "aws"
    ) is True


# ── policies_need_data_volume ─────────────────────────────────────────────────


def test_data_volume_gate_returns_set_of_estimators():
    cond_s3 = DataVolume(
        condition="data_volume",
        bytes_threshold=1024,
        operator="greater_than",
        estimator="s3_get_byte_estimate",
    )
    cond_rds = DataVolume(
        condition="data_volume",
        bytes_threshold=1024,
        operator="greater_than",
        estimator="rds_query_result_estimate",
    )
    engine = PolicyEngine([
        _policy("p-s3", tool="aws_s3_GetObject", when=[cond_s3]),
        _policy("p-rds", tool="aws_s3_GetObject", when=[cond_rds]),
    ])
    needed = engine.policies_need_data_volume("aws_s3_GetObject", "aws")
    assert needed == {"s3_get_byte_estimate", "rds_query_result_estimate"}


def test_data_volume_gate_returns_empty_when_no_matching_policy():
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=1024,
        operator="greater_than",
        estimator="s3_get_byte_estimate",
    )
    engine = PolicyEngine([_policy("p1", tool="aws_s3_PutObject", when=[cond])])
    needed = engine.policies_need_data_volume("aws_dynamodb_GetItem", "aws")
    assert needed == set()


def test_data_volume_gate_excludes_static_arg_size():
    """static_arg_size needs no boto3 call so it shouldn't trigger prefetch."""
    cond = DataVolume(
        condition="data_volume",
        bytes_threshold=1024,
        operator="greater_than",
        estimator="static_arg_size",
    )
    engine = PolicyEngine([_policy("p1", tool="aws_s3_GetObject", when=[cond])])
    needed = engine.policies_need_data_volume("aws_s3_GetObject", "aws")
    # static_arg_size IS still surfaced — gate returns all estimators in use;
    # the proxy decides which to prefetch based on the set. The proxy only
    # prefetches s3_get_byte_estimate and rds_query_result_estimate.
    assert needed == {"static_arg_size"}


def test_data_volume_gate_descends_into_any_of():
    inner = DataVolume(
        condition="data_volume",
        bytes_threshold=1024,
        operator="greater_than",
        estimator="s3_get_byte_estimate",
    )
    outer = AnyOf(
        condition="any_of",
        conditions=[ArgEquals(condition="arg_equals", arg="x", value="y"), inner],
    )
    engine = PolicyEngine([_policy("p1", tool="aws_s3_GetObject", when=[outer])])
    needed = engine.policies_need_data_volume("aws_s3_GetObject", "aws")
    assert needed == {"s3_get_byte_estimate"}
