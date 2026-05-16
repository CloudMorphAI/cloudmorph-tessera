"""Unit tests for the blast_radius condition + BlastRadiusBackend."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tessera.integrations.aws.blast_radius import BlastRadiusBackend
from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import BlastRadius

# ── Helpers ───────────────────────────────────────────────────────────────────

_WILDCARD_PRINCIPAL = 999_999


def _ctx(tool_name: str = "iam:PutRolePolicy", args: dict | None = None, backend=None) -> dict:
    return {
        "tool_call": {"name": tool_name, "arguments": args or {}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "blast_radius_backend": backend,
        "scope": "test",
    }


def _mock_iam(*, users: int = 5, roles: int = 3, assume_policy: dict | None = None):
    """Build a mock boto3 IAM client."""
    iam = MagicMock()
    iam.list_users.return_value = {"Users": [{"UserName": f"u{i}"} for i in range(users)]}
    iam.list_roles.return_value = {"Roles": [{"RoleName": f"r{i}"} for i in range(roles)]}
    default_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    iam.get_role.return_value = {
        "Role": {"AssumeRolePolicyDocument": assume_policy or default_policy}
    }
    return iam


# ── BlastRadiusBackend unit tests ─────────────────────────────────────────────


def test_wildcard_principal_star_returns_999999():
    """Policy with Principal=* → 999_999."""
    backend = BlastRadiusBackend()
    iam = _mock_iam()
    # Override get_role to return wildcard policy
    iam.get_role.return_value = {
        "Role": {
            "AssumeRolePolicyDocument": {
                "Statement": [{"Principal": "*", "Effect": "Allow", "Action": "sts:AssumeRole"}]
            }
        }
    }
    with patch.object(backend, "_iam_client", return_value=iam):
        count = backend.compute("iam:PutRolePolicy", {"RoleName": "my-role"})
    assert count == _WILDCARD_PRINCIPAL


def test_specific_arn_principal_returns_1():
    """Policy with a specific ARN principal → count 1."""
    backend = BlastRadiusBackend()
    policy = {
        "Statement": [
            {
                "Principal": {"AWS": "arn:aws:iam::123456789012:role/specific-role"},
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
            }
        ]
    }
    iam = _mock_iam(assume_policy=policy)
    with patch.object(backend, "_iam_client", return_value=iam):
        count = backend.compute("iam:PutRolePolicy", {"RoleName": "my-role"})
    assert count == 1


def test_account_root_principal_counts_users_and_roles():
    """Principal=arn:aws:iam::123:root → count = users + roles in account."""
    backend = BlastRadiusBackend()
    policy = {
        "Statement": [
            {
                "Principal": {"AWS": "arn:aws:iam::123456789012:root"},
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
            }
        ]
    }
    iam = _mock_iam(assume_policy=policy, users=5, roles=3)
    with patch.object(backend, "_iam_client", return_value=iam):
        count = backend.compute("iam:PutRolePolicy", {"RoleName": "my-role"})
    assert count == 8  # 5 users + 3 roles


def test_cross_account_arn_counted():
    """Multiple specific ARNs from different accounts → count = number of ARNs."""
    backend = BlastRadiusBackend()
    policy = {
        "Statement": [
            {
                "Principal": {
                    "AWS": [
                        "arn:aws:iam::111111111111:role/role-a",
                        "arn:aws:iam::222222222222:role/role-b",
                    ]
                },
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
            }
        ]
    }
    iam = _mock_iam(assume_policy=policy)
    with patch.object(backend, "_iam_client", return_value=iam):
        count = backend.compute("iam:PutRolePolicy", {"RoleName": "my-role"})
    assert count == 2


def test_missing_args_fall_closed_blocks():
    """blast_radius condition with missing backend defaults to True (block on uncertainty)."""
    cond = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=10,
        operator="greater_than",
    )
    ctx = _ctx(backend=None)
    assert evaluate_condition(cond, ctx) is True


def test_unknown_resource_type_returns_false():
    """blast_radius condition returns False when tool_name not in resource_types."""
    cond = BlastRadius(
        condition="blast_radius",
        principal_count_threshold=1,
        resource_types=["iam:PutRolePolicy"],
        operator="greater_than",
    )
    backend = MagicMock()
    backend.compute.return_value = 999
    ctx = _ctx(tool_name="s3:PutObject", backend=backend)
    assert evaluate_condition(cond, ctx) is False


def test_s3_bucket_policy_wildcard():
    """S3 bucket policy with Principal=* → 999_999."""
    backend = BlastRadiusBackend()
    policy_doc = json.dumps({
        "Statement": [{"Principal": "*", "Effect": "Allow", "Action": "s3:GetObject"}]
    })
    iam = _mock_iam()
    with patch.object(backend, "_iam_client", return_value=iam):
        count = backend.compute("s3:PutBucketPolicy", {"Bucket": "my-bucket", "Policy": policy_doc})
    assert count == _WILDCARD_PRINCIPAL
