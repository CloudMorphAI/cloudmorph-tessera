"""Microbench: PolicyEngine.evaluate() latency on bundled defaults.

Run:
    pytest benchmarks/decision_latency.py --benchmark-only \
        --benchmark-min-rounds=1000 --benchmark-max-time=30

Reports p50, p95, p99, mean, stddev. Target: p50 < 0.5ms on 18-policy default set
on commodity hardware (4-core, 16GB).
"""
from pathlib import Path  # noqa: I001

import pytest
from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader

POLICIES_DIR = Path(__file__).parent.parent / "tessera" / "policies_default"


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    loader = FilesystemPolicyLoader(POLICIES_DIR)
    policies = loader.load_all("default")
    return PolicyEngine(policies)


def _make_context(tool_name: str, args: dict) -> dict:
    return {
        "tool_call": {"name": tool_name, "arguments": args, "_meta": {}},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "scope": "bench",
        "state_backend": None,
        "blast_radius_backend": None,
        "cost_backend": None,
        "cost_cache": {},
        "aws_mapping": None,
    }


def test_bench_safe_tool_call(benchmark, engine):
    """Baseline: benign call that no policy blocks."""
    ctx = _make_context("aws_s3_GetObject", {"Bucket": "mybucket", "Key": "foo.txt"})
    benchmark(engine.evaluate, ctx)


def test_bench_blocked_iam_pass_role(benchmark, engine):
    """IAM PassRole gets caught by aws-mcp-passrole-guard.yaml."""
    ctx = _make_context("aws_iam_PassRole", {
        "RoleArn": "arn:aws:iam::123456789012:role/AdministratorAccess",
        "RoleSessionName": "test",
    })
    benchmark(engine.evaluate, ctx)


def test_bench_blocked_admin_policy_attach(benchmark, engine):
    """AttachRolePolicy with admin-tier policy ARN."""
    ctx = _make_context("aws_iam_AttachRolePolicy", {
        "RoleName": "test-role",
        "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
    })
    benchmark(engine.evaluate, ctx)


def test_bench_kms_schedule_deletion(benchmark, engine):
    """KMS ScheduleKeyDeletion gets require_approval."""
    ctx = _make_context("aws_kms_ScheduleKeyDeletion", {
        "KeyId": "arn:aws:kms:us-east-1:123:key/abc-123",
        "PendingWindowInDays": 7,
    })
    benchmark(engine.evaluate, ctx)


def test_bench_rds_public_deny(benchmark, engine):
    ctx = _make_context("aws_rds_CreateDBInstance", {
        "DBInstanceIdentifier": "test-db",
        "DBInstanceClass": "db.t3.micro",
        "Engine": "postgres",
        "PubliclyAccessible": True,
    })
    benchmark(engine.evaluate, ctx)


def test_bench_ec2_imdsv1_deny(benchmark, engine):
    ctx = _make_context("aws_ec2_RunInstances", {
        "ImageId": "ami-1234",
        "InstanceType": "t3.micro",
        "MinCount": 1, "MaxCount": 1,
        "MetadataOptions": {"HttpTokens": "optional"},
    })
    benchmark(engine.evaluate, ctx)


def test_bench_no_match_passthrough(benchmark, engine):
    """A call no policy matches — measures the cost of walking the full ruleset."""
    ctx = _make_context("custom_no_match_tool", {"arg1": "value1"})
    benchmark(engine.evaluate, ctx)
