from typing import Any, Dict, List, Optional
import os
import urllib.parse

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


def _gcp_get_json(url: str) -> Dict[str, Any]:
    token = _get_access_token(["https://www.googleapis.com/auth/cloud-platform"])
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"gcp_api_error:{resp.status_code}:{resp.text[:500]}")
    return resp.json() if resp.text else {}


def _list_iam_service_accounts(project: str, max_results: int) -> Dict[str, Any]:
    url = f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts?pageSize={max_results}"
    data = _gcp_get_json(url)
    accounts: List[Dict[str, Any]] = []
    for a in data.get("accounts", []) if isinstance(data.get("accounts"), list) else []:
        accounts.append({"email": a.get("email"), "displayName": a.get("displayName"), "name": a.get("name")})
    return {"project": project, "count": len(accounts), "serviceAccounts": accounts}


def _list_iam_roles(max_results: int) -> Dict[str, Any]:
    url = f"https://iam.googleapis.com/v1/roles?pageSize={min(max_results, 500)}"
    data = _gcp_get_json(url)
    roles: List[Dict[str, Any]] = []
    for r in data.get("roles", []) if isinstance(data.get("roles"), list) else []:
        roles.append({"name": r.get("name"), "title": r.get("title"), "stage": r.get("stage")})
    return {"count": len(roles), "roles": roles}


def _list_bigquery_datasets(project: str, max_results: int) -> Dict[str, Any]:
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets?maxResults={max_results}"
    data = _gcp_get_json(url)
    datasets: List[Dict[str, Any]] = []
    for d in data.get("datasets", []) if isinstance(data.get("datasets"), list) else []:
        ds_id = d.get("datasetReference", {}).get("datasetId") if isinstance(d.get("datasetReference"), dict) else ""
        datasets.append({"datasetId": ds_id, "id": d.get("id")})
    return {"project": project, "count": len(datasets), "datasets": datasets}


def _list_bigquery_tables(project: str, dataset_id: str, max_results: int) -> Dict[str, Any]:
    enc_ds = urllib.parse.quote(dataset_id, safe="")
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{enc_ds}/tables?maxResults={max_results}"
    data = _gcp_get_json(url)
    tables: List[Dict[str, Any]] = []
    for t in data.get("tables", []) if isinstance(data.get("tables"), list) else []:
        tid = t.get("tableReference", {}).get("tableId") if isinstance(t.get("tableReference"), dict) else ""
        tables.append({"tableId": tid, "type": t.get("type")})
    return {"project": project, "datasetId": dataset_id, "count": len(tables), "tables": tables}


def _list_cloud_functions(project: str, location: str, max_results: int) -> Dict[str, Any]:
    loc = location if location and location != "-" else "us-central1"
    url = (
        f"https://cloudfunctions.googleapis.com/v1/projects/{project}/locations/{loc}/functions"
        f"?pageSize={max_results}"
    )
    data = _gcp_get_json(url)
    functions: List[Dict[str, Any]] = []
    for fn in data.get("functions", []) if isinstance(data.get("functions"), list) else []:
        functions.append({"name": fn.get("name"), "runtime": fn.get("runtime"), "status": fn.get("status")})
    return {"project": project, "location": loc, "count": len(functions), "functions": functions}


def _list_pubsub_topics(project: str, max_results: int) -> Dict[str, Any]:
    url = f"https://pubsub.googleapis.com/v1/projects/{project}/topics?pageSize={max_results}"
    data = _gcp_get_json(url)
    topics: List[Dict[str, Any]] = []
    for t in data.get("topics", []) if isinstance(data.get("topics"), list) else []:
        topics.append({"name": t.get("name")})
    return {"project": project, "count": len(topics), "topics": topics}


def _list_pubsub_subscriptions(project: str, max_results: int) -> Dict[str, Any]:
    url = f"https://pubsub.googleapis.com/v1/projects/{project}/subscriptions?pageSize={max_results}"
    data = _gcp_get_json(url)
    subs: List[Dict[str, Any]] = []
    for s in data.get("subscriptions", []) if isinstance(data.get("subscriptions"), list) else []:
        subs.append({"name": s.get("name")})
    return {"project": project, "count": len(subs), "subscriptions": subs}


def _list_sql_instances(project: str, max_results: int) -> Dict[str, Any]:
    url = f"https://sqladmin.googleapis.com/v1/projects/{project}/instances?maxResults={max_results}"
    data = _gcp_get_json(url)
    instances: List[Dict[str, Any]] = []
    for inst in data.get("items", []) if isinstance(data.get("items"), list) else []:
        instances.append(
            {
                "name": inst.get("name"),
                "region": inst.get("region"),
                "databaseVersion": inst.get("databaseVersion"),
                "state": inst.get("state"),
            }
        )
    return {"project": project, "count": len(instances), "instances": instances}


def _describe_compute_instance(project: str, zone: str, instance: str) -> Dict[str, Any]:
    url = f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{instance}"
    data = _gcp_get_json(url)
    return {
        "name": data.get("name"),
        "id": data.get("id"),
        "status": data.get("status"),
        "zone": _parse_url_segment(data.get("zone")),
        "machineType": _parse_url_segment(data.get("machineType")),
        "networkInterfaces": [
            {
                "networkIP": ni.get("networkIP"),
                "accessConfigs": [
                    {"natIP": ac.get("natIP"), "type": ac.get("type")}
                    for ac in (ni.get("accessConfigs") or [])
                ],
            }
            for ni in (data.get("networkInterfaces") or [])
        ],
        "disks": [
            {
                "source": _parse_url_segment(d.get("source")),
                "boot": d.get("boot"),
                "autoDelete": d.get("autoDelete"),
            }
            for d in (data.get("disks") or [])
        ],
        "labels": data.get("labels") or {},
        "creationTimestamp": data.get("creationTimestamp"),
    }


def _get_bucket_metadata(project: str, bucket: str) -> Dict[str, Any]:
    client = gcs_storage.Client(project=project or None)
    b = client.get_bucket(bucket)
    return {
        "name": b.name,
        "location": b.location,
        "storageClass": b.storage_class,
        "timeCreated": str(b.time_created or ""),
        "labels": b.labels or {},
        "versioningEnabled": b.versioning_enabled,
        "retentionPeriod": b.retention_period,
        "projectNumber": b.project_number,
    }


def _get_iam_service_account(project: str, email: str) -> Dict[str, Any]:
    url = f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts/{urllib.parse.quote(email, safe='')}"
    data = _gcp_get_json(url)
    return {
        "name": data.get("name"),
        "email": data.get("email"),
        "displayName": data.get("displayName"),
        "description": data.get("description"),
        "disabled": data.get("disabled", False),
        "projectId": data.get("projectId"),
        "uniqueId": data.get("uniqueId"),
    }


def _get_bigquery_dataset(project: str, dataset_id: str) -> Dict[str, Any]:
    enc_ds = urllib.parse.quote(dataset_id, safe="")
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{enc_ds}"
    data = _gcp_get_json(url)
    ds_ref = data.get("datasetReference") if isinstance(data.get("datasetReference"), dict) else {}
    return {
        "datasetId": ds_ref.get("datasetId"),
        "projectId": ds_ref.get("projectId"),
        "location": data.get("location"),
        "description": data.get("description"),
        "defaultTableExpirationMs": data.get("defaultTableExpirationMs"),
        "labels": data.get("labels") or {},
        "creationTime": data.get("creationTime"),
        "lastModifiedTime": data.get("lastModifiedTime"),
        "access": [
            {
                "role": entry.get("role"),
                "userByEmail": entry.get("userByEmail"),
                "specialGroup": entry.get("specialGroup"),
            }
            for entry in (data.get("access") or [])
            if isinstance(entry, dict)
        ],
    }


def _describe_cloudrun_service(project: str, location: str, service: str) -> Dict[str, Any]:
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/services/{service}"
    data = _gcp_get_json(url)
    return {
        "name": data.get("name"),
        "uid": data.get("uid"),
        "state": data.get("state"),
        "uri": data.get("uri"),
        "latestReadyRevision": data.get("latestReadyRevision"),
        "latestCreatedRevision": data.get("latestCreatedRevision"),
        "traffic": data.get("traffic") or [],
        "createTime": data.get("createTime"),
        "updateTime": data.get("updateTime"),
        "labels": data.get("labels") or {},
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

        if normalized in {"gcp.iam.list_service_accounts"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            result = _list_iam_service_accounts(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} service accounts."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.iam.list_roles"}:
            result = _list_iam_roles(_resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} IAM roles."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.bigquery.list_datasets"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            result = _list_bigquery_datasets(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} datasets."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.bigquery.list_tables"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            dataset_id = str(payload.get("datasetId") or payload.get("dataset") or "").strip()
            if not dataset_id:
                return _build_result("failed", "datasetId is required.", reason="dataset_missing")
            result = _list_bigquery_tables(project, dataset_id, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} tables."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.functions.list_functions", "gcp.cloudfunctions.list_functions"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            location = _resolve_location(payload) or "us-central1"
            result = _list_cloud_functions(project, location, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} functions."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.pubsub.list_topics"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            result = _list_pubsub_topics(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} topics."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.pubsub.list_subscriptions"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            result = _list_pubsub_subscriptions(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} subscriptions."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.sql.list_instances"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            result = _list_sql_instances(project, _resolve_max_results(payload))
            summary = f"Listed {result.get('count', 0)} Cloud SQL instances."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.compute.describe_instance"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            zone = str(payload.get("zone") or "").strip()
            instance_name = str(payload.get("instance") or payload.get("instanceName") or "").strip()
            if not zone:
                return _build_result("failed", "Zone is required.", reason="zone_missing")
            if not instance_name:
                return _build_result("failed", "Instance name is required.", reason="instance_missing")
            log_lines.append(f"zone={zone} instance={instance_name}")
            result = _describe_compute_instance(project, zone, instance_name)
            summary = f"Described instance {instance_name}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.storage.get_bucket_metadata"}:
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            log_lines.append(f"bucket={bucket}")
            result = _get_bucket_metadata(project, bucket)
            summary = f"Retrieved metadata for bucket {bucket}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.iam.get_service_account"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            email = str(payload.get("serviceAccountEmail") or payload.get("email") or "").strip()
            if not email:
                return _build_result("failed", "serviceAccountEmail is required.", reason="email_missing")
            log_lines.append(f"email={email}")
            result = _get_iam_service_account(project, email)
            summary = f"Retrieved service account {email}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.bigquery.get_dataset"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            dataset_id = str(payload.get("datasetId") or payload.get("dataset") or "").strip()
            if not dataset_id:
                return _build_result("failed", "datasetId is required.", reason="dataset_missing")
            log_lines.append(f"datasetId={dataset_id}")
            result = _get_bigquery_dataset(project, dataset_id)
            summary = f"Retrieved dataset {dataset_id}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized in {"gcp.cloudrun.describe_service"}:
            if not project:
                return _build_result("failed", "Project is required.", reason="project_missing")
            location = _resolve_location(payload)
            if not location:
                return _build_result("failed", "Location is required.", reason="location_missing")
            service = str(payload.get("service") or payload.get("serviceName") or "").strip()
            if not service:
                return _build_result("failed", "Service name is required.", reason="service_missing")
            log_lines.append(f"location={location} service={service}")
            result = _describe_cloudrun_service(project, location, service)
            summary = f"Described Cloud Run service {service}."
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
