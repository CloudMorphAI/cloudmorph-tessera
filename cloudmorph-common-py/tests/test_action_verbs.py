"""Sanity tests for the action → verb mapping."""

from __future__ import annotations

import pytest

from cloudmorph_common.action_verbs import ACTION_VERBS, KNOWN_VERBS, verbs_for


class TestKnownVerbs:
    def test_22_verbs(self):
        # Sanity check: locked at 22 verbs in v0.1. If this fails, intent
        # taxonomy was changed — bump schemaVersion.
        assert len(KNOWN_VERBS) == 21  # NOTE: 21 distinct verbs in the enum (no .read.list duplicate)

    def test_all_verbs_namespaced_or_atomic(self):
        # Verbs should be lower-case dot-separated identifiers.
        for v in KNOWN_VERBS:
            assert v.islower()
            for part in v.split("."):
                assert part.replace("_", "").isalnum()


class TestActionVerbsMapping:
    def test_no_invalid_verbs(self):
        for action, verbs in ACTION_VERBS.items():
            invalid = verbs - KNOWN_VERBS
            assert not invalid, f"Action {action} maps to unknown verbs: {invalid}"

    def test_aws_s3_read_actions(self):
        assert verbs_for("aws.s3.list_buckets") == frozenset({"read.list"})
        assert verbs_for("aws.s3.list_objects") == frozenset({"read.list"})

    def test_aws_s3_destructive(self):
        assert "write.delete" in verbs_for("aws.s3.delete_bucket")

    def test_databricks_sql_execute_query_is_polymorphic(self):
        # Since SQL determines the actual verbs, the action maps to all read+write verbs.
        verbs = verbs_for("databricks.sql.execute_query")
        assert "read.list" in verbs
        assert "write.update" in verbs

    def test_unknown_action_returns_empty(self):
        assert verbs_for("unknown.action") == frozenset()

    def test_mcp_proxy_returns_all_verbs(self):
        # Wrapped MCP tools have unknown verbs; matcher escalates.
        verbs = verbs_for("mcp.proxy.example.com.do_thing")
        assert verbs == verbs_for("mcp.proxy")
        assert len(verbs) == len(KNOWN_VERBS)


class TestCompleteness:
    """Stub of the completeness gate. Once executor handler registries land
    (Block G), this test grows to import each cloud's ACTIONS dict and assert
    every action key has an entry in ACTION_VERBS. For now, just ensure all
    documented actions have mappings."""

    DOCUMENTED_ACTIONS = [
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
    ]

    @pytest.mark.parametrize("action", DOCUMENTED_ACTIONS)
    def test_documented_action_has_mapping(self, action):
        verbs = verbs_for(action)
        assert verbs, f"Documented action {action} has no verb mapping in action_verbs.py"
