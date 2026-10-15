"""Tests for the bundled `tessera/policies_default/` corpus.

Verifies:
1. Every shipped YAML loads cleanly via FilesystemPolicyLoader (schema + regex-safety).
2. The 6 P0-1..6 aws-mcp-* policies are present, have the expected id/action, and
   fire on synthetic MCP `tools/call` requests.
3. The 6 P0-1..6 policies do NOT fire on benign / least-privilege synthetic calls.
4. The 6 SA-5 aws-mcp-* policies (added to complete the 12-vector AWS-MCP surface)
   are present with the expected id/action, fire on the dangerous call they target,
   and do NOT fire on a benign/safe-path variant of a related call.

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


# ── SA-5: remaining 6 AWS-MCP vectors (12-vector coverage) ───────────────────
#
# The 6 P0-1..6 policies above cover: iam:PassRole, IAM admin-policy grant,
# iam:CreateAccessKey, KMS ScheduleKeyDeletion, RDS PubliclyAccessible, EC2
# IMDSv1. No repo doc enumerates a canonical "12-vector" list verbatim; the
# closest source is the [runtime policy contract](../../workspace-support/architecture/tessera-runtime/current.md#runtime-contracts),
# which is combined here with well-known AWS privilege-escalation/exposure
# vectors to reach 12 total AWS-MCP defaults:
#   7.  S3 bucket public exposure (policy/ACL/PublicAccessBlock)
#   8.  EC2 security-group 0.0.0.0/0 ingress on sensitive ports
#   9.  Secrets Manager / SSM bulk secret-read exfiltration
#   10. EC2 key-material minting + snapshot/AMI public-sharing exfiltration
#   11. CloudTrail StopLogging/DeleteTrail (audit-log disable)
#   12. Lambda public-invoke exposure (AddPermission "*" / Function URL none-auth)

_SA5_POLICIES = {
    "aws-mcp-s3-public-access-deny": ("block", 97),
    "aws-mcp-sg-open-ingress-deny": ("block", 96),
    "aws-mcp-secrets-bulk-read-approval": ("require_approval", 90),
    "aws-mcp-ec2-keymaterial-exfil-deny": ("block", 95),
    "aws-mcp-cloudtrail-logging-disable-deny": ("block", 99),
    "aws-mcp-lambda-public-invoke-deny": ("block", 94),
}


@pytest.mark.parametrize("policy_id,expected", list(_SA5_POLICIES.items()))
def test_sa5_bundled_policy_present(
    policy_by_id: dict[str, Policy], policy_id: str, expected: tuple[str, int]
) -> None:
    assert policy_id in policy_by_id, (
        f"SA-5 bundled policy {policy_id} missing from policies_default/"
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


# ── SA-5 Vector 7: S3 public access deny ─────────────────────────────────────


def test_s3_public_access_deny_fires_on_wildcard_bucket_policy(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx = _ctx(
        "aws_s3_PutBucketPolicy",
        {
            "Bucket": "my-bucket",
            "Policy": (
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                '"Principal":"*","Action":"s3:GetObject",'
                '"Resource":"arn:aws:s3:::my-bucket/*"}]}'
            ),
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_s3_public_access_deny_fires_on_public_read_acl(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx = _ctx("aws_s3_PutBucketAcl", {"Bucket": "my-bucket", "ACL": "public-read"})
    assert evaluate_conditions(p.when, ctx) is True


def test_s3_public_access_deny_fires_on_public_access_block_disabled(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx = _ctx(
        "aws_s3_PutPublicAccessBlock",
        {
            "Bucket": "my-bucket",
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": False,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_s3_public_access_deny_passes_scoped_bucket_policy(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx = _ctx(
        "aws_s3_PutBucketPolicy",
        {
            "Bucket": "my-bucket",
            "Policy": (
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                '"Principal":{"AWS":"arn:aws:iam::123456789012:root"},'
                '"Action":"s3:GetObject",'
                '"Resource":"arn:aws:s3:::my-bucket/*"}]}'
            ),
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_s3_public_access_deny_passes_private_acl_and_block_enabled(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx = _ctx("aws_s3_PutBucketAcl", {"Bucket": "my-bucket", "ACL": "private"})
    assert evaluate_conditions(p.when, ctx) is False
    p2 = policy_by_id["aws-mcp-s3-public-access-deny"]
    ctx2 = _ctx(
        "aws_s3_PutPublicAccessBlock",
        {
            "Bucket": "my-bucket",
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        },
    )
    assert evaluate_conditions(p2.when, ctx2) is False


# ── SA-5 Vector 8: Security-group open ingress deny ──────────────────────────


def test_sg_open_ingress_deny_fires_on_ssh_open_to_world(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-sg-open-ingress-deny"]
    ctx = _ctx(
        "aws_ec2_AuthorizeSecurityGroupIngress",
        {
            "GroupId": "sg-12345678",
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "CidrIp": "0.0.0.0/0",
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_sg_open_ingress_deny_fires_on_ip_permissions_form(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-sg-open-ingress-deny"]
    ctx = _ctx(
        "aws_ec2_AuthorizeSecurityGroupIngress",
        {
            "GroupId": "sg-12345678",
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 3389,
                    "ToPort": 3389,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }
            ],
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_sg_open_ingress_deny_passes_https_open_to_world(
    policy_by_id: dict[str, Policy],
) -> None:
    """0.0.0.0/0 on port 443 is a normal public-web-server pattern — not gated."""
    p = policy_by_id["aws-mcp-sg-open-ingress-deny"]
    ctx = _ctx(
        "aws_ec2_AuthorizeSecurityGroupIngress",
        {
            "GroupId": "sg-12345678",
            "IpProtocol": "tcp",
            "FromPort": 443,
            "ToPort": 443,
            "CidrIp": "0.0.0.0/0",
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_sg_open_ingress_deny_passes_ssh_scoped_to_office_cidr(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-sg-open-ingress-deny"]
    ctx = _ctx(
        "aws_ec2_AuthorizeSecurityGroupIngress",
        {
            "GroupId": "sg-12345678",
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "CidrIp": "203.0.113.0/24",
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


# ── SA-5 Vector 9: Secrets/SSM bulk read approval ────────────────────────────


def test_secrets_bulk_read_approval_fires_on_batch_get_secret_value(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-secrets-bulk-read-approval"]
    ctx = _ctx(
        "aws_secretsmanager_BatchGetSecretValue",
        {"SecretIdList": ["prod/db", "prod/api", "prod/cache"]},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_secrets_bulk_read_approval_fires_on_recursive_parameter_path(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-secrets-bulk-read-approval"]
    ctx = _ctx(
        "aws_ssm_GetParametersByPath",
        {"Path": "/", "Recursive": True, "WithDecryption": True},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_secrets_bulk_read_approval_fires_on_large_names_list(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-secrets-bulk-read-approval"]
    names = [f"/app/config/param-{i}" for i in range(20)]
    ctx = _ctx("aws_ssm_GetParameters", {"Names": names, "WithDecryption": True})
    assert evaluate_conditions(p.when, ctx) is True


def test_secrets_bulk_read_approval_passes_single_parameter_get(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-secrets-bulk-read-approval"]
    ctx = _ctx(
        "aws_ssm_GetParameters",
        {"Names": ["/app/db_host"], "WithDecryption": True},
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_secrets_bulk_read_approval_passes_non_recursive_path(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-secrets-bulk-read-approval"]
    ctx = _ctx(
        "aws_ssm_GetParametersByPath",
        {"Path": "/app/config", "Recursive": False},
    )
    assert evaluate_conditions(p.when, ctx) is False


# ── SA-5 Vector 10: EC2 key-material / snapshot-sharing exfiltration deny ────


def test_ec2_keymaterial_exfil_deny_fires_on_create_key_pair(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-keymaterial-exfil-deny"]
    ctx = _ctx("aws_ec2_CreateKeyPair", {"KeyName": "new-deploy-key"})
    assert evaluate_conditions(p.when, ctx) is True


def test_ec2_keymaterial_exfil_deny_fires_on_public_snapshot_share(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-keymaterial-exfil-deny"]
    ctx = _ctx(
        "aws_ec2_ModifySnapshotAttribute",
        {
            "SnapshotId": "snap-0123456789abcdef0",
            "Attribute": "createVolumePermission",
            "OperationType": "add",
            "GroupNames": ["all"],
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_ec2_keymaterial_exfil_deny_passes_snapshot_shared_to_specific_account(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-ec2-keymaterial-exfil-deny"]
    ctx = _ctx(
        "aws_ec2_ModifySnapshotAttribute",
        {
            "SnapshotId": "snap-0123456789abcdef0",
            "Attribute": "createVolumePermission",
            "OperationType": "add",
            "UserIds": ["123456789012"],
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_ec2_keymaterial_exfil_deny_passes_describe_key_pairs(
    policy_by_id: dict[str, Policy],
) -> None:
    """Read-only, unrelated tool — not matched by tool_name_in, no gated args present."""
    p = policy_by_id["aws-mcp-ec2-keymaterial-exfil-deny"]
    ctx = _ctx("aws_ec2_DescribeKeyPairs", {})
    assert evaluate_conditions(p.when, ctx) is False


# ── SA-5 Vector 11: CloudTrail logging-disable deny ──────────────────────────


def test_cloudtrail_logging_disable_deny_fires_on_stop_logging(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-cloudtrail-logging-disable-deny"]
    ctx = _ctx(
        "aws_cloudtrail_StopLogging",
        {"Name": "arn:aws:cloudtrail:us-east-1:123456789012:trail/prod-trail"},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_cloudtrail_logging_disable_deny_fires_on_delete_trail(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-cloudtrail-logging-disable-deny"]
    ctx = _ctx("aws_cloudtrail_DeleteTrail", {"Name": "prod-trail"})
    assert evaluate_conditions(p.when, ctx) is True


def test_cloudtrail_logging_disable_deny_passes_describe_trails(
    policy_by_id: dict[str, Policy],
) -> None:
    """Read-only DescribeTrails uses TrailNameList, not Name — no gated arg present."""
    p = policy_by_id["aws-mcp-cloudtrail-logging-disable-deny"]
    ctx = _ctx("aws_cloudtrail_DescribeTrails", {"TrailNameList": ["prod-trail"]})
    assert evaluate_conditions(p.when, ctx) is False


# ── SA-5 Vector 12: Lambda public-invoke exposure deny ───────────────────────


def test_lambda_public_invoke_deny_fires_on_wildcard_principal(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-lambda-public-invoke-deny"]
    ctx = _ctx(
        "aws_lambda_AddPermission",
        {
            "FunctionName": "my-function",
            "StatementId": "public-invoke",
            "Action": "lambda:InvokeFunction",
            "Principal": "*",
        },
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_lambda_public_invoke_deny_fires_on_function_url_no_auth(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-lambda-public-invoke-deny"]
    ctx = _ctx(
        "aws_lambda_CreateFunctionUrlConfig",
        {"FunctionName": "my-function", "AuthType": "NONE"},
    )
    assert evaluate_conditions(p.when, ctx) is True


def test_lambda_public_invoke_deny_passes_scoped_service_principal(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-lambda-public-invoke-deny"]
    ctx = _ctx(
        "aws_lambda_AddPermission",
        {
            "FunctionName": "my-function",
            "StatementId": "allow-eventbridge",
            "Action": "lambda:InvokeFunction",
            "Principal": "events.amazonaws.com",
            "SourceArn": "arn:aws:events:us-east-1:123456789012:rule/nightly-job",
        },
    )
    assert evaluate_conditions(p.when, ctx) is False


def test_lambda_public_invoke_deny_passes_function_url_iam_auth(
    policy_by_id: dict[str, Policy],
) -> None:
    p = policy_by_id["aws-mcp-lambda-public-invoke-deny"]
    ctx = _ctx(
        "aws_lambda_CreateFunctionUrlConfig",
        {"FunctionName": "my-function", "AuthType": "AWS_IAM"},
    )
    assert evaluate_conditions(p.when, ctx) is False
