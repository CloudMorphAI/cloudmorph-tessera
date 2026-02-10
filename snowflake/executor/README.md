# CloudMorph Snowflake Executor

Polls the Control Centre for jobs targeting Snowflake accounts and executes
them via the Snowflake Connector for Python.

## Supported Actions

| Action | Description |
|--------|-------------|
| `snowflake.account.list_databases` | List all databases |
| `snowflake.account.list_warehouses` | List all warehouses |
| `snowflake.database.list_schemas` | List schemas in a database |
| `snowflake.schema.list_tables` | List tables in a schema |
| `snowflake.account.list_roles` | List all roles |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CONTROL_CENTER_API_URL` | Yes | Control Centre API base URL |
| `CONTROL_CENTER_EXECUTOR_TOKEN` | Yes | Executor installation token |
| `CONTROL_CENTER_TENANT_ID` | Yes | Tenant ID |
| `CONTROL_CENTER_ACCOUNT_ID` | Yes | Account ID |
| `SNOWFLAKE_ACCOUNT` | Yes | Snowflake account identifier (e.g., `org-account`) |
| `SNOWFLAKE_USER` | Yes | Snowflake user |
| `SNOWFLAKE_PASSWORD` | Yes* | User password (or use key pair auth) |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | No | Path to private key file for key-pair auth |
| `SNOWFLAKE_WAREHOUSE` | No | Default warehouse |
| `SNOWFLAKE_ROLE` | No | Default role |

## Running

```bash
docker build -t cloudmorph-snowflake-executor -f snowflake/executor/Dockerfile .
docker run --env-file .env cloudmorph-snowflake-executor
```
