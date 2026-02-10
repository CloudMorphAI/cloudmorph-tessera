from typing import Any, Dict, List, Optional
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _resolve_region(payload: Dict[str, Any]) -> str:
    for key in ("region", "awsRegion", "aws_region"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def _resolve_bucket(payload: Dict[str, Any]) -> str:
    for key in ("bucket", "bucketName", "bucket_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_max_results(payload: Dict[str, Any], default: int = 200) -> int:
    for key in ("maxResults", "max_results", "maxKeys", "max_keys", "pageSize"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return default


def _resolve_bool(payload: Dict[str, Any], keys: List[str]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y"}:
                return True
            if normalized in {"0", "false", "no", "n"}:
                return False
    return False


def _normalize_region_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _list_regions(region: str) -> List[str]:
    ec2 = boto3.client("ec2", region_name=region)
    resp = ec2.describe_regions(AllRegions=True)
    regions = [item.get("RegionName") for item in resp.get("Regions", []) if item.get("RegionName")]
    return sorted(set(regions))


def _resolve_regions(payload: Dict[str, Any], fallback_region: str, default_all: bool = False) -> List[str]:
    for key in ("regions", "awsRegions", "aws_regions"):
        regions = _normalize_region_list(payload.get(key))
        if regions:
            return regions
    all_regions = _resolve_bool(payload, ["allRegions", "all_regions", "all"])
    if all_regions or default_all:
        try:
            regions = _list_regions(fallback_region)
            if regions:
                return regions
        except Exception:
            pass
    return [fallback_region]


def _resolve_clusters(payload: Dict[str, Any]) -> List[str]:
    for key in ("cluster", "clusterArn", "clusterName", "clusters"):
        value = payload.get(key)
        if value:
            return _normalize_region_list(value)
    return []


def _resolve_service(payload: Dict[str, Any]) -> str:
    for key in ("service", "serviceName", "serviceArn"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_desired_status(payload: Dict[str, Any]) -> str:
    value = payload.get("desiredStatus") or payload.get("desired_status")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _tag_value(tags: Any, key: str) -> str:
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        if isinstance(tag, dict) and tag.get("Key") == key and tag.get("Value"):
            return str(tag["Value"])
    return ""


def _list_instances(regions: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    max_results = _resolve_max_results(payload, default=200)
    filters = payload.get("filters") or payload.get("Filters")
    filters = filters if isinstance(filters, list) else []
    instances: List[Dict[str, Any]] = []
    per_region: List[Dict[str, Any]] = []

    for region in regions:
        ec2 = boto3.client("ec2", region_name=region)
        paginator = ec2.get_paginator("describe_instances")
        count_before = len(instances)
        paginate_kwargs: Dict[str, Any] = {}
        if filters:
            paginate_kwargs["Filters"] = filters
        for page in paginator.paginate(**paginate_kwargs):
            for reservation in page.get("Reservations", []):
                for item in reservation.get("Instances", []):
                    instances.append(
                        {
                            "region": region,
                            "instanceId": item.get("InstanceId"),
                            "state": item.get("State", {}).get("Name"),
                            "instanceType": item.get("InstanceType"),
                            "availabilityZone": item.get("Placement", {}).get("AvailabilityZone"),
                            "name": _tag_value(item.get("Tags"), "Name"),
                            "privateIp": item.get("PrivateIpAddress"),
                            "publicIp": item.get("PublicIpAddress"),
                            "launchTime": str(item.get("LaunchTime") or ""),
                        }
                    )
                    if len(instances) - count_before >= max_results:
                        break
                if len(instances) - count_before >= max_results:
                    break
            if len(instances) - count_before >= max_results:
                break
        per_region.append({"region": region, "count": len(instances) - count_before})

    return {
        "count": len(instances),
        "regions": per_region,
        "instances": instances,
    }


def _parse_name_from_arn(value: str) -> str:
    if not value:
        return ""
    if "/" in value:
        return value.rsplit("/", 1)[-1]
    return value


def _list_ecs_clusters(regions: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    max_results = _resolve_max_results(payload, default=100)
    clusters: List[Dict[str, Any]] = []
    for region in regions:
        ecs = boto3.client("ecs", region_name=region)
        paginator = ecs.get_paginator("list_clusters")
        for page in paginator.paginate():
            for arn in page.get("clusterArns", []):
                clusters.append({"region": region, "clusterArn": arn, "name": _parse_name_from_arn(arn)})
                if len(clusters) >= max_results:
                    break
            if len(clusters) >= max_results:
                break
    return {
        "count": len(clusters),
        "clusters": clusters,
    }


def _clusters_for_regions(
    regions: List[str],
    payload: Dict[str, Any],
    default_all: bool = False,
) -> List[Dict[str, str]]:
    clusters = _resolve_clusters(payload)
    if clusters:
        return [{"region": region, "cluster": cluster} for region in regions for cluster in clusters]
    if _resolve_bool(payload, ["allClusters", "all_clusters", "all"]) or default_all:
        found: List[Dict[str, str]] = []
        for region in regions:
            ecs = boto3.client("ecs", region_name=region)
            paginator = ecs.get_paginator("list_clusters")
            for page in paginator.paginate():
                for arn in page.get("clusterArns", []):
                    found.append({"region": region, "cluster": arn})
        return found
    return []


def _list_ecs_services(regions: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    max_results = _resolve_max_results(payload, default=100)
    clusters = _clusters_for_regions(regions, payload, default_all=True)
    services: List[Dict[str, Any]] = []
    for entry in clusters:
        region = entry["region"]
        cluster = entry["cluster"]
        ecs = boto3.client("ecs", region_name=region)
        paginator = ecs.get_paginator("list_services")
        count_before = len(services)
        for page in paginator.paginate(cluster=cluster):
            for arn in page.get("serviceArns", []):
                services.append(
                    {
                        "region": region,
                        "cluster": cluster,
                        "serviceArn": arn,
                        "name": _parse_name_from_arn(arn),
                    }
                )
                if len(services) - count_before >= max_results:
                    break
            if len(services) - count_before >= max_results:
                break
    return {
        "count": len(services),
        "services": services,
    }


def _list_ecs_tasks(regions: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    max_results = _resolve_max_results(payload, default=100)
    clusters = _clusters_for_regions(regions, payload, default_all=True)
    desired_status = _resolve_desired_status(payload)
    service = _resolve_service(payload)
    tasks: List[Dict[str, Any]] = []
    for entry in clusters:
        region = entry["region"]
        cluster = entry["cluster"]
        ecs = boto3.client("ecs", region_name=region)
        list_kwargs: Dict[str, Any] = {"cluster": cluster}
        if desired_status:
            list_kwargs["desiredStatus"] = desired_status
        if service:
            list_kwargs["serviceName"] = service
        paginator = ecs.get_paginator("list_tasks")
        task_arns: List[str] = []
        for page in paginator.paginate(**list_kwargs):
            task_arns.extend(page.get("taskArns", []))
            if len(task_arns) >= max_results:
                task_arns = task_arns[:max_results]
                break
        if not task_arns:
            continue
        for idx in range(0, len(task_arns), 100):
            chunk = task_arns[idx: idx + 100]
            resp = ecs.describe_tasks(cluster=cluster, tasks=chunk)
            for item in resp.get("tasks", []):
                containers = [
                    {
                        "name": container.get("name"),
                        "lastStatus": container.get("lastStatus"),
                        "exitCode": container.get("exitCode"),
                        "reason": container.get("reason"),
                    }
                    for container in item.get("containers", [])
                ]
                tasks.append(
                    {
                        "region": region,
                        "cluster": cluster,
                        "taskArn": item.get("taskArn"),
                        "lastStatus": item.get("lastStatus"),
                        "desiredStatus": item.get("desiredStatus"),
                        "launchType": item.get("launchType"),
                        "containers": containers,
                    }
                )
    return {
        "count": len(tasks),
        "tasks": tasks,
    }

def _format_error(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return f"aws_error:{exc.response.get('Error', {}).get('Code', 'unknown')}"
    if isinstance(exc, BotoCoreError):
        return "aws_error:boto_core"
    return f"error:{exc}"


def _list_buckets(region: str) -> Dict[str, Any]:
    s3 = boto3.client("s3", region_name=region)
    resp = s3.list_buckets()
    buckets = [
        {"name": item.get("Name"), "createdAt": str(item.get("CreationDate") or "")}
        for item in resp.get("Buckets", [])
    ]
    return {
        "buckets": buckets,
        "count": len(buckets),
    }


def _list_objects(region: str, bucket: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    s3 = boto3.client("s3", region_name=region)
    prefix = payload.get("prefix") or payload.get("keyPrefix") or ""
    max_keys = payload.get("maxKeys") or payload.get("max_keys") or 1000
    try:
        max_keys = int(max_keys)
    except (TypeError, ValueError):
        max_keys = 1000
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=str(prefix), MaxKeys=max_keys)
    objects = [
        {
            "key": item.get("Key"),
            "size": item.get("Size"),
            "lastModified": str(item.get("LastModified") or ""),
        }
        for item in resp.get("Contents", [])
    ]
    return {
        "bucket": bucket,
        "prefix": prefix,
        "count": len(objects),
        "objects": objects,
        "isTruncated": bool(resp.get("IsTruncated")),
        "nextContinuationToken": resp.get("NextContinuationToken"),
    }


def _extract_lifecycle_rules(payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    for key in ("lifecycle", "lifecycleConfiguration", "lifecycle_configuration"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            rules = candidate.get("Rules") or candidate.get("rules")
            if isinstance(rules, list) and rules:
                return rules
    rules = payload.get("rules") or payload.get("Rules")
    if isinstance(rules, list) and rules:
        return rules
    return None


def _put_bucket_lifecycle(region: str, bucket: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    rules = _extract_lifecycle_rules(payload)
    if not rules:
        raise ValueError("lifecycle_rules_missing")
    s3 = boto3.client("s3", region_name=region)
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": rules},
    )
    return {"bucket": bucket, "rulesApplied": len(rules)}


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

    region = _resolve_region(payload)
    log_lines: List[str] = [f"action={action}", f"region={region}"]

    try:
        if normalized == "aws.s3.list_buckets":
            result = _list_buckets(region)
            summary = f"Listed {result.get('count', 0)} buckets."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.s3.list_objects":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            result = _list_objects(region, bucket, payload)
            summary = f"Listed {result.get('count', 0)} objects in {bucket}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.s3.put_bucket_lifecycle":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            result = _put_bucket_lifecycle(region, bucket, payload)
            summary = f"Applied lifecycle rules to {bucket}."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.ec2.list_instances":
            regions = _resolve_regions(payload, region, default_all=True)
            log_lines.append(f"regions={','.join(regions)}")
            result = _list_instances(regions, payload)
            summary = f"Listed {result.get('count', 0)} instances."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.ecs.list_clusters":
            regions = _resolve_regions(payload, region, default_all=True)
            log_lines.append(f"regions={','.join(regions)}")
            result = _list_ecs_clusters(regions, payload)
            summary = f"Listed {result.get('count', 0)} ECS clusters."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.ecs.list_services":
            regions = _resolve_regions(payload, region, default_all=True)
            log_lines.append(f"regions={','.join(regions)}")
            result = _list_ecs_services(regions, payload)
            summary = f"Listed {result.get('count', 0)} ECS services."
            return _build_result("completed", summary, result, "\n".join(log_lines))

        if normalized == "aws.ecs.list_tasks":
            regions = _resolve_regions(payload, region, default_all=True)
            log_lines.append(f"regions={','.join(regions)}")
            result = _list_ecs_tasks(regions, payload)
            summary = f"Listed {result.get('count', 0)} ECS tasks."
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
