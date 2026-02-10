from typing import Any, Dict, List, Optional
import os

import google.auth
from google.auth.transport.requests import Request as GoogleRequest
from google.cloud import storage as gcs_storage
import requests

try:
    from google.api_core.exceptions import GoogleAPIError
except Exception:  # pragma: no cover - optional import
    GoogleAPIError = Exception  # type: ignore


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _resolve_project(payload: Dict[str, Any]) -> str:
    for key in ("projectId", "project_id", "project"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    )


def _resolve_bucket(payload: Dict[str, Any]) -> str:
    for key in ("bucket", "bucketName", "bucket_name", "storageBucket"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_prefix(payload: Dict[str, Any]) -> str:
    for key in ("prefix", "keyPrefix", "bucketPrefix", "objectPrefix"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_location(payload: Dict[str, Any]) -> str:
    for key in ("location", "region", "jobLocation", "cloudRunLocation", "cloudRunRegion"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("GCP_LOCATION")
        or os.getenv("GCP_REGION")
        or os.getenv("REGION")
        or os.getenv("STORAGE_REGION")
        or ""
    )


def _resolve_location_target(payload: Dict[str, Any], default_all: bool = False) -> str:
    location = _resolve_location(payload)
    if location:
        normalized = location.strip().lower()
        if normalized in {"all", "*", "-"}:
            return "-" if default_all else location
        return location
    return "-" if default_all else ""


def _resolve_zones(payload: Dict[str, Any]) -> List[str]:
    for key in ("zones", "zone", "locations", "location"):
        value = payload.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


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


def _resolve_page_token(payload: Dict[str, Any]) -> str:
    value = payload.get("pageToken") or payload.get("nextPageToken")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _format_error(exc: Exception) -> str:
    if isinstance(exc, GoogleAPIError):
        return f"gcp_error:{exc.__class__.__name__}"
    return f"error:{exc}"


def _get_access_token(scopes: List[str]) -> str:
    creds, _ = google.auth.default(scopes=scopes)
    creds.refresh(GoogleRequest())
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("gcp_token_missing")
    return str(token)


def _parse_url_segment(value: Optional[str]) -> str:
    if not value:
        return ""
    if "/" in value:
        return value.rsplit("/", 1)[-1]
    return value


def _list_buckets(project: str, max_results: int) -> Dict[str, Any]:
    client = gcs_storage.Client(project=project or None)
    iterator = client.list_buckets(max_results=max_results)
    buckets: List[Dict[str, Any]] = []
    for item in iterator:
        buckets.append(
            {
                "name": item.name,
                "createdAt": str(item.time_created or ""),
            }
        )
    return {
        "project": project or client.project or "",
        "count": len(buckets),
        "buckets": buckets,
        "nextPageToken": iterator.next_page_token,
    }


def _list_objects(project: str, bucket: str, prefix: str, max_results: int) -> Dict[str, Any]:
    client = gcs_storage.Client(project=project or None)
    iterator = client.list_blobs(bucket, prefix=prefix or None, max_results=max_results)
    objects: List[Dict[str, Any]] = []
    for blob in iterator:
        objects.append(
            {
                "name": blob.name,
                "size": blob.size,
                "updatedAt": str(blob.updated or ""),
            }
        )
    return {
        "bucket": bucket,
        "prefix": prefix,
        "count": len(objects),
        "objects": objects,
        "nextPageToken": iterator.next_page_token,
    }


def _list_run_jobs(project: str, location: str, max_results: int, page_token: str) -> Dict[str, Any]:
    token = _get_access_token(["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/jobs"
    params: Dict[str, Any] = {"pageSize": max_results}
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    if not resp.ok:
        raise RuntimeError(f"gcp_run_error:{resp.status_code}:{resp.text}")
    data = resp.json() if resp.text else {}
    jobs: List[Dict[str, Any]] = []
    for item in data.get("jobs", []) if isinstance(data.get("jobs"), list) else []:
        jobs.append(
            {
                "name": item.get("name"),
                "state": item.get("state"),
                "createTime": item.get("createTime"),
                "updateTime": item.get("updateTime"),
            }
        )
    return {
        "project": project,
        "location": location,
        "count": len(jobs),
        "jobs": jobs,
        "nextPageToken": data.get("nextPageToken"),
    }


def _list_run_services(project: str, location: str, max_results: int, page_token: str) -> Dict[str, Any]:
    token = _get_access_token(["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/services"
    params: Dict[str, Any] = {"pageSize": max_results}
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    if not resp.ok:
        raise RuntimeError(f"gcp_run_error:{resp.status_code}:{resp.text}")
    data = resp.json() if resp.text else {}
    services: List[Dict[str, Any]] = []
    for item in data.get("services", []) if isinstance(data.get("services"), list) else []:
        services.append(
            {
                "name": item.get("name"),
                "uid": item.get("uid"),
                "state": item.get("state"),
                "createTime": item.get("createTime"),
                "updateTime": item.get("updateTime"),
            }
        )
    return {
        "project": project,
        "location": location,
        "count": len(services),
        "services": services,
        "nextPageToken": data.get("nextPageToken"),
    }


def _list_compute_instances(
    project: str,
    max_results: int,
    page_token: str,
    zones: List[str],
) -> Dict[str, Any]:
    token = _get_access_token(["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/instances"
    params: Dict[str, Any] = {"maxResults": max_results}
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    if not resp.ok:
        raise RuntimeError(f"gcp_compute_error:{resp.status_code}:{resp.text}")
    data = resp.json() if resp.text else {}
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    instances: List[Dict[str, Any]] = []
    for scope, scoped in items.items():
        scoped_instances = scoped.get("instances") if isinstance(scoped, dict) else None
        if not isinstance(scoped_instances, list):
            continue
        for instance in scoped_instances:
            zone_name = _parse_url_segment(instance.get("zone")) or _parse_url_segment(scope)
            if zones and zone_name and zone_name not in zones:
                continue
            instances.append(
                {
                    "name": instance.get("name"),
                    "id": instance.get("id"),
                    "status": instance.get("status"),
                    "zone": zone_name,
                    "machineType": _parse_url_segment(instance.get("machineType")),
                    "networkIp": (instance.get("networkInterfaces") or [{}])[0].get("networkIP"),
                    "publicIp": (
                        (instance.get("networkInterfaces") or [{}])[0]
                        .get("accessConfigs", [{}])[0]
                        .get("natIP")
                    ),
                }
            )
    return {
        "project": project,
        "count": len(instances),
        "instances": instances,
        "nextPageToken": data.get("nextPageToken"),
    }


def _list_gke_clusters(project: str, location: str, max_results: int, page_token: str) -> Dict[str, Any]:
    token = _get_access_token(["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://container.googleapis.com/v1/projects/{project}/locations/{location}/clusters"
    params: Dict[str, Any] = {"pageSize": max_results}
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    if not resp.ok:
        raise RuntimeError(f"gcp_container_error:{resp.status_code}:{resp.text}")
    data = resp.json() if resp.text else {}
    clusters: List[Dict[str, Any]] = []
    for item in data.get("clusters", []) if isinstance(data.get("clusters"), list) else []:
        clusters.append(
            {
                "name": item.get("name"),
                "location": item.get("location"),
                "status": item.get("status"),
                "endpoint": item.get("endpoint"),
            }
        )
    return {
        "project": project,
        "location": location,
        "count": len(clusters),
        "clusters": clusters,
        "nextPageToken": data.get("nextPageToken"),
    }


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

    if "delete" in normalized or "remove" in normalized:
        return _build_result(
            "failed",
            "Destructive actions are not supported by this executor.",
            reason="destructive_action_not_supported",
        )

    project = _resolve_project(payload)
    log_lines: List[str] = [f"action={action}"]
    log_lines.append(f"project={project or 'default'}")

    try:
        if normalized in {"gcp.storage.list_buckets", "gcp.gcs.list_buckets"}:
            result = _list_buckets(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} buckets."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.storage.list_objects", "gcp.gcs.list_objects"}:
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            prefix = _resolve_prefix(payload)
            result = _list_objects(project, bucket, prefix, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} objects in {bucket}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.run.list_jobs", "gcp.cloudrun.list_jobs"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            location = _resolve_location_target(payload, default_all=True)
            if not location:
                return _build_result("failed", "Location is required.", reason="location_missing")
            log_lines.append(f"location={location}")
            result = _list_run_jobs(project, location, _resolve_max_results(payload), _resolve_page_token(payload))
            summary = f"Listed {result.get('count', 0)} jobs."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.run.list_services", "gcp.cloudrun.list_services"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            location = _resolve_location_target(payload, default_all=True)
            if not location:
                return _build_result("failed", "Location is required.", reason="location_missing")
            log_lines.append(f"location={location}")
            result = _list_run_services(project, location, _resolve_max_results(payload), _resolve_page_token(payload))
            summary = f"Listed {result.get('count', 0)} services."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.compute.list_instances", "gcp.compute.list_vms"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            zones = _resolve_zones(payload)
            if zones:
                log_lines.append(f"zones={','.join(zones)}")
            result = _list_compute_instances(
                project,
                _resolve_max_results(payload),
                _resolve_page_token(payload),
                zones,
            )
            summary = f"Listed {result.get('count', 0)} instances."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.container.list_clusters", "gcp.gke.list_clusters"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            location = _resolve_location_target(payload, default_all=True)
            if not location:
                return _build_result("failed", "Location is required.", reason="location_missing")
            log_lines.append(f"location={location}")
            result = _list_gke_clusters(project, location, _resolve_max_results(payload), _resolve_page_token(payload))
            summary = f"Listed {result.get('count', 0)} clusters."
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
