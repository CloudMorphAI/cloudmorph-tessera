"""Mapping of action names → set of intent verbs.

Used by the intent matcher (lexical stage) to determine whether a tool
call's verbs are consistent with the agent's declared intent.

The verb taxonomy is locked at v0.1 — extending requires a major schema
bump. See contracts/intent_declaration.schema.json for the full enum.

CI gate: tests/test_action_verbs_complete.py asserts every action handler
in any executor has a mapping here. New actions without a mapping fail CI.
"""

from __future__ import annotations
import yaml
from pathlib import Path

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
ACTION_VERBS: dict[str, frozenset[str]] = {
    # ── AWS S3 ──
    "aws.s3.list_buckets": _verbs("read.list"),
    "aws.s3.list_objects": _verbs("read.list"),
    "aws.s3.get_object_metadata": _verbs("read.describe", "read.get"),
    "aws.s3.put_object": _verbs("write.create", "write.update"),
    "aws.s3.delete_object": _verbs("write.delete"),
    "aws.s3.delete_bucket": _verbs("write.delete"),
    # ── AWS EC2 ──
    "aws.ec2.list_instances": _verbs("read.list"),
    "aws.ec2.describe_instance": _verbs("read.describe"),
    "aws.ec2.start_instance": _verbs("execute.run"),
    "aws.ec2.stop_instance": _verbs("execute.run"),
    "aws.ec2.terminate_instance": _verbs("write.delete"),
    # ── AWS ECS ──
    "aws.ecs.list_clusters": _verbs("read.list"),
    "aws.ecs.list_services": _verbs("read.list"),
    "aws.ecs.list_tasks": _verbs("read.list"),
    "aws.ecs.run_task": _verbs("execute.run"),
    # ── AWS IAM ──
    "aws.iam.list_users": _verbs("read.list"),
    "aws.iam.list_roles": _verbs("read.list"),
    "aws.iam.list_groups": _verbs("read.list"),
    "aws.iam.create_user": _verbs("write.create"),
    "aws.iam.delete_user": _verbs("write.delete"),
    # ── AWS Lambda ──
    "aws.lambda.list_functions": _verbs("read.list"),
    "aws.lambda.invoke": _verbs("execute.run"),
    # ── AWS misc ──
    "aws.cloudformation.list_stacks": _verbs("read.list"),
    "aws.cloudwatch.list_alarms": _verbs("read.list"),
    "aws.vpc.list_vpcs": _verbs("read.list"),
    "aws.vpc.list_subnets": _verbs("read.list"),
    "aws.vpc.list_security_groups": _verbs("read.list"),
    "aws.rds.list_db_instances": _verbs("read.list"),
    "aws.secretsmanager.list_secrets": _verbs("read.list"),
    "aws.secretsmanager.get_secret_metadata": _verbs("read.describe"),
    "aws.elb.list_load_balancers": _verbs("read.list"),
    # ── GCP Storage ──
    "gcp.storage.list_buckets": _verbs("read.list"),
    "gcp.storage.list_objects": _verbs("read.list"),
    # ── GCP Compute ──
    "gcp.compute.list_instances": _verbs("read.list"),
    "gcp.run.list_jobs": _verbs("read.list"),
    "gcp.run.list_services": _verbs("read.list"),
    "gcp.container.list_clusters": _verbs("read.list"),
    # ── Azure Blob ──
    "azure.blob.list_containers": _verbs("read.list"),
    "azure.blob.list_blobs": _verbs("read.list"),
    # ── Azure Compute ──
    "azure.compute.list_vms": _verbs("read.list"),
    "azure.containerapps.list_apps": _verbs("read.list"),
    "azure.containerapps.list_jobs": _verbs("read.list"),
    # ── Databricks ──
    "databricks.workspace.list_clusters": _verbs("read.list"),
    "databricks.workspace.list_jobs": _verbs("read.list"),
    "databricks.workspace.list_notebooks": _verbs("read.list"),
    "databricks.sql.list_warehouses": _verbs("read.list"),
    "databricks.unity_catalog.list_catalogs": _verbs("read.list"),
    "databricks.unity_catalog.list_schemas": _verbs("read.list"),
    # All possible verbs — actual verbs decided by parsing the SQL
    "databricks.sql.execute_query": _verbs(
        "read.list",
        "read.aggregate",
        "read.search",
        "write.create",
        "write.update",
        "write.delete",
    ),
    # ── Snowflake ──
    "snowflake.account.list_databases": _verbs("read.list"),
    "snowflake.account.list_warehouses": _verbs("read.list"),
    "snowflake.account.list_roles": _verbs("read.list"),
    "snowflake.database.list_schemas": _verbs("read.list"),
    "snowflake.schema.list_tables": _verbs("read.list"),
    "snowflake.sql.execute_query": _verbs(
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
    """
    if action in ACTION_VERBS:
        return ACTION_VERBS[action]
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
            raise ValueError(
                f"Verb list for '{tool_name}' must be a list, got {type(verb_list).__name__}"
            )
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
