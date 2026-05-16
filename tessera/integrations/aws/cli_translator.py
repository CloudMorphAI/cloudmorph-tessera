"""AWS canonical-name ↔ awslabs/mcp/aws-api-mcp-server `call_aws` CLI translator.

Customers who put Tessera in front of the official `awslabs/mcp/aws-api-mcp-server`
get a single `call_aws` tool that accepts CLI command strings. Tessera's policies
are authored against canonical names (aws_ec2_RunInstances). This module bridges
the two directions:

  to_call_aws(canonical, args) → {"tool": "call_aws", "command": "<aws-cli-string>"}
  from_call_aws(args) → canonical name (or None if unrecognized)

Routing mode (per Section 10, Q2 — locked 2026-05-16):
  Default is `specific-first`: use the per-op handler when `official_mcp_tool_name`
  is set, else fall back to `call_aws`. This module only concerns itself with the
  `call_aws` surface; `upstream.py` owns the dispatch decision.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Per-op handler registry: canonical name → callable that builds the CLI string
_HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {}

# Reverse-lookup: "aws <service> <verb>" prefix → canonical name
_REVERSE_PREFIX_MAP: dict[str, str] = {}

# Hand-curated overrides for AWS API's non-standard CamelCase vs derivable forms.
# Keys are kebab-case CLI verbs; values are the API-canonical CamelCase operation names.
# Added when `_kebab_to_camel` would derive the wrong form (e.g. Db vs DB).
_KEBAB_TO_CAMEL_OVERRIDES: dict[str, str] = {
    # RDS — DB stays uppercase
    "create-db-instance": "CreateDBInstance",
    "modify-db-instance": "ModifyDBInstance",
    "delete-db-instance": "DeleteDBInstance",
    "reboot-db-instance": "RebootDBInstance",
    "describe-db-instances": "DescribeDBInstances",
    "create-db-cluster": "CreateDBCluster",
    "modify-db-cluster": "ModifyDBCluster",
    "delete-db-cluster": "DeleteDBCluster",
    "restore-db-instance-from-db-snapshot": "RestoreDBInstanceFromDBSnapshot",
    "create-db-snapshot": "CreateDBSnapshot",
    "delete-db-snapshot": "DeleteDBSnapshot",
    "create-db-parameter-group": "CreateDBParameterGroup",
    "modify-db-parameter-group": "ModifyDBParameterGroup",
    # EC2 — keep consistent where AWS CLI uses lowercase 'nat', 'vpc', 'acl', etc.
    "create-nat-gateway": "CreateNatGateway",
    "delete-nat-gateway": "DeleteNatGateway",
    "create-vpc": "CreateVpc",
    "delete-vpc": "DeleteVpc",
    "create-dhcp-options": "CreateDhcpOptions",
    "associate-dhcp-options": "AssociateDhcpOptions",
    "create-network-acl": "CreateNetworkAcl",
    "delete-network-acl": "DeleteNetworkAcl",
    # IAM
    "create-saml-provider": "CreateSAMLProvider",
    "update-saml-provider": "UpdateSAMLProvider",
    "delete-saml-provider": "DeleteSAMLProvider",
    "get-saml-provider": "GetSAMLProvider",
    "list-saml-providers": "ListSAMLProviders",
    "put-role-policy": "PutRolePolicy",
    # EKS
    "create-eks-anywhere-subscription": "CreateEksAnywhereSubscription",
    # KMS
    "create-custom-key-store": "CreateCustomKeyStore",
    "delete-custom-key-store": "DeleteCustomKeyStore",
    # S3 — BucketPolicy
    "put-bucket-policy": "PutBucketPolicy",
    "get-bucket-policy": "GetBucketPolicy",
    "delete-bucket-policy": "DeleteBucketPolicy",
    "put-bucket-acl": "PutBucketAcl",
    "get-bucket-acl": "GetBucketAcl",
    "put-bucket-cors": "PutBucketCors",
    "get-bucket-cors": "GetBucketCors",
    "put-bucket-lifecycle-configuration": "PutBucketLifecycleConfiguration",
    "put-bucket-notification-configuration": "PutBucketNotificationConfiguration",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _camel_to_kebab(camel: str) -> str:
    """Convert CamelCase to kebab-case.

    Examples::
        InstanceType → instance-type
        DBInstanceIdentifier → db-instance-identifier
        RunInstances → run-instances
    """
    # Insert a hyphen before any uppercase letter that follows a lowercase letter
    # or digit, or before an uppercase letter that is followed by a lowercase letter
    # (handles consecutive capitals like "DB").
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", camel)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", s)
    return s.lower()


def _kebab_to_camel(kebab: str) -> str:
    """Convert kebab-case to CamelCase, checking overrides first.

    Examples::
        run-instances → RunInstances
        create-db-instance → CreateDBInstance (via override)

    The override map handles AWS API names that don't round-trip cleanly through
    simple title-case (e.g. ``DB`` vs derivable ``Db``).
    """
    if kebab in _KEBAB_TO_CAMEL_OVERRIDES:
        return _KEBAB_TO_CAMEL_OVERRIDES[kebab]
    return "".join(word.capitalize() for word in kebab.split("-"))


def _flags_from_args(args: dict[str, Any]) -> str:
    """Generic: serialize args dict as --kebab-key value pairs.

    Skips keys starting with ``_`` (Tessera internal metadata).
    Complex values (dict/list) are JSON-serialised and single-quoted.
    """
    import json
    parts: list[str] = []
    for k, v in args.items():
        if k.startswith("_"):
            continue
        flag = _camel_to_kebab(k)
        if isinstance(v, (dict, list)):
            parts.append(f"--{flag} '{json.dumps(v)}'")
        else:
            parts.append(f"--{flag} {v}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_handler(
    canonical: str,
    cli_prefix: str,
    handler: Callable[[dict[str, Any]], str],
) -> None:
    """Register a forward-translator handler for a canonical op.

    Calling this twice with the same ``canonical`` overwrites the previous
    registration silently — the last registration wins.  This is intentional:
    callers (e.g. plugin packs or tests) should be able to override the
    built-in handlers without ceremony.

    Args:
        canonical: e.g. ``"aws_ec2_RunInstances"``
        cli_prefix: e.g. ``"aws ec2 run-instances"`` — used as the reverse-
                    lookup key so ``from_call_aws`` can reconstruct the
                    canonical name from an inbound command string.
        handler: callable ``(args: dict) → str`` that produces the full CLI
                 command string.
    """
    _HANDLERS[canonical] = handler
    _REVERSE_PREFIX_MAP[cli_prefix] = canonical


def to_call_aws(canonical: str, args: dict[str, Any]) -> dict[str, str] | None:
    """Translate a canonical aws_*_* invocation into a call_aws-shaped request.

    Returns:
        ``{"tool": "call_aws", "command": "aws ec2 run-instances ..."}``
        OR ``None`` when the canonical name has no registered handler (let
        caller decide fallback — e.g., pass-through to the legacy direct-MCP
        path).
    """
    handler = _HANDLERS.get(canonical)
    if handler is None:
        return None
    command = handler(args)
    return {"tool": "call_aws", "command": command}


def from_call_aws(args: dict[str, Any]) -> str | None:
    """Parse a call_aws invocation's command string back to a canonical name.

    Args:
        args: the ``arguments`` dict of the inbound ``call_aws`` tools/call;
              expects ``args["command"]`` to be the CLI string.

    Returns:
        The canonical name (e.g. ``"aws_ec2_RunInstances"``) or ``None`` if
        unrecognized.  Used by matchers.py to make policies authored against
        canonical names still fire when calls come through as ``call_aws``.
    """
    command = args.get("command", "")
    if not isinstance(command, str):
        return None
    tokens = command.split()
    if len(tokens) < 3:
        return None
    if tokens[0] != "aws":
        return None
    service = tokens[1]
    cli_verb = tokens[2]
    # Try exact prefix match first (covers registered explicit handlers)
    prefix = f"aws {service} {cli_verb}"
    if prefix in _REVERSE_PREFIX_MAP:
        return _REVERSE_PREFIX_MAP[prefix]
    # Derive canonical name via kebab→camel conversion (covers generic handlers)
    try:
        operation_camel = _kebab_to_camel(cli_verb)
    except Exception:  # noqa: BLE001
        return None
    canonical = f"aws_{service}_{operation_camel}"
    if canonical in _HANDLERS:
        return canonical
    # Case-insensitive fallback — AWS occasionally uses inconsistent casing
    canonical_lower = canonical.lower()
    for registered in _HANDLERS:
        if registered.lower() == canonical_lower:
            return registered
    return None


# ---------------------------------------------------------------------------
# Generic fallback handler factory
# ---------------------------------------------------------------------------

def _generic_handler(canonical: str) -> Callable[[dict[str, Any]], str]:
    """Build a best-effort handler for ``canonical``.

    Derives ``aws <service> <kebab-verb>`` from the canonical name and passes
    all non-internal args as ``--kebab-key value`` pairs.  Less precise than
    per-op handlers but covers the long tail of ops not worth explicit handlers.

    Returns ``None`` if ``canonical`` doesn't match the ``aws_<svc>_<Op>`` form.
    """
    parts = canonical.split("_", 2)
    if len(parts) != 3:
        return lambda args: ""
    _, service, operation = parts
    cli_verb = _camel_to_kebab(operation)

    def handler(args: dict[str, Any]) -> str:
        flags = _flags_from_args(args)
        base = f"aws {service} {cli_verb}"
        return f"{base} {flags}".strip()

    return handler


# ---------------------------------------------------------------------------
# Priority ops — explicit per-op handlers
# ---------------------------------------------------------------------------

# ---- IAM priv-esc --------------------------------------------------------

def _iam_pass_role(args: dict[str, Any]) -> str:
    parts = ["aws iam pass-role"]
    if "RoleArn" in args:
        parts.append(f"--role-arn {args['RoleArn']}")
    if "RoleSessionName" in args:
        parts.append(f"--role-session-name {args['RoleSessionName']}")
    if "PolicyArns" in args:
        import json
        parts.append(f"--policy-arns '{json.dumps(args['PolicyArns'])}'")
    return " ".join(parts)


def _iam_attach_role_policy(args: dict[str, Any]) -> str:
    parts = ["aws iam attach-role-policy"]
    if "RoleName" in args:
        parts.append(f"--role-name {args['RoleName']}")
    if "PolicyArn" in args:
        parts.append(f"--policy-arn {args['PolicyArn']}")
    return " ".join(parts)


def _iam_put_role_policy(args: dict[str, Any]) -> str:
    import json
    parts = ["aws iam put-role-policy"]
    if "RoleName" in args:
        parts.append(f"--role-name {args['RoleName']}")
    if "PolicyName" in args:
        parts.append(f"--policy-name {args['PolicyName']}")
    if "PolicyDocument" in args:
        doc = args["PolicyDocument"]
        doc_str = json.dumps(doc) if isinstance(doc, dict) else str(doc)
        parts.append(f"--policy-document '{doc_str}'")
    return " ".join(parts)


def _iam_create_access_key(args: dict[str, Any]) -> str:
    parts = ["aws iam create-access-key"]
    if "UserName" in args:
        parts.append(f"--user-name {args['UserName']}")
    return " ".join(parts)


def _iam_create_user(args: dict[str, Any]) -> str:
    parts = ["aws iam create-user"]
    if "UserName" in args:
        parts.append(f"--user-name {args['UserName']}")
    if "Path" in args:
        parts.append(f"--path {args['Path']}")
    if "Tags" in args:
        import json
        parts.append(f"--tags '{json.dumps(args['Tags'])}'")
    return " ".join(parts)


def _iam_create_policy(args: dict[str, Any]) -> str:
    import json
    parts = ["aws iam create-policy"]
    if "PolicyName" in args:
        parts.append(f"--policy-name {args['PolicyName']}")
    if "PolicyDocument" in args:
        doc = args["PolicyDocument"]
        doc_str = json.dumps(doc) if isinstance(doc, dict) else str(doc)
        parts.append(f"--policy-document '{doc_str}'")
    if "Description" in args:
        parts.append(f"--description '{args['Description']}'")
    return " ".join(parts)


def _iam_update_assume_role_policy(args: dict[str, Any]) -> str:
    import json
    parts = ["aws iam update-assume-role-policy"]
    if "RoleName" in args:
        parts.append(f"--role-name {args['RoleName']}")
    if "PolicyDocument" in args:
        doc = args["PolicyDocument"]
        doc_str = json.dumps(doc) if isinstance(doc, dict) else str(doc)
        parts.append(f"--policy-document '{doc_str}'")
    return " ".join(parts)


# ---- KMS -----------------------------------------------------------------

def _kms_schedule_key_deletion(args: dict[str, Any]) -> str:
    parts = ["aws kms schedule-key-deletion"]
    if "KeyId" in args:
        parts.append(f"--key-id {args['KeyId']}")
    if "PendingWindowInDays" in args:
        parts.append(f"--pending-window-in-days {args['PendingWindowInDays']}")
    return " ".join(parts)


def _kms_create_key(args: dict[str, Any]) -> str:
    parts = ["aws kms create-key"]
    if "Description" in args:
        parts.append(f"--description '{args['Description']}'")
    if "KeyUsage" in args:
        parts.append(f"--key-usage {args['KeyUsage']}")
    if "Origin" in args:
        parts.append(f"--origin {args['Origin']}")
    if "Policy" in args:
        import json
        policy = args["Policy"]
        policy_str = json.dumps(policy) if isinstance(policy, dict) else str(policy)
        parts.append(f"--policy '{policy_str}'")
    return " ".join(parts)


# ---- RDS -----------------------------------------------------------------

def _rds_create_db_instance(args: dict[str, Any]) -> str:
    parts = ["aws rds create-db-instance"]
    if "DBInstanceIdentifier" in args:
        parts.append(f"--db-instance-identifier {args['DBInstanceIdentifier']}")
    if "DBInstanceClass" in args:
        parts.append(f"--db-instance-class {args['DBInstanceClass']}")
    if "Engine" in args:
        parts.append(f"--engine {args['Engine']}")
    if "MasterUsername" in args:
        parts.append(f"--master-username {args['MasterUsername']}")
    if "MasterUserPassword" in args:
        parts.append(f"--master-user-password {args['MasterUserPassword']}")
    if "AllocatedStorage" in args:
        parts.append(f"--allocated-storage {args['AllocatedStorage']}")
    if "MultiAZ" in args:
        val = args["MultiAZ"]
        parts.append("--multi-az" if val else "--no-multi-az")
    if "StorageType" in args:
        parts.append(f"--storage-type {args['StorageType']}")
    return " ".join(parts)


def _rds_modify_db_instance(args: dict[str, Any]) -> str:
    parts = ["aws rds modify-db-instance"]
    if "DBInstanceIdentifier" in args:
        parts.append(f"--db-instance-identifier {args['DBInstanceIdentifier']}")
    if "DBInstanceClass" in args:
        parts.append(f"--db-instance-class {args['DBInstanceClass']}")
    if "AllocatedStorage" in args:
        parts.append(f"--allocated-storage {args['AllocatedStorage']}")
    if "ApplyImmediately" in args:
        val = args["ApplyImmediately"]
        parts.append("--apply-immediately" if val else "--no-apply-immediately")
    return " ".join(parts)


def _rds_delete_db_instance(args: dict[str, Any]) -> str:
    parts = ["aws rds delete-db-instance"]
    if "DBInstanceIdentifier" in args:
        parts.append(f"--db-instance-identifier {args['DBInstanceIdentifier']}")
    if "SkipFinalSnapshot" in args:
        val = args["SkipFinalSnapshot"]
        parts.append("--skip-final-snapshot" if val else "--no-skip-final-snapshot")
    if "FinalDBSnapshotIdentifier" in args:
        parts.append(f"--final-db-snapshot-identifier {args['FinalDBSnapshotIdentifier']}")
    return " ".join(parts)


# ---- EC2 -----------------------------------------------------------------

def _ec2_run_instances(args: dict[str, Any]) -> str:
    parts = ["aws ec2 run-instances"]
    if "ImageId" in args:
        parts.append(f"--image-id {args['ImageId']}")
    if "InstanceType" in args:
        parts.append(f"--instance-type {args['InstanceType']}")
    if "MinCount" in args:
        parts.append(f"--min-count {args['MinCount']}")
    if "MaxCount" in args:
        parts.append(f"--max-count {args['MaxCount']}")
    if "SubnetId" in args:
        parts.append(f"--subnet-id {args['SubnetId']}")
    if "SecurityGroupIds" in args:
        ids = args["SecurityGroupIds"]
        if isinstance(ids, list):
            parts.append(f"--security-group-ids {' '.join(ids)}")
        else:
            parts.append(f"--security-group-ids {ids}")
    if "KeyName" in args:
        parts.append(f"--key-name {args['KeyName']}")
    if "IamInstanceProfile" in args:
        import json
        profile = args["IamInstanceProfile"]
        parts.append(f"--iam-instance-profile '{json.dumps(profile)}'")
    if "UserData" in args:
        parts.append(f"--user-data {args['UserData']}")
    return " ".join(parts)


def _ec2_terminate_instances(args: dict[str, Any]) -> str:
    parts = ["aws ec2 terminate-instances"]
    if "InstanceIds" in args:
        ids = args["InstanceIds"]
        if isinstance(ids, list):
            parts.append(f"--instance-ids {' '.join(ids)}")
        else:
            parts.append(f"--instance-ids {ids}")
    return " ".join(parts)


def _ec2_modify_instance_attribute(args: dict[str, Any]) -> str:
    import json
    parts = ["aws ec2 modify-instance-attribute"]
    if "InstanceId" in args:
        parts.append(f"--instance-id {args['InstanceId']}")
    if "InstanceType" in args:
        val = json.dumps({"Value": args["InstanceType"]})
        parts.append(f"--instance-type '{val}'")
    if "Groups" in args:
        groups = args["Groups"]
        if isinstance(groups, list):
            parts.append(f"--groups {' '.join(groups)}")
    if "DisableApiTermination" in args:
        val = json.dumps({"Value": args["DisableApiTermination"]})
        parts.append(f"--disable-api-termination '{val}'")
    return " ".join(parts)


def _ec2_start_instances(args: dict[str, Any]) -> str:
    parts = ["aws ec2 start-instances"]
    if "InstanceIds" in args:
        ids = args["InstanceIds"]
        if isinstance(ids, list):
            parts.append(f"--instance-ids {' '.join(ids)}")
        else:
            parts.append(f"--instance-ids {ids}")
    return " ".join(parts)


def _ec2_stop_instances(args: dict[str, Any]) -> str:
    parts = ["aws ec2 stop-instances"]
    if "InstanceIds" in args:
        ids = args["InstanceIds"]
        if isinstance(ids, list):
            parts.append(f"--instance-ids {' '.join(ids)}")
        else:
            parts.append(f"--instance-ids {ids}")
    if "Force" in args and args["Force"]:
        parts.append("--force")
    return " ".join(parts)


# ---- S3 ------------------------------------------------------------------

def _s3_put_object(args: dict[str, Any]) -> str:
    parts = ["aws s3api put-object"]
    if "Bucket" in args:
        parts.append(f"--bucket {args['Bucket']}")
    if "Key" in args:
        parts.append(f"--key {args['Key']}")
    if "Body" in args:
        parts.append(f"--body {args['Body']}")
    if "StorageClass" in args:
        parts.append(f"--storage-class {args['StorageClass']}")
    if "ContentType" in args:
        parts.append(f"--content-type {args['ContentType']}")
    if "ACL" in args:
        parts.append(f"--acl {args['ACL']}")
    return " ".join(parts)


def _s3_get_object(args: dict[str, Any]) -> str:
    parts = ["aws s3api get-object"]
    if "Bucket" in args:
        parts.append(f"--bucket {args['Bucket']}")
    if "Key" in args:
        parts.append(f"--key {args['Key']}")
    if "VersionId" in args:
        parts.append(f"--version-id {args['VersionId']}")
    return " ".join(parts)


def _s3_delete_object(args: dict[str, Any]) -> str:
    parts = ["aws s3api delete-object"]
    if "Bucket" in args:
        parts.append(f"--bucket {args['Bucket']}")
    if "Key" in args:
        parts.append(f"--key {args['Key']}")
    return " ".join(parts)


def _s3_create_bucket(args: dict[str, Any]) -> str:
    parts = ["aws s3api create-bucket"]
    if "Bucket" in args:
        parts.append(f"--bucket {args['Bucket']}")
    if "CreateBucketConfiguration" in args:
        import json
        config = args["CreateBucketConfiguration"]
        parts.append(f"--create-bucket-configuration '{json.dumps(config)}'")
    if "ACL" in args:
        parts.append(f"--acl {args['ACL']}")
    return " ".join(parts)


def _s3_put_bucket_policy(args: dict[str, Any]) -> str:
    import json
    parts = ["aws s3api put-bucket-policy"]
    if "Bucket" in args:
        parts.append(f"--bucket {args['Bucket']}")
    if "Policy" in args:
        policy = args["Policy"]
        policy_str = json.dumps(policy) if isinstance(policy, dict) else str(policy)
        parts.append(f"--policy '{policy_str}'")
    return " ".join(parts)


# ---- Bedrock -------------------------------------------------------------

def _bedrock_invoke_model(args: dict[str, Any]) -> str:
    import json
    parts = ["aws bedrock-runtime invoke-model"]
    if "modelId" in args:
        parts.append(f"--model-id {args['modelId']}")
    elif "ModelId" in args:
        parts.append(f"--model-id {args['ModelId']}")
    if "body" in args:
        body = args["body"]
        body_str = json.dumps(body) if isinstance(body, dict) else str(body)
        parts.append(f"--body '{body_str}'")
    if "contentType" in args:
        parts.append(f"--content-type {args['contentType']}")
    if "accept" in args:
        parts.append(f"--accept {args['accept']}")
    return " ".join(parts)


def _bedrock_invoke_model_stream(args: dict[str, Any]) -> str:
    import json
    parts = ["aws bedrock-runtime invoke-model-with-response-stream"]
    if "modelId" in args:
        parts.append(f"--model-id {args['modelId']}")
    elif "ModelId" in args:
        parts.append(f"--model-id {args['ModelId']}")
    if "body" in args:
        body = args["body"]
        body_str = json.dumps(body) if isinstance(body, dict) else str(body)
        parts.append(f"--body '{body_str}'")
    return " ".join(parts)


def _bedrock_apply_guardrail(args: dict[str, Any]) -> str:
    import json
    parts = ["aws bedrock-runtime apply-guardrail"]
    if "guardrailIdentifier" in args:
        parts.append(f"--guardrail-identifier {args['guardrailIdentifier']}")
    if "guardrailVersion" in args:
        parts.append(f"--guardrail-version {args['guardrailVersion']}")
    if "source" in args:
        parts.append(f"--source {args['source']}")
    if "content" in args:
        content = args["content"]
        parts.append(f"--content '{json.dumps(content)}'")
    return " ".join(parts)


def _bedrock_create_guardrail(args: dict[str, Any]) -> str:
    import json
    parts = ["aws bedrock create-guardrail"]
    if "name" in args:
        parts.append(f"--name {args['name']}")
    if "description" in args:
        parts.append(f"--description '{args['description']}'")
    if "blockedInputMessaging" in args:
        parts.append(f"--blocked-input-messaging '{args['blockedInputMessaging']}'")
    if "blockedOutputsMessaging" in args:
        parts.append(f"--blocked-outputs-messaging '{args['blockedOutputsMessaging']}'")
    if "contentPolicyConfig" in args:
        parts.append(f"--content-policy-config '{json.dumps(args['contentPolicyConfig'])}'")
    return " ".join(parts)


# ---- Lambda --------------------------------------------------------------

def _lambda_invoke_function(args: dict[str, Any]) -> str:
    import json
    parts = ["aws lambda invoke"]
    if "FunctionName" in args:
        parts.append(f"--function-name {args['FunctionName']}")
    if "InvocationType" in args:
        parts.append(f"--invocation-type {args['InvocationType']}")
    if "Payload" in args:
        payload = args["Payload"]
        payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        parts.append(f"--payload '{payload_str}'")
    if "Qualifier" in args:
        parts.append(f"--qualifier {args['Qualifier']}")
    # aws lambda invoke requires an output file positional arg
    parts.append("response.json")
    return " ".join(parts)


def _lambda_create_function(args: dict[str, Any]) -> str:
    import json
    parts = ["aws lambda create-function"]
    if "FunctionName" in args:
        parts.append(f"--function-name {args['FunctionName']}")
    if "Runtime" in args:
        parts.append(f"--runtime {args['Runtime']}")
    if "Role" in args:
        parts.append(f"--role {args['Role']}")
    if "Handler" in args:
        parts.append(f"--handler {args['Handler']}")
    if "Code" in args:
        code = args["Code"]
        parts.append(f"--code '{json.dumps(code)}'")
    if "Description" in args:
        parts.append(f"--description '{args['Description']}'")
    if "MemorySize" in args:
        parts.append(f"--memory-size {args['MemorySize']}")
    if "Timeout" in args:
        parts.append(f"--timeout {args['Timeout']}")
    return " ".join(parts)


def _lambda_update_function_code(args: dict[str, Any]) -> str:
    parts = ["aws lambda update-function-code"]
    if "FunctionName" in args:
        parts.append(f"--function-name {args['FunctionName']}")
    if "S3Bucket" in args:
        parts.append(f"--s3-bucket {args['S3Bucket']}")
    if "S3Key" in args:
        parts.append(f"--s3-key {args['S3Key']}")
    if "ZipFile" in args:
        parts.append(f"--zip-file {args['ZipFile']}")
    if "ImageUri" in args:
        parts.append(f"--image-uri {args['ImageUri']}")
    if "Publish" in args and args["Publish"]:
        parts.append("--publish")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Register all explicit handlers
# ---------------------------------------------------------------------------

# IAM priv-esc
register_handler("aws_iam_PassRole", "aws iam pass-role", _iam_pass_role)
register_handler("aws_iam_AttachRolePolicy", "aws iam attach-role-policy", _iam_attach_role_policy)
register_handler("aws_iam_PutRolePolicy", "aws iam put-role-policy", _iam_put_role_policy)
register_handler("aws_iam_CreateAccessKey", "aws iam create-access-key", _iam_create_access_key)
register_handler("aws_iam_CreateUser", "aws iam create-user", _iam_create_user)
register_handler("aws_iam_CreatePolicy", "aws iam create-policy", _iam_create_policy)
register_handler("aws_iam_UpdateAssumeRolePolicy", "aws iam update-assume-role-policy", _iam_update_assume_role_policy)

# KMS
register_handler("aws_kms_ScheduleKeyDeletion", "aws kms schedule-key-deletion", _kms_schedule_key_deletion)
register_handler("aws_kms_CreateKey", "aws kms create-key", _kms_create_key)

# RDS
register_handler("aws_rds_CreateDBInstance", "aws rds create-db-instance", _rds_create_db_instance)
register_handler("aws_rds_ModifyDBInstance", "aws rds modify-db-instance", _rds_modify_db_instance)
register_handler("aws_rds_DeleteDBInstance", "aws rds delete-db-instance", _rds_delete_db_instance)

# EC2
register_handler("aws_ec2_RunInstances", "aws ec2 run-instances", _ec2_run_instances)
register_handler("aws_ec2_TerminateInstances", "aws ec2 terminate-instances", _ec2_terminate_instances)
register_handler("aws_ec2_ModifyInstanceAttribute", "aws ec2 modify-instance-attribute", _ec2_modify_instance_attribute)
register_handler("aws_ec2_StartInstances", "aws ec2 start-instances", _ec2_start_instances)
register_handler("aws_ec2_StopInstances", "aws ec2 stop-instances", _ec2_stop_instances)

# S3 (note: S3 API calls use s3api service name in the CLI)
register_handler("aws_s3_PutObject", "aws s3api put-object", _s3_put_object)
register_handler("aws_s3_GetObject", "aws s3api get-object", _s3_get_object)
register_handler("aws_s3_DeleteObject", "aws s3api delete-object", _s3_delete_object)
register_handler("aws_s3_CreateBucket", "aws s3api create-bucket", _s3_create_bucket)
register_handler("aws_s3_PutBucketPolicy", "aws s3api put-bucket-policy", _s3_put_bucket_policy)

# Bedrock (runtime calls use bedrock-runtime service name)
register_handler("aws_bedrock_InvokeModel", "aws bedrock-runtime invoke-model", _bedrock_invoke_model)
register_handler(
    "aws_bedrock_InvokeModelWithResponseStream",
    "aws bedrock-runtime invoke-model-with-response-stream",
    _bedrock_invoke_model_stream,
)
register_handler("aws_bedrock_ApplyGuardrail", "aws bedrock-runtime apply-guardrail", _bedrock_apply_guardrail)
register_handler("aws_bedrock_CreateGuardrail", "aws bedrock create-guardrail", _bedrock_create_guardrail)

# Lambda
register_handler("aws_lambda_InvokeFunction", "aws lambda invoke", _lambda_invoke_function)
register_handler("aws_lambda_CreateFunction", "aws lambda create-function", _lambda_create_function)
register_handler("aws_lambda_UpdateFunctionCode", "aws lambda update-function-code", _lambda_update_function_code)


# ---------------------------------------------------------------------------
# Auto-register generic handlers for the builtin canonical AWS ops.
# ---------------------------------------------------------------------------

def _register_builtin_generics() -> None:
    """Auto-register generic fallback handlers for all BUILTIN_AWS_OPS canonicals."""
    from tessera.cost._aws_canonical_ops import BUILTIN_AWS_OPS  # noqa: PLC0415

    for canonical in BUILTIN_AWS_OPS:
        if canonical not in _HANDLERS:
            parts = canonical.split("_", 2)
            if len(parts) != 3:
                continue
            _, service, operation = parts
            cli_verb = _camel_to_kebab(operation)
            cli_prefix = f"aws {service} {cli_verb}"
            handler = _generic_handler(canonical)
            register_handler(canonical, cli_prefix, handler)


_register_builtin_generics()
