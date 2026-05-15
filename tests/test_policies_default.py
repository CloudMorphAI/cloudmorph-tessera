"""Tests for the bundled `tessera/policies_default/` corpus.

Verifies:
1. Every shipped YAML loads cleanly via FilesystemPolicyLoader (schema + regex-safety).
2. The 6 P0-1..6 aws-mcp-* policies are present, have the expected id/action, and
   fire on synthetic MCP `tools/call` requests.
3. The 6 P0-1..6 policies do NOT fire on benign / least-privilege synthetic calls.

Each scenario constructs an evaluation context as the proxy/engine does, then asks
``evaluate_condition`` whether ANY (any_of root) of the policy's top-level conditions
matches. We avoid the full PolicyEngine surface here to keep these tests narrowly
about the bundled YAML content; the engine is exercised in tests/unit/policy/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tessera.policy.conditions import evaluate_conditions
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action, Policy

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "tessera" / "policies_default"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def all_default_policies() -> list[Policy]:
    """Load every YAML in tessera/policies_default/. Fails if any file is invalid."""
    loader = FilesystemPolicyLoader(DEFAULTS_DIR)
    policies = loader.load_all()
    assert loader.state()["errored"] == [], (
        f"Policies failed to load: {loader.state()['errored']}"
    )
    return policies


@pytest.fixture(scope="module")
def policy_by_id(all_default_policies: list[Policy]) -> dict[str, Policy]:
    return {p.id: p for p in all_default_policies}


def _ctx(tool_name: str, arguments: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Build the evaluation context shape the conditions module expects."""
    return {
        "tool_call": {"name": tool_name, "arguments": arguments, "_meta": None},
        "intent": None,
        "upstream": "*",
        "runtime": {"lockdown": False},
        "policy_id": None,
        **extra,
    }


# ── Top-level corpus health ──────────────────────────────────────────────────


def test_all_default_policies_load(all_default_policies: list[Policy]) -> None:
    """Every YAML in policies_default/ parses against the Policy schema."""
    assert len(all_default_policies) > 0, "no default policies loaded"


def test_all_default_policy_ids_unique(all_default_policies: list[Policy]) -> None:
    ids = [p.id for p in all_default_policies]
    assert len(ids) == len(set(ids)), f"duplicate policy ids: {ids}"


def test_all_default_policy_actions_in_schema_enum(
    all_default_policies: list[Policy],
) -> None:
    """Every bundled policy uses a valid Action enum value.

    Sanity check against the P0-12 audit grep that flagged a non-enum verb.
    """
    valid = {a.value for a in Action}
    for p in all_default_policies:
        assert p.action.value in valid, (
            f"policy {p.id} has out-of-enum action {p.action.value!r}"
        )


# ── P0-1..6 presence + identity ──────────────────────────────────────────────


_P0_POLICIES = {
    "aws-mcp-passrole-guard": ("require_approval", 95),
    "aws-mcp-admin-policy-deny": ("block", 99),
    "aws-mcp-create-access-key-deny": ("block", 97),
    "aws-mcp-kms-deletion-approval": ("require_approval", 98),
    "aws-mcp-rds-public-deny": ("block", 97),
    "aws-mcp-ec2-imdsv1-deny": ("block", 96),
}


@pytest.mark.parametrize("policy_id,expected", list(_P0_POLICIES.items()))
def test_p0_bundled_policy_present(
    policy_by_id: dict[str, Policy], policy_id: str, expected: tuple[str, int]
) -> None:
    assert policy_id in policy_by_id, (
        f"P0 bundled policy {policy_id} missing from policies_default/"
    )
    p = policy_by_id[policy_id]
    expected_action, expected_priority = expected
    assert p.action.value == expected_action, (
        f"{policy_id}: action={p.action.value!r} expected {expected_action!r}"
    )
    assert p.priority == expected_priority, (
        f"{policy_id}: priority={p.priority} expected {expected_priority}"
    )


# ── P0-1 PassRole guard ──────────────────────────────────────────────────────


def test_passrole_guard_fires_on_admin_role_arn(policy_by_id: dict[str, Policy]) -> None:
    p = policy_by_id["aws-mcp-passrole-guard"]
    ctx = _ctx(
        "aws_iam_PassRole",
        {"RoleArn": "arn:aws:iam::123456789012:role/AdministratorAccessRole"},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_passrole_guard_fires_on_admin_role_name(policy_by_id: dict[str, Policy]) -> None:
    p = policy_by_id["aws-mcp-passrole-guard"]
    ctx = _ctx("aws_iam_PassRole", {"RoleName": "my-admin-role"})
    assert evaluate_conditions(p.when, ctx) is True


def test_passrole_guard_passes_least_privilege_with_safe_blast_radius(
    policy_by_id: dict[str, Policy],
) -> None:
    """Benign role name + injected low blast-radius count → policy does NOT fire.

    The policy has three OR'd branches: RoleArn regex, RoleName regex, and a
    blast_radius condition. blast_radius fails-closed (True / fire) when no
    backend is configured — that is the intended behaviour for an OSS bundled
    default: when uncertainty exists about who can assume the target role,
    err on the side of human approval. To exercise the genuine least-privilege
    "no fire" path we inject blast_radius_cache with a safe count of 1.
    """
    p = policy_by_id["aws-mcp-passrole-guard"]
    ctx = _ctx(
        "aws_iam_PassRole",
        {"RoleArn": "arn:aws:iam::123456789012:role/s3-readonly-customer-app"},
        blast_radius_cache={"aws_iam_PassRole": 1},
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_passrole_guard_fail_closed_when_no_blast_radius_backend(
    policy_by_id: dict[str, Policy],
) -> None:
    """No regex match + no blast-radius backend → policy fires (fail-closed).

    This documents the intentional fail-closed posture of the blast_radius
    branch: when no resolver is configured, treat PassRole as gated. Tenants
    who consider this too aggressive should fork and remove the blast_radius
    branch from the policy's when clause.
    """
    p = policy_by_id["aws-mcp-passrole-guard"]
    ctx = _ctx(
        "aws_iam_PassRole",
        {"RoleArn": "arn:aws:iam::123456789012:role/benign-customer-role"},
    )
    assert evaluate_conditions(p.when, ctx) is True


# ── P0-2 Admin policy deny ───────────────────────────────────────────────────


def test_admin_policy_deny_fires_on_attach_admin(policy_by_id: dict[str, Policy]) -> None:
    p = policy_by_id["aws-mcp-admin-policy-deny"]
    ctx = _ctx(
        "aws_iam_AttachRolePolicy",
        {
            "RoleName": "harmless-role",
            "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_admin_policy_deny_fires_on_put_role_policy_wildcard(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-admin-policy-deny"]
    ctx = _ctx(
        "aws_iam_PutRolePolicy",
        {
            "RoleName": "r",
            "PolicyName": "p",
            "PolicyDocument": (
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                '"Action":"*","Resource":"*"}]}'
            ),
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_admin_policy_deny_passes_least_priv_policy(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-admin-policy-deny"]
    ctx = _ctx(
        "aws_iam_AttachRolePolicy",
        {
            "RoleName": "harmless-role",
            "PolicyArn": "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_admin_policy_deny_passes_narrow_inline_policy(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-admin-policy-deny"]
    ctx = _ctx(
        "aws_iam_PutRolePolicy",
        {
            "RoleName": "r",
            "PolicyName": "p",
            "PolicyDocument": (
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                '"Action":"s3:GetObject","Resource":"arn:aws:s3:::bucket/key"}]}'
            ),
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


# ── P0-3 CreateAccessKey deny ────────────────────────────────────────────────


def test_create_access_key_deny_fires_on_admin_user(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-create-access-key-deny"]
    ctx = _ctx("aws_iam_CreateAccessKey", {"UserName": "admin-bootstrap"})
    assert evaluate_conditions(p.when, ctx) is True


def test_create_access_key_deny_fires_on_root_user(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-create-access-key-deny"]
    ctx = _ctx("aws_iam_CreateAccessKey", {"UserName": "root_account"})
    assert evaluate_conditions(p.when, ctx) is True


def test_create_access_key_deny_passes_service_user(
    policy_by_id: dict[str, Policy],
) -> None:
    """Non-admin service-account UserName passes the default-deny pattern.

    The policy intentionally only fires on admin-tier UserName matches; non-admin
    flows are not in the default-deny set so that the bundled default does not
    break legitimate static-key issuance for CI deployer accounts. Tenants who
    want stricter default-deny should fork and tighten.
    """
    p = policy_by_id["aws-mcp-create-access-key-deny"]
    ctx = _ctx("aws_iam_CreateAccessKey", {"UserName": "ci-deployer"})
    assert evaluate_conditions(p.when, ctx) is False


# ── P0-4 KMS deletion approval ───────────────────────────────────────────────


def test_kms_deletion_approval_fires_on_any_keyid(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-kms-deletion-approval"]
    ctx = _ctx(
        "aws_kms_ScheduleKeyDeletion",
        {"KeyId": "alias/prod-data", "PendingWindowInDays": 7},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_kms_deletion_approval_skips_when_no_keyid(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-kms-deletion-approval"]
    ctx = _ctx("aws_kms_ScheduleKeyDeletion", {})
    assert evaluate_conditions(p.when, ctx) is False


# ── P0-5 RDS public deny ─────────────────────────────────────────────────────


def test_rds_public_deny_fires_on_publicly_accessible_true_bool(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-rds-public-deny"]
    ctx = _ctx(
        "aws_rds_CreateDBInstance",
        {"DBInstanceIdentifier": "test", "PubliclyAccessible": True},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_rds_public_deny_fires_on_publicly_accessible_true_string(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-rds-public-deny"]
    ctx = _ctx(
        "aws_rds_ModifyDBInstance",
        {"DBInstanceIdentifier": "test", "PubliclyAccessible": "true"},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_rds_public_deny_passes_private_instance(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-rds-public-deny"]
    ctx = _ctx(
        "aws_rds_CreateDBInstance",
        {"DBInstanceIdentifier": "test", "PubliclyAccessible": False},
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_rds_public_deny_passes_publicly_accessible_omitted(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-rds-public-deny"]
    ctx = _ctx(
        "aws_rds_CreateDBInstance",
        {"DBInstanceIdentifier": "test"},
    )
    assert evaluate_conditions(p.when, ctx) is False


# ── P0-6 EC2 IMDSv1 deny ─────────────────────────────────────────────────────


def test_ec2_imdsv1_deny_fires_on_run_instances_imdsv1(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-imdsv1-deny"]
    ctx = _ctx(
        "aws_ec2_RunInstances",
        {
            "ImageId": "ami-12345678",
            "MetadataOptions": {"HttpTokens": "optional", "HttpEndpoint": "enabled"},
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_ec2_imdsv1_deny_fires_on_modify_attribute_imdsv1(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-imdsv1-deny"]
    ctx = _ctx(
        "aws_ec2_ModifyInstanceAttribute",
        {"InstanceId": "i-12345", "HttpTokens": "optional"},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_ec2_imdsv1_deny_passes_imdsv2_required(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-imdsv1-deny"]
    ctx = _ctx(
        "aws_ec2_RunInstances",
        {
            "ImageId": "ami-12345678",
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_ec2_imdsv1_deny_passes_metadata_options_omitted(
    policy_by_id: dict[str, Policy],
) -> None:
    """Account-default IMDSv2 path — MetadataOptions omitted should pass.

    Modern AWS accounts default to IMDSv2-only, so an omitted MetadataOptions
    is the safe path. Customers with legacy-default accounts should fork this
    policy and add an explicit-presence check.
    """
    p = policy_by_id["aws-mcp-ec2-imdsv1-deny"]
    ctx = _ctx("aws_ec2_RunInstances", {"ImageId": "ami-12345678"})
    assert evaluate_conditions(p.when, ctx) is False
