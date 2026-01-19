from typing import Any, Dict, List, Optional
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _extract_action(job: Dict[str, Any]) -> str:
    action = job.get("action") or ""
    payload = job.get("payload") or {}
    if not action and isinstance(payload, dict):
        action = payload.get("action") or ""
    return str(action).strip()


def _extract_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        return payload.get("payload") or {}
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

        return _build_result("failed", "Unsupported action.", reason="unsupported_action")
    except Exception as exc:
        return _build_result(
            "failed",
            "Execution failed.",
            result={"error": str(exc)},
            logs="\n".join(log_lines),
            reason=_format_error(exc),
        )
