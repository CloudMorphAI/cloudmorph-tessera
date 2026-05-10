# Read-First demo bundle.
#
# Default deny. Allow only explicit read.* actions across the documented
# action catalog (see docs/getting-started.md).
#
# This is the smoke-test bundle for the OPA WASM engine. The MCP server's
# Block D bench tests its eval against this bundle to validate the < 50ms
# p99 cold target.
#
# Build:
#   opa build -t wasm -e cm/decision -o opa/policy.wasm rules/

package cm.decision

import future.keywords

# ── Default outcome ────────────────────────────────────────────

default outcome := "deny"
default reason := "no_matching_rule"

# ── 1. Tenant lockdown (data flag) ─────────────────────────────

outcome := "deny" if input.tenantSettings.locked == true
reason := "tenant_locked" if input.tenantSettings.locked == true

# ── 2. Read-first allowlist ────────────────────────────────────

read_actions := {
    # AWS
    "aws.s3.list_buckets",
    "aws.s3.list_objects",
    "aws.ec2.list_instances",
    "aws.ecs.list_clusters",
    "aws.ecs.list_services",
    "aws.ecs.list_tasks",
    # GCP
    "gcp.storage.list_buckets",
    "gcp.storage.list_objects",
    "gcp.compute.list_instances",
    "gcp.run.list_jobs",
    "gcp.run.list_services",
    "gcp.container.list_clusters",
    # Azure
    "azure.blob.list_containers",
    "azure.blob.list_blobs",
    "azure.compute.list_vms",
    "azure.containerapps.list_apps",
    "azure.containerapps.list_jobs",
    # Databricks
    "databricks.workspace.list_clusters",
    "databricks.workspace.list_jobs",
    "databricks.workspace.list_notebooks",
    "databricks.sql.list_warehouses",
    "databricks.unity_catalog.list_catalogs",
    "databricks.unity_catalog.list_schemas",
    # Snowflake
    "snowflake.account.list_databases",
    "snowflake.account.list_warehouses",
    "snowflake.database.list_schemas",
    "snowflake.schema.list_tables",
    "snowflake.account.list_roles",
}

outcome := "allow" if {
    input.toolCall.action in read_actions
    not input.tenantSettings.locked
}
reason := "allow_read_first" if {
    input.toolCall.action in read_actions
    not input.tenantSettings.locked
}

# ── 3. Intent-vs-action divergence (when intent declared) ──────

outcome := "deny" if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}
reason := "intent_mismatch" if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}

# ── 4. Explicit destructive denylist (defense in depth) ────────

destructive_actions := {
    "aws.s3.delete_object",
    "aws.s3.delete_bucket",
    "aws.iam.delete_user",
    "aws.ec2.terminate_instance",
}

outcome := "deny" if input.toolCall.action in destructive_actions
reason := "destructive_action_denied" if input.toolCall.action in destructive_actions

# ── Decision evidence (always emitted) ─────────────────────────

matched_rules := result if {
    outcome == "allow"
    result := [{"ruleId": "allow_read_first", "outcome": "allow", "weight": 1.0}]
}
matched_rules := result if {
    outcome == "deny"
    reason == "intent_mismatch"
    result := [{"ruleId": "deny_intent_mismatch", "outcome": "deny", "weight": 1.0}]
}
matched_rules := result if {
    outcome == "deny"
    reason == "destructive_action_denied"
    result := [{"ruleId": "deny_destructive", "outcome": "deny", "weight": 1.0}]
}
matched_rules := result if {
    outcome == "deny"
    reason == "tenant_locked"
    result := [{"ruleId": "tenant_lockdown", "outcome": "deny", "weight": 1.0}]
}
matched_rules := result if {
    outcome == "deny"
    reason == "no_matching_rule"
    result := [{"ruleId": "default_deny", "outcome": "deny", "weight": 1.0}]
}

# ── Top-level decision object ──────────────────────────────────

decision := {
    "outcome": outcome,
    "reason": reason,
    "matchedRules": matched_rules,
}
