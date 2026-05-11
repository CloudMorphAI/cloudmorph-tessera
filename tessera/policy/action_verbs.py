"""Mapping of action names → set of intent verbs.

Used by the intent matcher (lexical stage) to determine whether a tool
call's verbs are consistent with the agent's declared intent.

The verb taxonomy is locked at v0.1 — extending requires a major schema
bump. See contracts/intent_declaration.schema.json for the full enum.

CI gate: tests/test_action_verbs_complete.py asserts every action handler
in any executor has a mapping here. New actions without a mapping fail CI.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# All known intent verbs. Mirrors the structuredVerbs enum in
# contracts/intent_declaration.schema.json.
KNOWN_VERBS: frozenset[str] = frozenset(
    {
        "read.list",
        "read.describe",
        "read.get",
        "read.search",
        "read.aggregate",
        "analyze",
        "summarize",
        "compare",
        "write.create",
        "write.update",
        "write.delete",
        "execute.run",
        "execute.deploy",
        "notify.send",
        "notify.publish",
        "escalate.approve",
        "escalate.deny",
        "audit.log",
        "audit.export",
        "simulate",
        "dry_run",
    }
)


def _verbs(*verbs: str) -> frozenset[str]:
    """Helper that validates verb names against the known taxonomy."""
    invalid = set(verbs) - KNOWN_VERBS
    if invalid:
        raise ValueError(f"Unknown intent verb(s): {invalid}")
    return frozenset(verbs)


# action → verb-set mapping. Per cloud, organized roughly by service.
#
# Naming convention: underscored, matching the MCP-server convention used by
# the popular open-source MCP servers for AWS / GCP / Azure / Databricks /
# Snowflake (e.g., `aws_s3_list_buckets`, NOT `aws.s3.list_buckets`).
# Custom MCP servers with different naming should add entries via
# `policies/_action_verbs.yaml` (loaded via `load_user_mappings`).
ACTION_VERBS: dict[str, frozenset[str]] = {
    # ── AWS S3 ──
    "aws_s3_list_buckets": _verbs("read.list"),
    "aws_s3_list_objects": _verbs("read.list"),
    "aws_s3_get_object_metadata": _verbs("read.describe", "read.get"),
    "aws_s3_put_object": _verbs("write.create", "write.update"),
    "aws_s3_delete_object": _verbs("write.delete"),
    "aws_s3_delete_bucket": _verbs("write.delete"),
    # ── AWS EC2 ──
    "aws_ec2_list_instances": _verbs("read.list"),
    "aws_ec2_describe_instance": _verbs("read.describe"),
    "aws_ec2_start_instance": _verbs("execute.run"),
    "aws_ec2_stop_instance": _verbs("execute.run"),
    "aws_ec2_terminate_instance": _verbs("write.delete"),
    # ── AWS ECS ──
    "aws_ecs_list_clusters": _verbs("read.list"),
    "aws_ecs_list_services": _verbs("read.list"),
    "aws_ecs_list_tasks": _verbs("read.list"),
    "aws_ecs_run_task": _verbs("execute.run"),
    # ── AWS IAM ──
    "aws_iam_list_users": _verbs("read.list"),
    "aws_iam_list_roles": _verbs("read.list"),
    "aws_iam_list_groups": _verbs("read.list"),
    "aws_iam_create_user": _verbs("write.create"),
    "aws_iam_delete_user": _verbs("write.delete"),
    # ── AWS Lambda ──
    "aws_lambda_list_functions": _verbs("read.list"),
    "aws_lambda_invoke": _verbs("execute.run"),
    # ── AWS misc ──
    "aws_cloudformation_list_stacks": _verbs("read.list"),
    "aws_cloudwatch_list_alarms": _verbs("read.list"),
    "aws_vpc_list_vpcs": _verbs("read.list"),
    "aws_vpc_list_subnets": _verbs("read.list"),
    "aws_vpc_list_security_groups": _verbs("read.list"),
    "aws_rds_list_db_instances": _verbs("read.list"),
    "aws_secretsmanager_list_secrets": _verbs("read.list"),
    "aws_secretsmanager_get_secret_metadata": _verbs("read.describe"),
    "aws_elb_list_load_balancers": _verbs("read.list"),
    # ── GCP Storage ──
    "gcp_storage_list_buckets": _verbs("read.list"),
    "gcp_storage_list_objects": _verbs("read.list"),
    # ── GCP Compute ──
    "gcp_compute_list_instances": _verbs("read.list"),
    "gcp_run_list_jobs": _verbs("read.list"),
    "gcp_run_list_services": _verbs("read.list"),
    "gcp_container_list_clusters": _verbs("read.list"),
    # ── Azure Blob ──
    "azure_blob_list_containers": _verbs("read.list"),
    "azure_blob_list_blobs": _verbs("read.list"),
    # ── Azure Compute ──
    "azure_compute_list_vms": _verbs("read.list"),
    "azure_containerapps_list_apps": _verbs("read.list"),
    "azure_containerapps_list_jobs": _verbs("read.list"),
    # ── Databricks ──
    "databricks_workspace_list_clusters": _verbs("read.list"),
    "databricks_workspace_list_jobs": _verbs("read.list"),
    "databricks_workspace_list_notebooks": _verbs("read.list"),
    "databricks_sql_list_warehouses": _verbs("read.list"),
    "databricks_unity_catalog_list_catalogs": _verbs("read.list"),
    "databricks_unity_catalog_list_schemas": _verbs("read.list"),
    # All possible verbs — actual verbs decided by parsing the SQL
    "databricks_sql_execute_query": _verbs(
        "read.list",
        "read.aggregate",
        "read.search",
        "write.create",
        "write.update",
        "write.delete",
    ),
    # ── Snowflake ──
    "snowflake_account_list_databases": _verbs("read.list"),
    "snowflake_account_list_warehouses": _verbs("read.list"),
    "snowflake_account_list_roles": _verbs("read.list"),
    "snowflake_database_list_schemas": _verbs("read.list"),
    "snowflake_schema_list_tables": _verbs("read.list"),
    "snowflake_sql_execute_query": _verbs(
        "read.list",
        "read.aggregate",
        "read.search",
        "write.create",
        "write.update",
        "write.delete",
    ),
}


def verbs_for(action: str) -> frozenset[str]:
    """Look up the verb set for an action.

    Returns empty frozenset for unknown actions (matcher treats as ambiguous).

    Defensive normalization: also tries the input with `.` → `_` so that
    legacy dotted tool names (e.g., from older fixtures or pre-MCP examples)
    still resolve. Real MCP servers use underscored names; this is a safety
    net, not a contract.
    """
    if action in ACTION_VERBS:
        return ACTION_VERBS[action]
    # Fallback: try normalized form for backwards compatibility.
    normalized = action.replace(".", "_")
    if normalized != action and normalized in ACTION_VERBS:
        return ACTION_VERBS[normalized]
    return frozenset()


def load_user_mappings(path: Path) -> dict[str, frozenset[str]]:
    """Load user-defined action→verb mappings from a YAML file.

    Expected file shape::

        mappings:
          my.custom.tool: [read.list, analyze]
          another.tool: [write.create]

    Raises:
        ValueError: if the file is malformed or contains unknown verbs.
    """
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict) or "mappings" not in data:
        raise ValueError("YAML file must have a top-level 'mappings' key")

    raw_mappings = data["mappings"]
    if not isinstance(raw_mappings, dict):
        raise ValueError("'mappings' must be a dict of tool_name → [verb, ...]")

    result: dict[str, frozenset[str]] = {}
    for tool_name, verb_list in raw_mappings.items():
        if not isinstance(verb_list, list):
            raise ValueError(f"Verb list for '{tool_name}' must be a list, got {type(verb_list).__name__}")
        unknown = set(verb_list) - KNOWN_VERBS
        if unknown:
            raise ValueError(f"Unknown intent verb(s) for '{tool_name}': {unknown}")
        result[str(tool_name)] = frozenset(verb_list)

    return result


def merge_mappings(
    builtin: dict[str, frozenset[str]],
    user: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    """Merge builtin and user mappings; user entries override builtin for the same key.

    Returns a new dict — neither input is mutated.
    """
    return {**builtin, **user}
