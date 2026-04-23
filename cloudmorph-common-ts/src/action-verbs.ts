/**
 * Mirror of cloudmorph_common.action_verbs (Python).
 * Same invariants: 21 known verbs, action → verb-set mapping, polymorphic
 * SQL-execute-query, mcp.proxy.* escalation.
 *
 * Locked at v0.1; extending requires a major IntentDeclaration schemaVersion bump.
 */

export const KNOWN_VERBS: ReadonlySet<string> = new Set([
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
]);

function verbs(...vs: string[]): ReadonlySet<string> {
  for (const v of vs) {
    if (!KNOWN_VERBS.has(v)) {
      throw new Error(`Unknown intent verb: ${v}`);
    }
  }
  return new Set(vs);
}

export const ACTION_VERBS: ReadonlyMap<string, ReadonlySet<string>> = new Map([
  // AWS
  ["aws.s3.list_buckets", verbs("read.list")],
  ["aws.s3.list_objects", verbs("read.list")],
  ["aws.s3.get_object_metadata", verbs("read.describe", "read.get")],
  ["aws.s3.put_object", verbs("write.create", "write.update")],
  ["aws.s3.delete_object", verbs("write.delete")],
  ["aws.s3.delete_bucket", verbs("write.delete")],
  ["aws.ec2.list_instances", verbs("read.list")],
  ["aws.ec2.describe_instance", verbs("read.describe")],
  ["aws.ec2.start_instance", verbs("execute.run")],
  ["aws.ec2.stop_instance", verbs("execute.run")],
  ["aws.ec2.terminate_instance", verbs("write.delete")],
  ["aws.ecs.list_clusters", verbs("read.list")],
  ["aws.ecs.list_services", verbs("read.list")],
  ["aws.ecs.list_tasks", verbs("read.list")],
  ["aws.ecs.run_task", verbs("execute.run")],
  ["aws.iam.list_users", verbs("read.list")],
  ["aws.iam.list_roles", verbs("read.list")],
  ["aws.iam.list_groups", verbs("read.list")],
  ["aws.iam.create_user", verbs("write.create")],
  ["aws.iam.delete_user", verbs("write.delete")],
  ["aws.lambda.list_functions", verbs("read.list")],
  ["aws.lambda.invoke", verbs("execute.run")],
  ["aws.cloudformation.list_stacks", verbs("read.list")],
  ["aws.cloudwatch.list_alarms", verbs("read.list")],
  ["aws.vpc.list_vpcs", verbs("read.list")],
  ["aws.vpc.list_subnets", verbs("read.list")],
  ["aws.vpc.list_security_groups", verbs("read.list")],
  ["aws.rds.list_db_instances", verbs("read.list")],
  ["aws.secretsmanager.list_secrets", verbs("read.list")],
  ["aws.secretsmanager.get_secret_metadata", verbs("read.describe")],
  ["aws.elb.list_load_balancers", verbs("read.list")],

  // GCP
  ["gcp.storage.list_buckets", verbs("read.list")],
  ["gcp.storage.list_objects", verbs("read.list")],
  ["gcp.compute.list_instances", verbs("read.list")],
  ["gcp.run.list_jobs", verbs("read.list")],
  ["gcp.run.list_services", verbs("read.list")],
  ["gcp.container.list_clusters", verbs("read.list")],

  // Azure
  ["azure.blob.list_containers", verbs("read.list")],
  ["azure.blob.list_blobs", verbs("read.list")],
  ["azure.compute.list_vms", verbs("read.list")],
  ["azure.containerapps.list_apps", verbs("read.list")],
  ["azure.containerapps.list_jobs", verbs("read.list")],

  // Databricks
  ["databricks.workspace.list_clusters", verbs("read.list")],
  ["databricks.workspace.list_jobs", verbs("read.list")],
  ["databricks.workspace.list_notebooks", verbs("read.list")],
  ["databricks.sql.list_warehouses", verbs("read.list")],
  ["databricks.unity_catalog.list_catalogs", verbs("read.list")],
  ["databricks.unity_catalog.list_schemas", verbs("read.list")],
  [
    "databricks.sql.execute_query",
    verbs("read.list", "read.aggregate", "read.search", "write.create", "write.update", "write.delete"),
  ],

  // Snowflake
  ["snowflake.account.list_databases", verbs("read.list")],
  ["snowflake.account.list_warehouses", verbs("read.list")],
  ["snowflake.account.list_roles", verbs("read.list")],
  ["snowflake.database.list_schemas", verbs("read.list")],
  ["snowflake.schema.list_tables", verbs("read.list")],
  [
    "snowflake.sql.execute_query",
    verbs("read.list", "read.aggregate", "read.search", "write.create", "write.update", "write.delete"),
  ],

  // mcp.proxy — wraps any downstream MCP tool; matcher special-cases mcp.proxy.*
  ["mcp.proxy", new Set(KNOWN_VERBS)],
]);

export function verbsFor(action: string): ReadonlySet<string> {
  const direct = ACTION_VERBS.get(action);
  if (direct) return direct;
  if (action.startsWith("mcp.proxy.")) {
    return ACTION_VERBS.get("mcp.proxy") ?? new Set<string>();
  }
  return new Set<string>();
}
