"""Databricks executor job runner.

Supports workspace, SQL warehouse, and Unity Catalog operations
via the Databricks REST API.
"""

from typing import Any, Dict, List, Optional
import json
import os
import urllib.parse
import urllib.request


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _resolve_host(payload: Dict[str, Any]) -> str:
    for key in ("host", "workspaceUrl", "databricksHost"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            host = value.strip().rstrip("/")
            if not host.startswith("http"):
                host = f"https://{host}"
            return host
    env_host = os.getenv("DATABRICKS_HOST") or os.getenv("DATABRICKS_WORKSPACE_URL") or ""
    if env_host:
        host = env_host.strip().rstrip("/")
        if not host.startswith("http"):
            host = f"https://{host}"
        return host
    return ""


def _resolve_token(payload: Dict[str, Any]) -> str:
    for key in ("token", "pat", "databricksToken", "accessToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("DATABRICKS_TOKEN") or os.getenv("DATABRICKS_PAT") or ""


def _resolve_max_results(payload: Dict[str, Any], default: int = 100) -> int:
    for key in ("maxResults", "max_results", "limit", "pageSize"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return default


def _resolve_page_token(payload: Dict[str, Any]) -> str:
    value = payload.get("pageToken") or payload.get("nextPageToken") or payload.get("page_token")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _databricks_get(host: str, token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make a GET request to the Databricks REST API."""
    url = f"{host}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None and v != ""}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"databricks_api_error:{exc.code}:{body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"databricks_connection_error:{exc}") from exc


def _list_clusters(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _databricks_get(host, token, "/api/2.0/clusters/list")
    clusters_raw = data.get("clusters") or []
    clusters: List[Dict[str, Any]] = []
    for item in clusters_raw:
        clusters.append({
            "cluster_id": item.get("cluster_id"),
            "cluster_name": item.get("cluster_name"),
            "state": item.get("state"),
            "creator_user_name": item.get("creator_user_name"),
            "spark_version": item.get("spark_version"),
            "node_type_id": item.get("node_type_id"),
            "num_workers": item.get("num_workers"),
            "autotermination_minutes": item.get("autotermination_minutes"),
        })
    return {
        "count": len(clusters),
        "clusters": clusters,
    }


def _list_jobs(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    max_results = _resolve_max_results(payload)
    page_token = _resolve_page_token(payload)
    params: Dict[str, Any] = {"limit": max_results, "expand_tasks": "false"}
    if page_token:
        params["offset"] = page_token
    name_filter = payload.get("name") or payload.get("nameFilter")
    if isinstance(name_filter, str) and name_filter.strip():
        params["name"] = name_filter.strip()

    data = _databricks_get(host, token, "/api/2.1/jobs/list", params)
    jobs_raw = data.get("jobs") or []
    jobs: List[Dict[str, Any]] = []
    for item in jobs_raw:
        settings = item.get("settings") or {}
        jobs.append({
            "job_id": item.get("job_id"),
            "name": settings.get("name"),
            "creator_user_name": item.get("creator_user_name"),
            "created_time": item.get("created_time"),
            "schedule": (settings.get("schedule") or {}).get("quartz_cron_expression"),
        })
    return {
        "count": len(jobs),
        "jobs": jobs,
        "has_more": data.get("has_more", False),
        "nextPageToken": str(data.get("next_page_token") or ""),
    }


def _list_notebooks(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = payload.get("path") or payload.get("notebookPath") or "/"
    data = _databricks_get(host, token, "/api/2.0/workspace/list", {"path": path})
    objects_raw = data.get("objects") or []
    notebooks: List[Dict[str, Any]] = []
    for item in objects_raw:
        notebooks.append({
            "path": item.get("path"),
            "object_type": item.get("object_type"),
            "object_id": item.get("object_id"),
            "language": item.get("language"),
        })
    return {
        "path": path,
        "count": len(notebooks),
        "objects": notebooks,
    }


def _list_sql_warehouses(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _databricks_get(host, token, "/api/2.0/sql/warehouses")
    warehouses_raw = data.get("warehouses") or []
    warehouses: List[Dict[str, Any]] = []
    for item in warehouses_raw:
        warehouses.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "state": item.get("state"),
            "cluster_size": item.get("cluster_size"),
            "min_num_clusters": item.get("min_num_clusters"),
            "max_num_clusters": item.get("max_num_clusters"),
            "auto_stop_mins": item.get("auto_stop_mins"),
            "warehouse_type": item.get("warehouse_type"),
            "creator_name": item.get("creator_name"),
        })
    return {
        "count": len(warehouses),
        "warehouses": warehouses,
    }


def _list_catalogs(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _databricks_get(host, token, "/api/2.1/unity-catalog/catalogs")
    catalogs_raw = data.get("catalogs") or []
    catalogs: List[Dict[str, Any]] = []
    for item in catalogs_raw:
        catalogs.append({
            "name": item.get("name"),
            "owner": item.get("owner"),
            "comment": item.get("comment"),
            "catalog_type": item.get("catalog_type"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        })
    return {
        "count": len(catalogs),
        "catalogs": catalogs,
        "nextPageToken": data.get("next_page_token") or "",
    }


def _list_schemas(host: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    catalog = payload.get("catalog") or payload.get("catalogName") or payload.get("catalog_name")
    if not catalog:
        raise ValueError("catalog_name_required")
    data = _databricks_get(host, token, "/api/2.1/unity-catalog/schemas", {"catalog_name": catalog})
    schemas_raw = data.get("schemas") or []
    schemas: List[Dict[str, Any]] = []
    for item in schemas_raw:
        schemas.append({
            "name": item.get("name"),
            "catalog_name": item.get("catalog_name"),
            "owner": item.get("owner"),
            "comment": item.get("comment"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        })
    return {
        "catalog": catalog,
        "count": len(schemas),
        "schemas": schemas,
        "nextPageToken": data.get("next_page_token") or "",
    }


def _format_error(exc: Exception) -> str:
    msg = str(exc)
    if msg.startswith("databricks_"):
        return msg.split(":", 1)[0]
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

    host = _resolve_host(payload)
    token = _resolve_token(payload)
    log_lines: List[str] = [f"action={action}", f"host={host[:30]}..." if len(host) > 30 else f"host={host}"]

    if not host:
        return _build_result("failed", "Databricks host is required.", reason="host_missing")
    if not token:
        return _build_result("failed", "Databricks token is required.", reason="token_missing")

    try:
        if normalized == "databricks.workspace.list_clusters":
            result = _list_clusters(host, token, payload)
            summary = f"Listed {result.get('count', 0)} clusters."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "databricks.workspace.list_jobs":
            result = _list_jobs(host, token, payload)
            summary = f"Listed {result.get('count', 0)} jobs."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "databricks.workspace.list_notebooks":
            result = _list_notebooks(host, token, payload)
            summary = f"Listed {result.get('count', 0)} objects at {result.get('path', '/')}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "databricks.sql.list_warehouses":
            result = _list_sql_warehouses(host, token, payload)
            summary = f"Listed {result.get('count', 0)} SQL warehouses."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "databricks.unity_catalog.list_catalogs":
            result = _list_catalogs(host, token, payload)
            summary = f"Listed {result.get('count', 0)} catalogs."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "databricks.unity_catalog.list_schemas":
            result = _list_schemas(host, token, payload)
            summary = f"Listed {result.get('count', 0)} schemas in {result.get('catalog', '')}."
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
