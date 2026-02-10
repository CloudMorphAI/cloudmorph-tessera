from typing import Any, Dict, List, Optional
import json
import os
import urllib.parse
import urllib.request

from azure.storage.blob import BlobServiceClient

try:
    from azure.core.exceptions import AzureError
except Exception:  # pragma: no cover - optional import
    AzureError = Exception  # type: ignore

from storage_pointers import build_pointer

CONTAINER_APPS_API_VERSION = "2023-05-01"
COMPUTE_API_VERSION = "2023-07-01"


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _resolve_account(payload: Dict[str, Any]) -> str:
    for key in ("account", "accountName", "storageAccount", "storageAccountName"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("STORAGE_ACCOUNT")
        or os.getenv("AZURE_STORAGE_ACCOUNT")
        or ""
    )


def _resolve_subscription(payload: Dict[str, Any]) -> str:
    for key in ("subscriptionId", "subscription_id", "subscription"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("AZURE_SUBSCRIPTION_ID") or ""


def _resolve_resource_group(payload: Dict[str, Any]) -> str:
    for key in ("resourceGroup", "resource_group", "rg"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("AZURE_RESOURCE_GROUP") or ""


def _resolve_container(payload: Dict[str, Any]) -> str:
    for key in ("container", "containerName", "storageContainer"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("STORAGE_CONTAINER")
        or os.getenv("AZURE_STORAGE_CONTAINER")
        or ""
    )


def _resolve_prefix(payload: Dict[str, Any]) -> str:
    for key in ("prefix", "keyPrefix", "blobPrefix", "namePrefix"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_max_results(payload: Dict[str, Any], default: int = 1000) -> int:
    for key in ("maxResults", "max_results", "maxKeys", "max_keys", "pageSize"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return default


def _resolve_connection_string(payload: Dict[str, Any]) -> str:
    for key in ("connectionString", "storageConnectionString"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        or os.getenv("STORAGE_CONNECTION_STRING")
        or ""
    )


def _resolve_sas_token(payload: Dict[str, Any]) -> str:
    for key in ("sasToken", "storageSasToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("STORAGE_SAS_TOKEN")
        or os.getenv("AZURE_STORAGE_SAS_TOKEN")
        or ""
    )


def _resolve_account_key(payload: Dict[str, Any]) -> str:
    for key in ("accountKey", "storageAccountKey"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.getenv("AZURE_STORAGE_ACCOUNT_KEY") or ""


def _resolve_access_token(payload: Dict[str, Any]) -> str:
    for key in ("accessToken", "token", "azureAccessToken", "managementToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("AZURE_ACCESS_TOKEN")
        or os.getenv("CONTROL_CENTER_AZURE_TOKEN")
        or os.getenv("AZURE_BEARER_TOKEN")
        or ""
    )


def _format_error(exc: Exception) -> str:
    if isinstance(exc, AzureError):
        return f"azure_error:{exc.__class__.__name__}"
    return f"error:{exc}"


def _request_json(url: str, token: str) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8") or "{}")
    except Exception as exc:  # pragma: no cover - safety net
        raise RuntimeError(f"azure_http_error:{exc}") from exc


def _request_paged(url: str, token: str, max_results: int) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    next_link = url
    while next_link:
        data = _request_json(next_link, token)
        values = data.get("value") if isinstance(data.get("value"), list) else []
        if values:
            items.extend(values)
        next_link = data.get("nextLink")
        if max_results and len(items) >= max_results:
            items = items[:max_results]
            break
    return {"items": items, "nextLink": next_link}


def _resource_group_from_id(resource_id: Optional[str]) -> str:
    if not resource_id:
        return ""
    lowered = resource_id.lower()
    marker = "/resourcegroups/"
    if marker in lowered:
        start = lowered.find(marker) + len(marker)
        remainder = resource_id[start:]
        return remainder.split("/", 1)[0]
    return ""


def _build_service_client(payload: Dict[str, Any]) -> BlobServiceClient:
    conn_str = _resolve_connection_string(payload)
    if conn_str:
        return BlobServiceClient.from_connection_string(conn_str)

    account = _resolve_account(payload)
    account_url = payload.get("accountUrl") or payload.get("storageAccountUrl")
    if not isinstance(account_url, str) or not account_url.strip():
        account_url = f"https://{account}.blob.core.windows.net" if account else ""

    sas = _resolve_sas_token(payload)
    if account_url and sas:
        return BlobServiceClient(account_url=account_url, credential=sas.lstrip("?"))

    account_key = _resolve_account_key(payload)
    if account_url and account_key:
        return BlobServiceClient(account_url=account_url, credential=account_key)

    raise RuntimeError("storage_credentials_missing")


def _list_virtual_machines(payload: Dict[str, Any]) -> Dict[str, Any]:
    subscription_id = _resolve_subscription(payload)
    resource_group = _resolve_resource_group(payload)
    token = _resolve_access_token(payload)
    if not subscription_id:
        raise ValueError("subscription_missing")
    if not token:
        raise RuntimeError("access_token_missing")

    if resource_group:
        base = f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Compute/virtualMachines"
    else:
        base = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines"
    url = f"{base}?{urllib.parse.urlencode({'api-version': COMPUTE_API_VERSION})}"
    max_results = _resolve_max_results(payload)
    page = _request_paged(url, token, max_results)
    vms: List[Dict[str, Any]] = []
    for item in page.get("items", []):
        if not isinstance(item, dict):
            continue
        vm_id = item.get("id")
        vms.append(
            {
                "name": item.get("name"),
                "location": item.get("location"),
                "resourceGroup": _resource_group_from_id(vm_id),
                "id": vm_id,
                "vmSize": (item.get("properties") or {}).get("hardwareProfile", {}).get("vmSize"),
                "provisioningState": (item.get("properties") or {}).get("provisioningState"),
            }
        )
    return {
        "subscriptionId": subscription_id,
        "resourceGroup": resource_group or None,
        "count": len(vms),
        "virtualMachines": vms,
        "nextLink": page.get("nextLink"),
    }


def _list_containerapps(payload: Dict[str, Any]) -> Dict[str, Any]:
    subscription_id = _resolve_subscription(payload)
    resource_group = _resolve_resource_group(payload)
    token = _resolve_access_token(payload)
    if not subscription_id:
        raise ValueError("subscription_missing")
    if not token:
        raise RuntimeError("access_token_missing")

    if resource_group:
        base = f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.App/containerApps"
    else:
        base = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.App/containerApps"
    url = f"{base}?{urllib.parse.urlencode({'api-version': CONTAINER_APPS_API_VERSION})}"
    max_results = _resolve_max_results(payload)
    page = _request_paged(url, token, max_results)
    apps: List[Dict[str, Any]] = []
    for item in page.get("items", []):
        if not isinstance(item, dict):
            continue
        app_id = item.get("id")
        apps.append(
            {
                "name": item.get("name"),
                "location": item.get("location"),
                "resourceGroup": _resource_group_from_id(app_id),
                "id": app_id,
                "provisioningState": (item.get("properties") or {}).get("provisioningState"),
            }
        )
    return {
        "subscriptionId": subscription_id,
        "resourceGroup": resource_group or None,
        "count": len(apps),
        "apps": apps,
        "nextLink": page.get("nextLink"),
    }


def _list_containers(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = _build_service_client(payload)
    prefix = _resolve_prefix(payload)
    max_results = _resolve_max_results(payload)
    containers: List[Dict[str, Any]] = []
    truncated = False
    for container in client.list_containers(name_starts_with=prefix or None):
        name = getattr(container, "name", None) or (container.get("name") if isinstance(container, dict) else None)
        last_modified = getattr(container, "last_modified", None) or (
            container.get("last_modified") if isinstance(container, dict) else None
        )
        containers.append(
            {
                "name": name or "",
                "lastModified": str(last_modified or ""),
            }
        )
        if len(containers) >= max_results:
            truncated = True
            break
    return {
        "account": _resolve_account(payload),
        "prefix": prefix,
        "count": len(containers),
        "containers": containers,
        "isTruncated": truncated,
    }


def _list_blobs(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = _build_service_client(payload)
    container = _resolve_container(payload)
    if not container:
        raise ValueError("container_missing")
    prefix = _resolve_prefix(payload)
    max_results = _resolve_max_results(payload)
    container_client = client.get_container_client(container)
    blobs: List[Dict[str, Any]] = []
    truncated = False
    for blob in container_client.list_blobs(name_starts_with=prefix or None):
        name = getattr(blob, "name", None) or (blob.get("name") if isinstance(blob, dict) else None)
        size = getattr(blob, "size", None) or (blob.get("size") if isinstance(blob, dict) else None)
        last_modified = getattr(blob, "last_modified", None) or (
            blob.get("last_modified") if isinstance(blob, dict) else None
        )
        blobs.append(
            {
                "name": name or "",
                "size": size,
                "lastModified": str(last_modified or ""),
            }
        )
        if len(blobs) >= max_results:
            truncated = True
            break
    return {
        "account": _resolve_account(payload),
        "container": container,
        "prefix": prefix,
        "count": len(blobs),
        "blobs": blobs,
        "isTruncated": truncated,
    }


def _list_containerapp_jobs(payload: Dict[str, Any]) -> Dict[str, Any]:
    subscription_id = _resolve_subscription(payload)
    resource_group = _resolve_resource_group(payload)
    token = _resolve_access_token(payload)
    if not subscription_id:
        raise ValueError("subscription_missing")
    if not resource_group:
        raise ValueError("resource_group_missing")
    if not token:
        raise RuntimeError("access_token_missing")

    base = f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.App/jobs"
    url = f"{base}?{urllib.parse.urlencode({'api-version': '2023-05-01'})}"
    data = _request_json(url, token)
    items = data.get("value") if isinstance(data.get("value"), list) else []
    jobs: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            jobs.append(
                {
                    "name": item.get("name"),
                    "location": item.get("location"),
                    "id": item.get("id"),
                }
            )
    return {
        "subscriptionId": subscription_id,
        "resourceGroup": resource_group,
        "count": len(jobs),
        "jobs": jobs,
        "nextLink": data.get("nextLink"),
    }


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _build_result(
    status: str,
    summary: str,
    result: Optional[Dict[str, Any]] = None,
    logs: Optional[str] = None,
    reason: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "status": status,
        "artifacts": artifacts or [],
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

    artifacts = [
        build_pointer("azure:blob", uri, {"source": "payload"})
        for uri in _as_list(payload.get("artifactUri") or payload.get("artifactUris"))
        if uri
    ]

    if not normalized:
        return _build_result("failed", "Missing action.", reason="missing_action", artifacts=artifacts)

    if "delete" in normalized or "remove" in normalized:
        return _build_result(
            "failed",
            "Destructive actions are not supported by this executor.",
            reason="destructive_action_not_supported",
            artifacts=artifacts,
        )

    log_lines: List[str] = [f"action={action}"]
    account = _resolve_account(payload)
    if account:
        log_lines.append(f"account={account}")
    subscription_id = _resolve_subscription(payload)
    if subscription_id:
        log_lines.append(f"subscriptionId={subscription_id}")

    try:
        if normalized in {"azure.blob.list_containers", "azure.storage.list_containers"}:
            result = _list_containers(payload)
            summary = f"Listed {result.get('count', 0)} containers."
            return _build_result("completed", summary, result, "\n".join(log_lines), artifacts=artifacts)

        if normalized in {
            "azure.blob.list_blobs",
            "azure.storage.list_blobs",
            "azure.blob.list_objects",
            "azure.storage.list_objects",
        }:
            result = _list_blobs(payload)
            summary = f"Listed {result.get('count', 0)} blobs in {result.get('container', '')}."
            return _build_result("completed", summary, result, "\n".join(log_lines), artifacts=artifacts)

        if normalized in {"azure.compute.list_vms", "azure.compute.list_instances"}:
            result = _list_virtual_machines(payload)
            summary = f"Listed {result.get('count', 0)} virtual machines."
            return _build_result("completed", summary, result, "\n".join(log_lines), artifacts=artifacts)

        if normalized in {"azure.containerapps.list_apps", "azure.containerapps.list_containerapps", "azure.app.list_apps"}:
            result = _list_containerapps(payload)
            summary = f"Listed {result.get('count', 0)} container apps."
            return _build_result("completed", summary, result, "\n".join(log_lines), artifacts=artifacts)

        if normalized in {"azure.containerapps.list_jobs", "azure.app.list_jobs"}:
            result = _list_containerapp_jobs(payload)
            summary = f"Listed {result.get('count', 0)} jobs."
            return _build_result("completed", summary, result, "\n".join(log_lines), artifacts=artifacts)

        return _build_result("failed", "Unsupported action.", reason="unsupported_action", artifacts=artifacts)
    except Exception as exc:
        reason = _format_error(exc)
        if str(exc) == "container_missing":
            reason = "container_missing"
        if str(exc) == "subscription_missing":
            reason = "subscription_missing"
        if str(exc) == "resource_group_missing":
            reason = "resource_group_missing"
        if str(exc) == "access_token_missing":
            reason = "access_token_missing"
        return _build_result(
            "failed",
            "Execution failed.",
            result={"error": str(exc)},
            logs="\n".join(log_lines),
            reason=reason,
            artifacts=artifacts,
        )
