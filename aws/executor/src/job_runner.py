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

        if normalized == "aws.iam.list_roles":
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            roles: List[Dict[str, Any]] = []
            for page in iam.get_paginator("list_roles").paginate():
                for r in page.get("Roles", []):
                    roles.append(
                        {
                            "roleName": r.get("RoleName"),
                            "arn": r.get("Arn"),
                            "createDate": str(r.get("CreateDate") or ""),
                        }
                    )
                if len(roles) >= _resolve_max_results(payload, 500):
                    break
            result = {"count": len(roles), "roles": roles[: _resolve_max_results(payload, 500)]}
            return _build_result("completed", f"Listed {result['count']} IAM roles.", result, "\n".join(log_lines))

        if normalized == "aws.iam.list_users":
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            users: List[Dict[str, Any]] = []
            for page in iam.get_paginator("list_users").paginate():
                for u in page.get("Users", []):
                    users.append(
                        {
                            "userName": u.get("UserName"),
                            "arn": u.get("Arn"),
                            "createDate": str(u.get("CreateDate") or ""),
                        }
                    )
                if len(users) >= _resolve_max_results(payload, 500):
                    break
            result = {"count": len(users), "users": users[: _resolve_max_results(payload, 500)]}
            return _build_result("completed", f"Listed {result['count']} IAM users.", result, "\n".join(log_lines))

        if normalized == "aws.iam.list_policies":
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            scope = str(payload.get("scope") or "Local").strip() or "Local"
            iam = boto3.client("iam", region_name=iam_region)
            policies: List[Dict[str, Any]] = []
            for page in iam.get_paginator("list_policies").paginate(Scope=scope):
                for p in page.get("Policies", []):
                    policies.append(
                        {
                            "policyName": p.get("PolicyName"),
                            "arn": p.get("Arn"),
                            "defaultVersionId": p.get("DefaultVersionId"),
                        }
                    )
                if len(policies) >= _resolve_max_results(payload, 500):
                    break
            result = {"count": len(policies), "policies": policies[: _resolve_max_results(payload, 500)]}
            return _build_result("completed", f"Listed {result['count']} IAM policies.", result, "\n".join(log_lines))

        if normalized == "aws.iam.list_groups":
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            groups: List[Dict[str, Any]] = []
            for page in iam.get_paginator("list_groups").paginate():
                for g in page.get("Groups", []):
                    groups.append(
                        {
                            "groupName": g.get("GroupName"),
                            "arn": g.get("Arn"),
                            "createDate": str(g.get("CreateDate") or ""),
                        }
                    )
                if len(groups) >= _resolve_max_results(payload, 500):
                    break
            result = {"count": len(groups), "groups": groups[: _resolve_max_results(payload, 500)]}
            return _build_result("completed", f"Listed {result['count']} IAM groups.", result, "\n".join(log_lines))

        if normalized == "aws.sts.get_caller_identity":
            sts = boto3.client("sts", region_name=region)
            ident = sts.get_caller_identity()
            result = {
                "arn": ident.get("Arn"),
                "userId": ident.get("UserId"),
                "account": ident.get("Account"),
            }
            return _build_result("completed", "Resolved caller identity.", result, "\n".join(log_lines))

        if normalized == "aws.lambda.list_functions":
            regions = _resolve_regions(payload, region, default_all=False)
            functions: List[Dict[str, Any]] = []
            for reg in regions:
                lam = boto3.client("lambda", region_name=reg)
                paginator = lam.get_paginator("list_functions")
                for page in paginator.paginate():
                    for fn in page.get("Functions", []):
                        functions.append(
                            {
                                "region": reg,
                                "functionName": fn.get("FunctionName"),
                                "runtime": fn.get("Runtime"),
                                "lastModified": fn.get("LastModified"),
                            }
                        )
                        if len(functions) >= _resolve_max_results(payload, 300):
                            break
                    if len(functions) >= _resolve_max_results(payload, 300):
                        break
                if len(functions) >= _resolve_max_results(payload, 300):
                    break
            result = {"count": len(functions), "functions": functions}
            return _build_result("completed", f"Listed {len(functions)} Lambda functions.", result, "\n".join(log_lines))

        if normalized == "aws.rds.list_instances":
            regions = _resolve_regions(payload, region, default_all=True)
            instances: List[Dict[str, Any]] = []
            for reg in regions:
                rds = boto3.client("rds", region_name=reg)
                paginator = rds.get_paginator("describe_db_instances")
                for page in paginator.paginate():
                    for db in page.get("DBInstances", []):
                        instances.append(
                            {
                                "region": reg,
                                "dbInstanceIdentifier": db.get("DBInstanceIdentifier"),
                                "engine": db.get("Engine"),
                                "status": db.get("DBInstanceStatus"),
                            }
                        )
                        if len(instances) >= _resolve_max_results(payload, 300):
                            break
                    if len(instances) >= _resolve_max_results(payload, 300):
                        break
                if len(instances) >= _resolve_max_results(payload, 300):
                    break
            result = {"count": len(instances), "instances": instances}
            return _build_result("completed", f"Listed {len(instances)} RDS instances.", result, "\n".join(log_lines))

        if normalized == "aws.rds.list_clusters":
            regions = _resolve_regions(payload, region, default_all=True)
            clusters: List[Dict[str, Any]] = []
            for reg in regions:
                rds = boto3.client("rds", region_name=reg)
                paginator = rds.get_paginator("describe_db_clusters")
                for page in paginator.paginate():
                    for cl in page.get("DBClusters", []):
                        clusters.append(
                            {
                                "region": reg,
                                "dbClusterIdentifier": cl.get("DBClusterIdentifier"),
                                "engine": cl.get("Engine"),
                                "status": cl.get("Status"),
                            }
                        )
                        if len(clusters) >= _resolve_max_results(payload, 200):
                            break
                    if len(clusters) >= _resolve_max_results(payload, 200):
                        break
                if len(clusters) >= _resolve_max_results(payload, 200):
                    break
            result = {"count": len(clusters), "clusters": clusters}
            return _build_result("completed", f"Listed {len(clusters)} RDS clusters.", result, "\n".join(log_lines))

        if normalized == "aws.cloudwatch.list_alarms":
            regions = _resolve_regions(payload, region, default_all=False)
            alarms: List[Dict[str, Any]] = []
            for reg in regions:
                cw = boto3.client("cloudwatch", region_name=reg)
                paginator = cw.get_paginator("describe_alarms")
                for page in paginator.paginate(MaxRecords=100):
                    for a in page.get("MetricAlarms", []):
                        alarms.append(
                            {
                                "region": reg,
                                "alarmName": a.get("AlarmName"),
                                "stateValue": a.get("StateValue"),
                                "metricName": a.get("MetricName"),
                            }
                        )
                        if len(alarms) >= _resolve_max_results(payload, 200):
                            break
                    if len(alarms) >= _resolve_max_results(payload, 200):
                        break
                if len(alarms) >= _resolve_max_results(payload, 200):
                    break
            result = {"count": len(alarms), "alarms": alarms}
            return _build_result("completed", f"Listed {len(alarms)} CloudWatch alarms.", result, "\n".join(log_lines))

        if normalized == "aws.cloudwatch.list_metrics":
            regions = _resolve_regions(payload, region, default_all=False)
            namespace = str(payload.get("namespace") or payload.get("Namespace") or "AWS/EC2").strip()
            metrics: List[Dict[str, Any]] = []
            for reg in regions:
                cw = boto3.client("cloudwatch", region_name=reg)
                paginator = cw.get_paginator("list_metrics")
                for page in paginator.paginate(Namespace=namespace):
                    for m in page.get("Metrics", []):
                        metrics.append(
                            {
                                "region": reg,
                                "namespace": m.get("Namespace"),
                                "metricName": m.get("MetricName"),
                            }
                        )
                        if len(metrics) >= _resolve_max_results(payload, 200):
                            break
                    if len(metrics) >= _resolve_max_results(payload, 200):
                        break
                if len(metrics) >= _resolve_max_results(payload, 200):
                    break
            result = {"count": len(metrics), "metrics": metrics}
            return _build_result("completed", f"Listed {len(metrics)} metrics.", result, "\n".join(log_lines))

        if normalized == "aws.sns.list_topics":
            regions = _resolve_regions(payload, region, default_all=False)
            topics: List[Dict[str, Any]] = []
            for reg in regions:
                sns = boto3.client("sns", region_name=reg)
                paginator = sns.get_paginator("list_topics")
                for page in paginator.paginate():
                    for t in page.get("Topics", []):
                        arn = t.get("TopicArn") or ""
                        topics.append({"region": reg, "topicArn": arn})
                        if len(topics) >= _resolve_max_results(payload, 300):
                            break
                    if len(topics) >= _resolve_max_results(payload, 300):
                        break
                if len(topics) >= _resolve_max_results(payload, 300):
                    break
            result = {"count": len(topics), "topics": topics}
            return _build_result("completed", f"Listed {len(topics)} SNS topics.", result, "\n".join(log_lines))

        if normalized == "aws.sqs.list_queues":
            regions = _resolve_regions(payload, region, default_all=False)
            queues: List[Dict[str, Any]] = []
            for reg in regions:
                sqs = boto3.client("sqs", region_name=reg)
                resp = sqs.list_queues(MaxResults=min(1000, _resolve_max_results(payload, 1000)))
                urls = resp.get("QueueUrls") or []
                for url in urls:
                    queues.append({"region": reg, "queueUrl": url})
                    if len(queues) >= _resolve_max_results(payload, 500):
                        break
                if len(queues) >= _resolve_max_results(payload, 500):
                    break
            result = {"count": len(queues), "queues": queues}
            return _build_result("completed", f"Listed {len(queues)} SQS queues.", result, "\n".join(log_lines))

        # --- Write actions ---

        if normalized == "aws.ec2.start_instances":
            instance_ids = payload.get("instanceIds") or payload.get("instance_ids") or []
            if isinstance(instance_ids, str):
                instance_ids = [i.strip() for i in instance_ids.split(",") if i.strip()]
            if not instance_ids:
                return _build_result("failed", "instanceIds required.", reason="instance_ids_missing")
            ec2 = boto3.client("ec2", region_name=region)
            resp = ec2.start_instances(InstanceIds=instance_ids)
            starting = [
                {"instanceId": i.get("InstanceId"), "currentState": i.get("CurrentState", {}).get("Name")}
                for i in resp.get("StartingInstances", [])
            ]
            result = {"startingInstances": starting, "count": len(starting)}
            return _build_result("completed", f"Started {len(starting)} instance(s).", result, "\n".join(log_lines))

        if normalized == "aws.ec2.stop_instances":
            instance_ids = payload.get("instanceIds") or payload.get("instance_ids") or []
            if isinstance(instance_ids, str):
                instance_ids = [i.strip() for i in instance_ids.split(",") if i.strip()]
            if not instance_ids:
                return _build_result("failed", "instanceIds required.", reason="instance_ids_missing")
            ec2 = boto3.client("ec2", region_name=region)
            resp = ec2.stop_instances(InstanceIds=instance_ids)
            stopping = [
                {"instanceId": i.get("InstanceId"), "currentState": i.get("CurrentState", {}).get("Name")}
                for i in resp.get("StoppingInstances", [])
            ]
            result = {"stoppingInstances": stopping, "count": len(stopping)}
            return _build_result("completed", f"Stopped {len(stopping)} instance(s).", result, "\n".join(log_lines))

        if normalized == "aws.s3.put_object":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            key = str(payload.get("key") or payload.get("objectKey") or "").strip()
            if not key:
                return _build_result("failed", "Object key is required.", reason="key_missing")
            body = payload.get("body") or payload.get("content") or ""
            if isinstance(body, str):
                body = body.encode("utf-8")
            content_type = str(payload.get("contentType") or payload.get("content_type") or "application/octet-stream")
            s3 = boto3.client("s3", region_name=region)
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
            result = {"bucket": bucket, "key": key, "contentType": content_type}
            return _build_result("completed", f"Uploaded s3://{bucket}/{key}.", result, "\n".join(log_lines))

        if normalized == "aws.s3.get_object":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            key = str(payload.get("key") or payload.get("objectKey") or "").strip()
            if not key:
                return _build_result("failed", "Object key is required.", reason="key_missing")
            s3 = boto3.client("s3", region_name=region)
            resp = s3.get_object(Bucket=bucket, Key=key)
            content_length = resp.get("ContentLength", 0)
            content_type = resp.get("ContentType", "")
            last_modified = str(resp.get("LastModified") or "")
            body_bytes = resp["Body"].read()
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = f"<binary content, {content_length} bytes>"
            result = {
                "bucket": bucket,
                "key": key,
                "contentType": content_type,
                "contentLength": content_length,
                "lastModified": last_modified,
                "body": body_text,
            }
            return _build_result("completed", f"Retrieved s3://{bucket}/{key} ({content_length} bytes).", result, "\n".join(log_lines))

        if normalized == "aws.lambda.update_function_code":
            function_name = str(payload.get("functionName") or payload.get("function_name") or "").strip()
            if not function_name:
                return _build_result("failed", "functionName is required.", reason="function_name_missing")
            lam = boto3.client("lambda", region_name=region)
            update_kwargs: Dict[str, Any] = {"FunctionName": function_name}
            s3_bucket = _resolve_bucket(payload)
            s3_key = str(payload.get("s3Key") or payload.get("s3_key") or "").strip()
            zip_file = payload.get("zipFile") or payload.get("zip_file")
            if s3_bucket and s3_key:
                update_kwargs["S3Bucket"] = s3_bucket
                update_kwargs["S3Key"] = s3_key
                s3_version = str(payload.get("s3ObjectVersion") or payload.get("s3_object_version") or "").strip()
                if s3_version:
                    update_kwargs["S3ObjectVersion"] = s3_version
            elif zip_file:
                if isinstance(zip_file, str):
                    import base64
                    zip_file = base64.b64decode(zip_file)
                update_kwargs["ZipFile"] = zip_file
            else:
                return _build_result("failed", "Either s3Bucket+s3Key or zipFile is required.", reason="code_source_missing")
            resp = lam.update_function_code(**update_kwargs)
            result = {
                "functionName": resp.get("FunctionName"),
                "functionArn": resp.get("FunctionArn"),
                "runtime": resp.get("Runtime"),
                "lastModified": resp.get("LastModified"),
                "codeSha256": resp.get("CodeSha256"),
                "version": resp.get("Version"),
            }
            return _build_result("completed", f"Updated function code for {function_name}.", result, "\n".join(log_lines))

        # --- Additional read actions ---

        if normalized == "aws.cloudwatch.get_log_events":
            log_group = str(payload.get("logGroupName") or payload.get("log_group_name") or "").strip()
            log_stream = str(payload.get("logStreamName") or payload.get("log_stream_name") or "").strip()
            if not log_group or not log_stream:
                return _build_result("failed", "logGroupName and logStreamName are required.", reason="log_params_missing")
            cw_logs = boto3.client("logs", region_name=region)
            kwargs: Dict[str, Any] = {
                "logGroupName": log_group,
                "logStreamName": log_stream,
                "limit": _resolve_max_results(payload, 100),
            }
            start_time = payload.get("startTime")
            end_time = payload.get("endTime")
            if start_time:
                kwargs["startTime"] = int(start_time)
            if end_time:
                kwargs["endTime"] = int(end_time)
            resp = cw_logs.get_log_events(**kwargs)
            events = [
                {"timestamp": e.get("timestamp"), "message": e.get("message")}
                for e in resp.get("events", [])
            ]
            result = {"logGroupName": log_group, "logStreamName": log_stream, "count": len(events), "events": events}
            return _build_result("completed", f"Retrieved {len(events)} log events.", result, "\n".join(log_lines))

        if normalized == "aws.cloudwatch.get_metric_data":
            cw = boto3.client("cloudwatch", region_name=region)
            metric_queries = payload.get("metricDataQueries") or payload.get("metric_data_queries") or []
            if not metric_queries:
                return _build_result("failed", "metricDataQueries is required.", reason="metric_queries_missing")
            start_time = payload.get("startTime") or payload.get("start_time")
            end_time = payload.get("endTime") or payload.get("end_time")
            if not start_time or not end_time:
                return _build_result("failed", "startTime and endTime are required.", reason="time_range_missing")
            resp = cw.get_metric_data(
                MetricDataQueries=metric_queries,
                StartTime=start_time,
                EndTime=end_time,
            )
            results = [
                {
                    "id": r.get("Id"),
                    "label": r.get("Label"),
                    "timestamps": [str(t) for t in r.get("Timestamps", [])],
                    "values": r.get("Values", []),
                    "statusCode": r.get("StatusCode"),
                }
                for r in resp.get("MetricDataResults", [])
            ]
            result = {"count": len(results), "metricDataResults": results}
            return _build_result("completed", f"Retrieved metric data for {len(results)} query(s).", result, "\n".join(log_lines))

        if normalized == "aws.ec2.describe_instance":
            instance_id = str(payload.get("instanceId") or payload.get("instance_id") or "").strip()
            if not instance_id:
                return _build_result("failed", "instanceId is required.", reason="instance_id_missing")
            ec2 = boto3.client("ec2", region_name=region)
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            if not reservations or not reservations[0].get("Instances"):
                return _build_result("failed", f"Instance {instance_id} not found.", reason="instance_not_found")
            item = reservations[0]["Instances"][0]
            result = {
                "instanceId": item.get("InstanceId"),
                "state": item.get("State", {}).get("Name"),
                "instanceType": item.get("InstanceType"),
                "availabilityZone": item.get("Placement", {}).get("AvailabilityZone"),
                "privateIp": item.get("PrivateIpAddress"),
                "publicIp": item.get("PublicIpAddress"),
                "launchTime": str(item.get("LaunchTime") or ""),
                "name": _tag_value(item.get("Tags"), "Name"),
                "tags": item.get("Tags", []),
                "securityGroups": [
                    {"groupId": sg.get("GroupId"), "groupName": sg.get("GroupName")}
                    for sg in item.get("SecurityGroups", [])
                ],
                "iamInstanceProfile": item.get("IamInstanceProfile", {}).get("Arn"),
                "architecture": item.get("Architecture"),
                "platform": item.get("Platform"),
                "imageId": item.get("ImageId"),
                "vpcId": item.get("VpcId"),
                "subnetId": item.get("SubnetId"),
            }
            return _build_result("completed", f"Described instance {instance_id}.", result, "\n".join(log_lines))

        if normalized == "aws.s3.get_bucket_policy":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            s3 = boto3.client("s3", region_name=region)
            try:
                resp = s3.get_bucket_policy(Bucket=bucket)
                import json as _json
                policy_str = resp.get("Policy") or "{}"
                try:
                    policy_doc = _json.loads(policy_str)
                except Exception:
                    policy_doc = policy_str
                result = {"bucket": bucket, "policy": policy_doc}
            except ClientError as ce:
                if ce.response.get("Error", {}).get("Code") == "NoSuchBucketPolicy":
                    result = {"bucket": bucket, "policy": None, "message": "No bucket policy attached."}
                else:
                    raise
            return _build_result("completed", f"Retrieved policy for bucket {bucket}.", result, "\n".join(log_lines))

        if normalized == "aws.s3.head_object":
            bucket = _resolve_bucket(payload)
            if not bucket:
                return _build_result("failed", "Bucket is required.", reason="bucket_missing")
            key = str(payload.get("key") or payload.get("objectKey") or "").strip()
            if not key:
                return _build_result("failed", "Object key is required.", reason="key_missing")
            s3 = boto3.client("s3", region_name=region)
            resp = s3.head_object(Bucket=bucket, Key=key)
            result = {
                "bucket": bucket,
                "key": key,
                "contentLength": resp.get("ContentLength"),
                "contentType": resp.get("ContentType"),
                "lastModified": str(resp.get("LastModified") or ""),
                "eTag": resp.get("ETag"),
                "versionId": resp.get("VersionId"),
                "metadata": resp.get("Metadata", {}),
            }
            return _build_result("completed", f"Head object s3://{bucket}/{key}.", result, "\n".join(log_lines))

        if normalized == "aws.iam.get_role":
            role_name = str(payload.get("roleName") or payload.get("role_name") or "").strip()
            if not role_name:
                return _build_result("failed", "roleName is required.", reason="role_name_missing")
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            resp = iam.get_role(RoleName=role_name)
            role = resp.get("Role", {})
            import json as _json
            assume_policy = role.get("AssumeRolePolicyDocument") or {}
            result = {
                "roleName": role.get("RoleName"),
                "arn": role.get("Arn"),
                "createDate": str(role.get("CreateDate") or ""),
                "description": role.get("Description"),
                "maxSessionDuration": role.get("MaxSessionDuration"),
                "assumeRolePolicyDocument": assume_policy,
                "tags": role.get("Tags", []),
            }
            return _build_result("completed", f"Retrieved role {role_name}.", result, "\n".join(log_lines))

        if normalized == "aws.iam.get_role_policy":
            role_name = str(payload.get("roleName") or payload.get("role_name") or "").strip()
            policy_name = str(payload.get("policyName") or payload.get("policy_name") or "").strip()
            if not role_name or not policy_name:
                return _build_result("failed", "roleName and policyName are required.", reason="params_missing")
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            resp = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
            policy_doc = resp.get("PolicyDocument") or {}
            result = {
                "roleName": resp.get("RoleName"),
                "policyName": resp.get("PolicyName"),
                "policyDocument": policy_doc,
            }
            return _build_result("completed", f"Retrieved inline policy {policy_name} for role {role_name}.", result, "\n".join(log_lines))

        if normalized == "aws.iam.list_attached_role_policies":
            role_name = str(payload.get("roleName") or payload.get("role_name") or "").strip()
            if not role_name:
                return _build_result("failed", "roleName is required.", reason="role_name_missing")
            iam_region = str(payload.get("iamRegion") or "us-east-1").strip() or "us-east-1"
            iam = boto3.client("iam", region_name=iam_region)
            policies: List[Dict[str, Any]] = []
            for page in iam.get_paginator("list_attached_role_policies").paginate(RoleName=role_name):
                for p in page.get("AttachedPolicies", []):
                    policies.append({"policyName": p.get("PolicyName"), "policyArn": p.get("PolicyArn")})
            result = {"roleName": role_name, "count": len(policies), "attachedPolicies": policies}
            return _build_result("completed", f"Listed {len(policies)} attached policies for role {role_name}.", result, "\n".join(log_lines))

        if normalized == "aws.ec2.list_security_groups":
            regions = _resolve_regions(payload, region, default_all=False)
            sgs: List[Dict[str, Any]] = []
            filters = payload.get("filters") or payload.get("Filters") or []
            for reg in regions:
                ec2 = boto3.client("ec2", region_name=reg)
                paginator = ec2.get_paginator("describe_security_groups")
                paginate_kwargs: Dict[str, Any] = {}
                if filters:
                    paginate_kwargs["Filters"] = filters
                for page in paginator.paginate(**paginate_kwargs):
                    for sg in page.get("SecurityGroups", []):
                        sgs.append(
                            {
                                "region": reg,
                                "groupId": sg.get("GroupId"),
                                "groupName": sg.get("GroupName"),
                                "description": sg.get("Description"),
                                "vpcId": sg.get("VpcId"),
                            }
                        )
                        if len(sgs) >= _resolve_max_results(payload, 300):
                            break
                    if len(sgs) >= _resolve_max_results(payload, 300):
                        break
                if len(sgs) >= _resolve_max_results(payload, 300):
                    break
            result = {"count": len(sgs), "securityGroups": sgs}
            return _build_result("completed", f"Listed {len(sgs)} security groups.", result, "\n".join(log_lines))

        if normalized == "aws.ec2.list_volumes":
            regions = _resolve_regions(payload, region, default_all=False)
            volumes: List[Dict[str, Any]] = []
            filters = payload.get("filters") or payload.get("Filters") or []
            for reg in regions:
                ec2 = boto3.client("ec2", region_name=reg)
                paginator = ec2.get_paginator("describe_volumes")
                paginate_kwargs: Dict[str, Any] = {}
                if filters:
                    paginate_kwargs["Filters"] = filters
                for page in paginator.paginate(**paginate_kwargs):
                    for v in page.get("Volumes", []):
                        volumes.append(
                            {
                                "region": reg,
                                "volumeId": v.get("VolumeId"),
                                "state": v.get("State"),
                                "size": v.get("Size"),
                                "volumeType": v.get("VolumeType"),
                                "availabilityZone": v.get("AvailabilityZone"),
                                "encrypted": v.get("Encrypted"),
                                "attachments": [
                                    {"instanceId": a.get("InstanceId"), "device": a.get("Device"), "state": a.get("State")}
                                    for a in v.get("Attachments", [])
                                ],
                            }
                        )
                        if len(volumes) >= _resolve_max_results(payload, 300):
                            break
                    if len(volumes) >= _resolve_max_results(payload, 300):
                        break
                if len(volumes) >= _resolve_max_results(payload, 300):
                    break
            result = {"count": len(volumes), "volumes": volumes}
            return _build_result("completed", f"Listed {len(volumes)} EBS volumes.", result, "\n".join(log_lines))

        return _build_result("failed", "Unsupported action.", reason="unsupported_action")
    except Exception as exc:
        return _build_result(
            "failed",
            "Execution failed.",
            result={"error": str(exc)},
            logs="\n".join(log_lines),
            reason=_format_error(exc),
        )
