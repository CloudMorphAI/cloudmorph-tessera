"""Snowflake executor job runner.

Supports database, warehouse, schema, table, and role listing
via the Snowflake Connector for Python.
"""

from typing import Any, Dict, List, Optional
import os

import snowflake.connector


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _resolve_account(payload: Dict[str, Any]) -> str:
    for key in ("account", "snowflakeAccount", "accountIdentifier"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("SNOWFLAKE_ACCOUNT") or ""


def _resolve_user(payload: Dict[str, Any]) -> str:
    for key in ("user", "username", "snowflakeUser"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("SNOWFLAKE_USER") or ""


def _resolve_password(payload: Dict[str, Any]) -> str:
    for key in ("password", "snowflakePassword"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("SNOWFLAKE_PASSWORD") or ""


def _resolve_warehouse(payload: Dict[str, Any]) -> str:
    for key in ("warehouse", "snowflakeWarehouse"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("SNOWFLAKE_WAREHOUSE") or ""


def _resolve_role(payload: Dict[str, Any]) -> str:
    for key in ("role", "snowflakeRole"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("SNOWFLAKE_ROLE") or ""


def _resolve_database(payload: Dict[str, Any]) -> str:
    for key in ("database", "databaseName", "db"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_schema(payload: Dict[str, Any]) -> str:
    for key in ("schema", "schemaName"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _get_connection(payload: Dict[str, Any]) -> snowflake.connector.SnowflakeConnection:
    """Create a Snowflake connection from payload or environment."""
    account = _resolve_account(payload)
    user = _resolve_user(payload)
    password = _resolve_password(payload)
    warehouse = _resolve_warehouse(payload)
    role = _resolve_role(payload)

    if not account:
        raise ValueError("snowflake_account_required")
    if not user:
        raise ValueError("snowflake_user_required")

    conn_params: Dict[str, Any] = {
        "account": account,
        "user": user,
    }

    # Support key-pair auth
    private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if private_key_path and os.path.exists(private_key_path):
        from cryptography.hazmat.primitives import serialization
        with open(private_key_path, "rb") as key_file:
            p_key = serialization.load_pem_private_key(key_file.read(), password=None)
        pkb = p_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        conn_params["private_key"] = pkb
    elif password:
        conn_params["password"] = password
    else:
        raise ValueError("snowflake_auth_required")

    if warehouse:
        conn_params["warehouse"] = warehouse
    if role:
        conn_params["role"] = role

    return snowflake.connector.connect(**conn_params)


def _query(conn: snowflake.connector.SnowflakeConnection, sql: str) -> List[Dict[str, Any]]:
    """Execute a query and return results as list of dicts."""
    cursor = conn.cursor(snowflake.connector.DictCursor)
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        # Normalize keys to lowercase
        return [{k.lower(): v for k, v in row.items()} for row in rows]
    finally:
        cursor.close()


def _list_databases(conn: snowflake.connector.SnowflakeConnection, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = _query(conn, "SHOW DATABASES")
    databases: List[Dict[str, Any]] = []
    for row in rows:
        databases.append({
            "name": row.get("name"),
            "owner": row.get("owner"),
            "origin": row.get("origin"),
            "created_on": str(row.get("created_on") or ""),
            "retention_time": row.get("retention_time"),
        })
    return {
        "count": len(databases),
        "databases": databases,
    }


def _list_warehouses(conn: snowflake.connector.SnowflakeConnection, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = _query(conn, "SHOW WAREHOUSES")
    warehouses: List[Dict[str, Any]] = []
    for row in rows:
        warehouses.append({
            "name": row.get("name"),
            "state": row.get("state"),
            "type": row.get("type"),
            "size": row.get("size"),
            "owner": row.get("owner"),
            "auto_suspend": row.get("auto_suspend"),
            "auto_resume": row.get("auto_resume"),
            "created_on": str(row.get("created_on") or ""),
        })
    return {
        "count": len(warehouses),
        "warehouses": warehouses,
    }


def _list_schemas(conn: snowflake.connector.SnowflakeConnection, payload: Dict[str, Any]) -> Dict[str, Any]:
    database = _resolve_database(payload)
    if not database:
        raise ValueError("database_required")
    rows = _query(conn, f"SHOW SCHEMAS IN DATABASE \"{database}\"")
    schemas: List[Dict[str, Any]] = []
    for row in rows:
        schemas.append({
            "name": row.get("name"),
            "database_name": row.get("database_name") or database,
            "owner": row.get("owner"),
            "created_on": str(row.get("created_on") or ""),
            "retention_time": row.get("retention_time"),
        })
    return {
        "database": database,
        "count": len(schemas),
        "schemas": schemas,
    }


def _list_tables(conn: snowflake.connector.SnowflakeConnection, payload: Dict[str, Any]) -> Dict[str, Any]:
    database = _resolve_database(payload)
    schema = _resolve_schema(payload)
    if not database:
        raise ValueError("database_required")
    if not schema:
        raise ValueError("schema_required")
    rows = _query(conn, f"SHOW TABLES IN \"{database}\".\"{schema}\"")
    tables: List[Dict[str, Any]] = []
    for row in rows:
        tables.append({
            "name": row.get("name"),
            "database_name": row.get("database_name") or database,
            "schema_name": row.get("schema_name") or schema,
            "kind": row.get("kind"),
            "owner": row.get("owner"),
            "rows": row.get("rows"),
            "created_on": str(row.get("created_on") or ""),
        })
    return {
        "database": database,
        "schema": schema,
        "count": len(tables),
        "tables": tables,
    }


def _list_roles(conn: snowflake.connector.SnowflakeConnection, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = _query(conn, "SHOW ROLES")
    roles: List[Dict[str, Any]] = []
    for row in rows:
        roles.append({
            "name": row.get("name"),
            "owner": row.get("owner"),
            "comment": row.get("comment"),
            "created_on": str(row.get("created_on") or ""),
            "assigned_to_users": row.get("assigned_to_users"),
            "granted_to_roles": row.get("granted_to_roles"),
            "granted_roles": row.get("granted_roles"),
        })
    return {
        "count": len(roles),
        "roles": roles,
    }


def _format_error(exc: Exception) -> str:
    class_name = exc.__class__.__name__
    if "snowflake" in class_name.lower() or "ProgrammingError" in class_name:
        return f"snowflake_error:{class_name}"
    return f"error:{exc}"


def _build_result(
    status: str,
    summary: str,
    result: Optional[Dict[str, Any]] = None,
    logs: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "status": status,
        "artifacts": [],
        "summary": summary,
        "result": result or {},
        "logs": logs,
    }
    if reason:
        output["reason"] = reason
    return output


def run(job: Dict[str, Any]) -> Dict[str, Any]:
    action = _extract_action(job)
    payload = _extract_payload(job)
    normalized = action.lower()

    if not normalized:
        return _build_result("failed", "Missing action.", reason="missing_action")

    if "delete" in normalized or "remove" in normalized or "drop" in normalized:
        return _build_result(
            "failed",
            "Destructive actions are not supported by this executor.",
            reason="destructive_action_not_supported",
        )

    log_lines: List[str] = [f"action={action}"]
    account = _resolve_account(payload)
    log_lines.append(f"account={account or 'env'}")

    conn = None
    try:
        conn = _get_connection(payload)

        if normalized == "snowflake.account.list_databases":
            result = _list_databases(conn, payload)
            summary = f"Listed {result.get('count', 0)} databases."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "snowflake.account.list_warehouses":
            result = _list_warehouses(conn, payload)
            summary = f"Listed {result.get('count', 0)} warehouses."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "snowflake.database.list_schemas":
            result = _list_schemas(conn, payload)
            summary = f"Listed {result.get('count', 0)} schemas in {result.get('database', '')}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "snowflake.schema.list_tables":
            result = _list_tables(conn, payload)
            summary = f"Listed {result.get('count', 0)} tables in {result.get('database', '')}.{result.get('schema', '')}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "snowflake.account.list_roles":
            result = _list_roles(conn, payload)
            summary = f"Listed {result.get('count', 0)} roles."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        return _build_result("failed", "Unsupported action.", reason="unsupported_action")
    except Exception as exc:
        return _build_result(
            "failed",
            "Execution failed.",
            result={"error": str(exc)},
            logs="\n".join(log_lines),
            reason=_format_error(exc),
        )
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
