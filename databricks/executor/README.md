# CloudMorph Databricks Executor

Polls the Control Centre for jobs targeting Databricks workspaces and executes
them via the Databricks REST API.

## Supported Actions

| Action | Description |
|--------|-------------|
| `databricks.workspace.list_clusters` | List all clusters in a workspace |
| `databricks.workspace.list_jobs` | List all jobs in a workspace |
| `databricks.workspace.list_notebooks` | List notebooks under a path |
| `databricks.sql.list_warehouses` | List SQL warehouses |
| `databricks.unity_catalog.list_catalogs` | List Unity Catalog catalogs |
| `databricks.unity_catalog.list_schemas` | List schemas in a catalog |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CONTROL_CENTER_API_URL` | Yes | Control Centre API base URL |
| `CONTROL_CENTER_EXECUTOR_TOKEN` | Yes | Executor installation token |
| `CONTROL_CENTER_TENANT_ID` | Yes | Tenant ID |
| `CONTROL_CENTER_ACCOUNT_ID` | Yes | Account ID |
| `DATABRICKS_HOST` | Yes | Databricks workspace URL (e.g., `https://adb-1234.azuredatabricks.net`) |
| `DATABRICKS_TOKEN` | Yes* | Personal access token (PAT) |
| `DATABRICKS_CLIENT_ID` | No | OAuth M2M client ID (alternative to PAT) |
| `DATABRICKS_CLIENT_SECRET` | No | OAuth M2M client secret |

## Running

```bash
docker build -t cloudmorph-databricks-executor -f databricks/executor/Dockerfile .
docker run --env-file .env cloudmorph-databricks-executor
```
