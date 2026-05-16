"""Unit tests for tessera/integrations/aws/cli_translator.py.

Coverage:
  1. Forward translation — 15 hand-crafted fixtures (one per priority op).
  2. Reverse translation — 10 fixtures (command string → canonical name).
  3. Round-trip — from_call_aws(to_call_aws(op, args)) == op for priority ops.
  4. Generic fallback — auto-registered handler for non-explicit ops.
  5. Handler overwrite — register_handler twice cleans up without error.
"""

from __future__ import annotations

import warnings

import pytest

# Suppress DeprecationWarning from aws_mapping import at collection time
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from tessera.integrations.aws.cli_translator import (
        _camel_to_kebab,
        _kebab_to_camel,
        from_call_aws,
        register_handler,
        to_call_aws,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cmd(result: dict | None) -> str:
    """Extract command string from to_call_aws result."""
    assert result is not None, "to_call_aws returned None unexpectedly"
    assert result["tool"] == "call_aws"
    return result["command"]


# ---------------------------------------------------------------------------
# 1. Forward translation — 15 explicit-handler fixtures
# ---------------------------------------------------------------------------

class TestForwardTranslation:
    def test_iam_pass_role(self) -> None:
        result = to_call_aws(
            "aws_iam_PassRole",
            {
                "RoleArn": "arn:aws:iam::123456789012:role/AdminRole",
                "RoleSessionName": "test-session",
            },
        )
        cmd = _cmd(result)
        assert "aws iam pass-role" in cmd
        assert "--role-arn arn:aws:iam::123456789012:role/AdminRole" in cmd
        assert "--role-session-name test-session" in cmd

    def test_iam_attach_role_policy(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_iam_AttachRolePolicy",
                {
                    "RoleName": "MyRole",
                    "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
                },
            )
        )
        assert "aws iam attach-role-policy" in cmd
        assert "--role-name MyRole" in cmd
        assert "--policy-arn arn:aws:iam::aws:policy/AdministratorAccess" in cmd

    def test_iam_put_role_policy(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_iam_PutRolePolicy",
                {
                    "RoleName": "MyRole",
                    "PolicyName": "InlinePolicy",
                    "PolicyDocument": {"Version": "2012-10-17", "Statement": []},
                },
            )
        )
        assert "aws iam put-role-policy" in cmd
        assert "--role-name MyRole" in cmd
        assert "--policy-name InlinePolicy" in cmd
        assert "--policy-document" in cmd

    def test_iam_create_access_key(self) -> None:
        cmd = _cmd(to_call_aws("aws_iam_CreateAccessKey", {"UserName": "alice"}))
        assert "aws iam create-access-key" in cmd
        assert "--user-name alice" in cmd

    def test_iam_create_user(self) -> None:
        cmd = _cmd(to_call_aws("aws_iam_CreateUser", {"UserName": "bob", "Path": "/service/"}))
        assert "aws iam create-user" in cmd
        assert "--user-name bob" in cmd
        assert "--path /service/" in cmd

    def test_iam_create_policy(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_iam_CreatePolicy",
                {
                    "PolicyName": "MyPolicy",
                    "PolicyDocument": {"Version": "2012-10-17", "Statement": []},
                },
            )
        )
        assert "aws iam create-policy" in cmd
        assert "--policy-name MyPolicy" in cmd

    def test_iam_update_assume_role_policy(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_iam_UpdateAssumeRolePolicy",
                {
                    "RoleName": "MyRole",
                    "PolicyDocument": {"Version": "2012-10-17"},
                },
            )
        )
        assert "aws iam update-assume-role-policy" in cmd
        assert "--role-name MyRole" in cmd

    def test_kms_schedule_key_deletion(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_kms_ScheduleKeyDeletion",
                {"KeyId": "arn:aws:kms:us-east-1:123:key/abc", "PendingWindowInDays": 30},
            )
        )
        assert "aws kms schedule-key-deletion" in cmd
        assert "--key-id arn:aws:kms:us-east-1:123:key/abc" in cmd
        assert "--pending-window-in-days 30" in cmd

    def test_rds_create_db_instance(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_rds_CreateDBInstance",
                {
                    "DBInstanceIdentifier": "mydb",
                    "DBInstanceClass": "db.t3.micro",
                    "Engine": "mysql",
                    "MasterUsername": "admin",
                    "AllocatedStorage": 20,
                    "MultiAZ": False,
                },
            )
        )
        assert "aws rds create-db-instance" in cmd
        assert "--db-instance-identifier mydb" in cmd
        assert "--db-instance-class db.t3.micro" in cmd
        assert "--engine mysql" in cmd
        assert "--no-multi-az" in cmd

    def test_ec2_run_instances(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_ec2_RunInstances",
                {
                    "ImageId": "ami-0abcdef1234567890",
                    "InstanceType": "m5.large",
                    "MinCount": 1,
                    "MaxCount": 2,
                    "SubnetId": "subnet-12345",
                },
            )
        )
        assert "aws ec2 run-instances" in cmd
        assert "--image-id ami-0abcdef1234567890" in cmd
        assert "--instance-type m5.large" in cmd
        assert "--min-count 1" in cmd
        assert "--max-count 2" in cmd
        assert "--subnet-id subnet-12345" in cmd

    def test_ec2_terminate_instances(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_ec2_TerminateInstances",
                {"InstanceIds": ["i-0abc123", "i-0def456"]},
            )
        )
        assert "aws ec2 terminate-instances" in cmd
        assert "i-0abc123" in cmd
        assert "i-0def456" in cmd

    def test_s3_put_object(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_s3_PutObject",
                {"Bucket": "my-bucket", "Key": "path/to/file.txt", "StorageClass": "STANDARD"},
            )
        )
        assert "aws s3api put-object" in cmd
        assert "--bucket my-bucket" in cmd
        assert "--key path/to/file.txt" in cmd
        assert "--storage-class STANDARD" in cmd

    def test_s3_put_bucket_policy(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_s3_PutBucketPolicy",
                {
                    "Bucket": "my-bucket",
                    "Policy": {"Version": "2012-10-17", "Statement": []},
                },
            )
        )
        assert "aws s3api put-bucket-policy" in cmd
        assert "--bucket my-bucket" in cmd
        assert "--policy" in cmd

    def test_bedrock_invoke_model(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_bedrock_InvokeModel",
                {
                    "modelId": "anthropic.claude-3-sonnet-20240229-v1:0",
                    "body": {"prompt": "hello"},
                },
            )
        )
        assert "aws bedrock-runtime invoke-model" in cmd
        assert "--model-id anthropic.claude-3-sonnet-20240229-v1:0" in cmd

    def test_lambda_invoke_function(self) -> None:
        cmd = _cmd(
            to_call_aws(
                "aws_lambda_InvokeFunction",
                {
                    "FunctionName": "my-function",
                    "InvocationType": "RequestResponse",
                    "Payload": {"key": "value"},
                },
            )
        )
        assert "aws lambda invoke" in cmd
        assert "--function-name my-function" in cmd
        assert "--invocation-type RequestResponse" in cmd


# ---------------------------------------------------------------------------
# 2. Reverse translation — 10 fixtures
# ---------------------------------------------------------------------------

class TestReverseTranslation:
    def test_iam_pass_role(self) -> None:
        result = from_call_aws(
            {"command": "aws iam pass-role --role-arn arn:aws:iam::123456789012:role/AdminRole"}
        )
        assert result == "aws_iam_PassRole"

    def test_ec2_run_instances(self) -> None:
        result = from_call_aws(
            {"command": "aws ec2 run-instances --instance-type m5.large --image-id ami-abc"}
        )
        assert result == "aws_ec2_RunInstances"

    def test_iam_create_access_key(self) -> None:
        result = from_call_aws({"command": "aws iam create-access-key --user-name alice"})
        assert result == "aws_iam_CreateAccessKey"

    def test_kms_schedule_key_deletion(self) -> None:
        result = from_call_aws(
            {"command": "aws kms schedule-key-deletion --key-id arn:aws:kms:us-east-1:123:key/abc"}
        )
        assert result == "aws_kms_ScheduleKeyDeletion"

    def test_rds_create_db_instance(self) -> None:
        # This tests that the override map handles CreateDBInstance (not CreateDbInstance)
        result = from_call_aws(
            {"command": "aws rds create-db-instance --db-instance-identifier mydb"}
        )
        assert result == "aws_rds_CreateDBInstance"

    def test_lambda_invoke_function(self) -> None:
        result = from_call_aws({"command": "aws lambda invoke --function-name my-fn response.json"})
        assert result == "aws_lambda_InvokeFunction"

    def test_s3_put_bucket_policy(self) -> None:
        result = from_call_aws(
            {"command": "aws s3api put-bucket-policy --bucket my-bucket --policy '{}'"}
        )
        assert result == "aws_s3_PutBucketPolicy"

    def test_bedrock_invoke_model(self) -> None:
        result = from_call_aws(
            {"command": "aws bedrock-runtime invoke-model --model-id anthropic.claude-3"}
        )
        assert result == "aws_bedrock_InvokeModel"

    def test_unknown_service_returns_none(self) -> None:
        result = from_call_aws({"command": "aws frobnicate do-thing"})
        assert result is None

    def test_non_aws_prefix_returns_none(self) -> None:
        result = from_call_aws({"command": "gcloud compute instances list"})
        assert result is None

    def test_too_short_command_returns_none(self) -> None:
        result = from_call_aws({"command": "aws iam"})
        assert result is None

    def test_missing_command_key_returns_none(self) -> None:
        result = from_call_aws({})
        assert result is None


# ---------------------------------------------------------------------------
# 3. Round-trip tests — from_call_aws(to_call_aws(op, args)) == op
# ---------------------------------------------------------------------------

_ROUND_TRIP_FIXTURES: list[tuple[str, dict]] = [
    ("aws_iam_PassRole", {"RoleArn": "arn:aws:iam::123:role/R", "RoleSessionName": "s"}),
    ("aws_iam_AttachRolePolicy", {"RoleName": "R", "PolicyArn": "arn:aws:iam::aws:policy/P"}),
    ("aws_iam_PutRolePolicy", {"RoleName": "R", "PolicyName": "P", "PolicyDocument": {}}),
    ("aws_iam_CreateAccessKey", {"UserName": "alice"}),
    ("aws_iam_CreateUser", {"UserName": "bob"}),
    ("aws_iam_CreatePolicy", {"PolicyName": "P", "PolicyDocument": {}}),
    ("aws_iam_UpdateAssumeRolePolicy", {"RoleName": "R", "PolicyDocument": {}}),
    ("aws_kms_ScheduleKeyDeletion", {"KeyId": "key-1", "PendingWindowInDays": 7}),
    ("aws_kms_CreateKey", {"Description": "test"}),
    ("aws_rds_CreateDBInstance", {"DBInstanceIdentifier": "mydb", "Engine": "mysql"}),
    ("aws_rds_ModifyDBInstance", {"DBInstanceIdentifier": "mydb"}),
    ("aws_rds_DeleteDBInstance", {"DBInstanceIdentifier": "mydb", "SkipFinalSnapshot": True}),
    ("aws_ec2_RunInstances", {"ImageId": "ami-1", "InstanceType": "t3.micro", "MinCount": 1, "MaxCount": 1}),
    ("aws_ec2_TerminateInstances", {"InstanceIds": ["i-1"]}),
    ("aws_ec2_StartInstances", {"InstanceIds": ["i-1"]}),
    ("aws_ec2_StopInstances", {"InstanceIds": ["i-1"]}),
    ("aws_s3_PutObject", {"Bucket": "b", "Key": "k"}),
    ("aws_s3_GetObject", {"Bucket": "b", "Key": "k"}),
    ("aws_s3_DeleteObject", {"Bucket": "b", "Key": "k"}),
    ("aws_s3_CreateBucket", {"Bucket": "b"}),
    ("aws_s3_PutBucketPolicy", {"Bucket": "b", "Policy": {}}),
    ("aws_bedrock_InvokeModel", {"modelId": "m"}),
    ("aws_lambda_InvokeFunction", {"FunctionName": "f"}),
    ("aws_lambda_CreateFunction", {"FunctionName": "f", "Runtime": "python3.12", "Role": "r", "Handler": "h"}),
    ("aws_lambda_UpdateFunctionCode", {"FunctionName": "f", "S3Bucket": "b", "S3Key": "k"}),
]


class TestRoundTrip:
    @pytest.mark.parametrize("canonical,args", _ROUND_TRIP_FIXTURES)
    def test_round_trip(self, canonical: str, args: dict) -> None:
        fwd = to_call_aws(canonical, args)
        assert fwd is not None, f"to_call_aws returned None for {canonical}"
        recovered = from_call_aws(fwd)
        assert recovered == canonical, (
            f"Round-trip failed for {canonical}: "
            f"command={fwd['command']!r}, recovered={recovered!r}"
        )


# ---------------------------------------------------------------------------
# 4. Generic fallback
# ---------------------------------------------------------------------------

class TestGenericFallback:
    def test_builtin_mapping_op_glue_start_crawler(self) -> None:
        """Auto-registered generic handler covers ops not in the priority 25."""
        # aws_glue_StartCrawler is not in the priority list but should be
        # auto-registered via _register_builtin_generics if it were in _BUILTIN_MAPPING,
        # OR resolved generically if registered via register_handler at import time.
        # For this test we explicitly register one to simulate the generic path.
        _called: list[str] = []

        def _fake_handler(args: dict) -> str:
            cmd = f"aws glue start-crawler"
            if "Name" in args:
                cmd += f" --name {args['Name']}"
            _called.append(cmd)
            return cmd

        register_handler("aws_glue_StartCrawler", "aws glue start-crawler", _fake_handler)
        result = to_call_aws("aws_glue_StartCrawler", {"Name": "my-crawler"})
        assert result is not None
        assert result["tool"] == "call_aws"
        assert "aws glue start-crawler" in result["command"]
        assert "--name my-crawler" in result["command"]

    def test_generic_forward_for_ec2_create_nat_gateway(self) -> None:
        """aws_ec2_CreateNatGateway is in _BUILTIN_MAPPING — should be auto-registered."""
        result = to_call_aws("aws_ec2_CreateNatGateway", {"SubnetId": "subnet-1"})
        assert result is not None
        cmd = result["command"]
        # Generic handler derives "aws ec2 create-nat-gateway"
        assert "aws ec2 create-nat-gateway" in cmd

    def test_generic_unregistered_returns_none(self) -> None:
        """An op with no registration whatsoever returns None."""
        result = to_call_aws("aws_completely_UnknownOp99999", {})
        assert result is None

    def test_generic_eks_create_cluster(self) -> None:
        """aws_eks_CreateCluster is in _BUILTIN_MAPPING — auto-registered."""
        result = to_call_aws("aws_eks_CreateCluster", {"Name": "my-cluster"})
        assert result is not None
        assert "aws eks create-cluster" in result["command"]


# ---------------------------------------------------------------------------
# 5. No double registration / overwrite behaviour
# ---------------------------------------------------------------------------

class TestHandlerOverwrite:
    def test_double_register_overwrites(self) -> None:
        """Registering the same canonical twice should silently overwrite."""
        canonical = "aws_test_OverwriteOp"
        register_handler(canonical, "aws test overwrite-op", lambda args: "first")
        register_handler(canonical, "aws test overwrite-op", lambda args: "second")
        result = to_call_aws(canonical, {})
        assert result is not None
        assert result["command"] == "second"


# ---------------------------------------------------------------------------
# 6. Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelpers:
    @pytest.mark.parametrize(
        "camel,expected",
        [
            ("InstanceType", "instance-type"),
            ("DBInstanceIdentifier", "db-instance-identifier"),
            ("RunInstances", "run-instances"),
            ("MaxCount", "max-count"),
            ("InvokeModelWithResponseStream", "invoke-model-with-response-stream"),
            ("CreateDBInstance", "create-db-instance"),
            ("PutBucketPolicy", "put-bucket-policy"),
        ],
    )
    def test_camel_to_kebab(self, camel: str, expected: str) -> None:
        assert _camel_to_kebab(camel) == expected

    @pytest.mark.parametrize(
        "kebab,expected",
        [
            ("run-instances", "RunInstances"),
            ("create-db-instance", "CreateDBInstance"),
            ("modify-db-instance", "ModifyDBInstance"),
            ("delete-db-instance", "DeleteDBInstance"),
            ("invoke-model", "InvokeModel"),
            ("create-function", "CreateFunction"),
            ("schedule-key-deletion", "ScheduleKeyDeletion"),
        ],
    )
    def test_kebab_to_camel(self, kebab: str, expected: str) -> None:
        assert _kebab_to_camel(kebab) == expected

    def test_to_call_aws_returns_correct_shape(self) -> None:
        result = to_call_aws("aws_iam_CreateUser", {"UserName": "charlie"})
        assert result is not None
        assert set(result.keys()) == {"tool", "command"}
        assert result["tool"] == "call_aws"
        assert isinstance(result["command"], str)
        assert len(result["command"]) > 0
