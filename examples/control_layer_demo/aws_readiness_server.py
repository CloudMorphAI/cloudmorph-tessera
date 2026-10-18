"""Authenticated loopback MCP adapter for bounded AWS readiness reads.

AWS access uses the official SigV4 MCP SDK and the normal boto3 credential
provider chain. The only script accepted is the fixed read-only Amplify query
below. Proposal tools are inert local fixtures; they never call AWS or a
diagnostics client. The local bearer token is held only in process memory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

AWS_ENDPOINT = "https://aws-mcp.us-east-1.api.aws/mcp"
AWS_SERVICE, AWS_REGION = "aws-mcp", "us-east-1"
AWS_OPERATION_TIMEOUT, AWS_INITIAL_READ_TIMEOUT, AWS_POLL_READ_TIMEOUT = 30, 20, 8
BACKEND_APP_ID, TESSERA_UI_APP_ID = "d3agonv0r2imi7", "d2nxbpvv53qeqc"
APP_IDS = (BACKEND_APP_ID, TESSERA_UI_APP_ID)
PINNED_JOBS = {BACKEND_APP_ID: "178", TESSERA_UI_APP_ID: "83"}
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_BODY, MAX_REPLY, MAX_CALLS = 65536, 262144, 100
TOKEN_ENV = "CM_READINESS_UPSTREAM_TOKEN"  # noqa: S105 - env var name, never a token value
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}\Z")

READONLY_SCRIPT = '''targets = (
    {"appId": "d3agonv0r2imi7", "jobId": "178"},
    {"appId": "d2nxbpvv53qeqc", "jobId": "83"},
)

async def get_job(target):
    return await call_boto3(
        service_name="amplify",
        operation_name="GetJob",
        region_name="us-east-1",
        params={"appId": target["appId"], "branchName": "main", "jobId": target["jobId"]},
    )

responses = await asyncio.gather(
    *[get_job(target) for target in targets], return_exceptions=True
)
rows = []
for target, response in zip(targets, responses):
    if isinstance(response, BaseException):
        raise response
    job = response.get("job", {}).get("summary", {}) if isinstance(response, dict) else {}
    rows.append({
        "appId": target["appId"],
        "jobId": job.get("jobId", ""),
        "status": job.get("status", ""),
        "commitId": job.get("commitId", ""),
    })
result = {"jobs": rows}
result
'''
REGION_ARGS = {"regions": [AWS_REGION], "resource_type": "cfn",
               "filters": ["AWS::Lambda::Function", "AWS::DynamoDB::Table"]}
REGION_LABEL = "INERT LOCAL FIXTURE - NO AWS ACTION PERFORMED"

for _name in ("boto3", "botocore", "mcp_proxy_for_aws", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _schema_allows(schema: Any, expected_type: str) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == expected_type:
        return True
    return any(_schema_allows(option, expected_type)
               for union in ("anyOf", "oneOf")
               for option in schema.get(union, []) if isinstance(schema.get(union), list))


def _remote_tools(tools: list[Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate the actual catalog and derive an exact, narrow local catalog."""
    found: dict[str, dict[str, Any]] = {}
    for tool in tools:
        item = _plain(tool)
        name, schema = item.get("name"), item.get("inputSchema")
        if not isinstance(name, str) or name in found or not isinstance(schema, dict):
            raise ValueError("AWS MCP returned an invalid or duplicate tool schema")
        found[name] = schema

    run, regions, availability = (found.get("aws___run_script"), found.get("aws___list_regions"),
                                  found.get("aws___get_regional_availability"))
    if not all(isinstance(schema, dict) for schema in (run, regions, availability)):
        raise ValueError("AWS MCP lacks a required bounded readiness read tool")
    run_props = run.get("properties", {})
    if (run.get("type") != "object" or run.get("required") != ["code"]
            or not isinstance(run_props.get("code"), dict) or run_props["code"].get("type") != "string"):
        raise ValueError("AWS MCP run_script schema differs from the reviewed code contract")
    if regions.get("type") != "object" or regions.get("required", []):
        raise ValueError("AWS MCP list_regions schema is outside the no-argument contract")
    props = availability.get("properties", {})
    required = availability.get("required", [])
    if (availability.get("type") != "object" or set(required) != {"resource_type"}
            or props.get("regions", {}).get("type") != "array"
            or props["regions"].get("items", {}).get("type") != "string"
            or props.get("resource_type", {}).get("type") != "string"
            or props.get("filters", {}).get("type") != "array"
            or props["filters"].get("items", {}).get("type") != "string"):
        raise ValueError("AWS MCP regional availability schema differs from the reviewed filter contract")
    constrained_values = ((props["regions"], [AWS_REGION]),
                          (props["resource_type"], "cfn"),
                          (props["filters"], REGION_ARGS["filters"]))
    for prop_schema, expected in constrained_values:
        enum_values = prop_schema.get("enum")
        if enum_values is not None and expected not in enum_values:
            raise ValueError("AWS MCP availability schema excludes a fixed query value")
    for prop_name, expected_values in (("regions", [AWS_REGION]), ("filters", REGION_ARGS["filters"])):
        item_enum = props[prop_name].get("items", {}).get("enum")
        if item_enum is not None and any(value not in item_enum for value in expected_values):
            raise ValueError("AWS MCP availability item schema excludes a fixed query value")
    status_schema = found.get("aws___get_tasks")
    if status_schema is not None:
        status_props = status_schema.get("properties", {})
        status_ids = status_props.get("task_ids", {})
        if (status_schema.get("type") != "object"
                or set(status_schema.get("required", [])) != {"task_ids"}
                or status_ids.get("type") != "array"
                or status_ids.get("items", {}).get("type") != "string"
                or not _schema_allows(status_props.get("poll_iteration"), "integer")):
            raise ValueError("AWS MCP status schema is outside the bounded task ID contract")

    local = [
        {"name": "aws___run_script", "description": "Read two pinned main-branch CloudMorph Amplify jobs (backend job 178 and Tessera UI job 83); the GetJob script is fixed and read-only.",
         "inputSchema": {"type": "object", "properties": {"code": {"type": "string", "const": READONLY_SCRIPT}},
                         "required": ["code"], "additionalProperties": False}},
        {"name": "aws___list_regions", "description": "List AWS regions using the official read-only AWS MCP tool.",
         "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
        {"name": "aws___get_regional_availability", "description": "Check only static CloudFormation availability in us-east-1 for Lambda and DynamoDB resources.",
         "inputSchema": {"type": "object", "properties": {
             "regions": {"type": "array", "items": {"type": "string", "const": AWS_REGION}, "const": [AWS_REGION]},
             "resource_type": {"type": "string", "const": "cfn"},
             "filters": {"type": "array", "items": {"type": "string"}, "const": REGION_ARGS["filters"]}},
             "required": ["regions", "resource_type", "filters"], "additionalProperties": False}},
    ]
    return local + _fixture_tools(), found


def _fixture_tools() -> list[dict[str, Any]]:
    def schema(name: str) -> dict[str, Any]:
        return {"type": "object", "properties": {"request": {"type": "string", "const": name}},
                "required": ["request"], "additionalProperties": False}
    return [
        {"name": "fixture__request_access_change", "description": "Inert local access-change proposal; no IAM or AWS call.",
         "inputSchema": schema("production_access_change")},
        {"name": "fixture__request_diagnostics_review", "description": "Inert local diagnostics-review proposal; no diagnostics or AWS call.",
         "inputSchema": schema("read_only_diagnostics_review")},
    ]


def _accepts(schema: dict[str, Any], args: Any) -> bool:
    if not isinstance(args, dict) or set(args) != set(schema.get("required", [])):
        return False
    props = schema.get("properties", {})
    if set(args) - set(props):
        return False
    return all(isinstance(args[key], str) and args[key] == spec.get("const")
               for key, spec in props.items() if key in args and spec.get("type") == "string") and all(
        args.get(key) == spec.get("const") for key, spec in props.items()
        if key in args and spec.get("type") == "array")


class Ledger:
    def __init__(self, path: str | Path) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(path, flags, 0o600)
        os.fchmod(self.fd, 0o600)
        self.lock = threading.Lock()
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        with self.lock:
            os.write(self.fd, json.dumps(row, separators=(",", ":"), allow_nan=False).encode() + b"\n")
            os.fsync(self.fd)
            self.rows.append(dict(row))

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.rows]

    def close(self) -> None:
        os.close(self.fd)


class AWSReadOnly:
    async def _invoke(self, name: str | None = None, args: dict[str, Any] | None = None) -> Any:
        from mcp import ClientSession
        from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

        async with asyncio.timeout(AWS_OPERATION_TIMEOUT):
            async with aws_iam_streamablehttp_client(endpoint=AWS_ENDPOINT, aws_service=AWS_SERVICE,
                    aws_region=AWS_REGION, timeout=AWS_INITIAL_READ_TIMEOUT,
                    sse_read_timeout=AWS_INITIAL_READ_TIMEOUT) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    if initialized.protocolVersion not in PROTOCOLS:
                        raise RuntimeError("Unsupported AWS MCP protocol")
                    response = await session.list_tools()
                    catalog, schemas = _remote_tools(list(response.tools))
                    if name is None:
                        return catalog
                    plan = {"aws___run_script": {"code": READONLY_SCRIPT},
                            "aws___list_regions": {},
                            "aws___get_regional_availability": REGION_ARGS}.get(name)
                    if plan is None or args != plan:
                        raise ValueError("Tool arguments do not match the fixed read-only plan")
                    if name == "aws___run_script" and args != {"code": READONLY_SCRIPT}:
                        raise ValueError("Only the exact fixed script may be dispatched")
                    result = await session.call_tool(name, args,
                                read_timeout_seconds=timedelta(seconds=(
                                    AWS_INITIAL_READ_TIMEOUT if name == "aws___run_script"
                                    else AWS_POLL_READ_TIMEOUT)))
                    if name == "aws___run_script" and not result.isError:
                        has_task_marker, task_id = _async_task_id(result)
                        if has_task_marker and task_id is None:
                            return {"_cm_readiness_result": result,
                                    "_cm_task_marker_invalid": True}
                        if task_id:
                            info = _task_info(result, task_id)
                            initial_status = info["status"] if info else None
                            if initial_status == "working":
                                result = await _poll(session, task_id, schemas.get("aws___get_tasks"),
                                                     info.get("recommended_wait_seconds"))
                            return {"_cm_readiness_result": result,
                                    "_cm_expected_task_id": task_id}
                    return result

    def discover(self) -> list[dict[str, Any]]:
        return asyncio.run(self._invoke())

    def call(self, name: str, args: dict[str, Any]) -> Any:
        return asyncio.run(self._invoke(name, args))


def _plain_text(result: Any) -> list[str]:
    item = _plain(result)
    texts = [part["text"] for part in item.get("content", [])
             if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
    if isinstance(item, dict) and item.get("structuredContent") is not None:
        texts.append(json.dumps(item["structuredContent"], ensure_ascii=False, allow_nan=False))
    return [text[:65536] for text in texts]


def _json_values(text: str) -> list[Any]:
    try:
        return [json.loads(text.strip())]
    except (ValueError, RecursionError):
        return []


def _candidate_values(result: Any) -> list[Any]:
    item = _plain(result)
    candidates: list[Any] = []
    if isinstance(item, dict) and item.get("structuredContent") is not None:
        candidates.append(item["structuredContent"])
    for text in _plain_text(result):
        candidates.extend(_json_values(text))
    unique: dict[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = dict(candidate)
            for key in ("stderr", "warning"):
                if candidate.get(key, object()) is None:
                    candidate.pop(key)
        unique[json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)] = candidate
    return list(unique.values())


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _task_id_fields(value: dict[str, Any]) -> set[str]:
    ids = set()
    for key, candidate in value.items():
        normalized = key.lower().replace("-", "").replace("_", "")
        if normalized in {"taskid", "executionid"} and isinstance(candidate, str) and ID_RE.fullmatch(candidate):
            ids.add(candidate)
    return ids


def _async_task_id(result: Any) -> tuple[bool, str | None]:
    candidates = _candidate_values(result)
    ids: set[str] = set()
    marker_present = False
    malformed = False
    for candidate in candidates:
        for item in _walk_dicts(candidate):
            if "task" in item or "tasks" in item:
                marker_present = True
            for key, value in item.items():
                normalized = key.lower().replace("-", "").replace("_", "")
                if normalized not in {"taskid", "executionid"}:
                    continue
                marker_present = True
                if not isinstance(value, str) or not ID_RE.fullmatch(value):
                    malformed = True
                else:
                    ids.add(value)
    text_id = re.compile(
        r"\b(?:task|execution)[ _-]*id\b[\"']?\s*[:=]\s*[\"']?([^,\s\"'}]+)", re.I
    )
    for text in _plain_text(result):
        if re.search(r"\b(?:task|execution)[ _-]*id\b", text, re.I):
            marker_present = True
            matches = text_id.findall(text)
            if not matches:
                malformed = True
            for value in matches:
                if ID_RE.fullmatch(value):
                    ids.add(value)
                else:
                    malformed = True
    if len(candidates) > 1 and marker_present:
        malformed = True
    if not marker_present:
        return False, None
    return True, next(iter(ids)) if not malformed and len(ids) == 1 else None


def _task_info(result: Any, expected_id: str | None = None) -> dict[str, Any] | None:
    candidates = _candidate_values(result)
    if len(candidates) != 1:
        return None
    records = []
    for item in _walk_dicts(candidates[0]):
        if expected_id is not None and "tasks" in item:
            tasks = item["tasks"]
            if (not isinstance(tasks, list) or len(tasks) != 1
                    or not isinstance(tasks[0], dict) or _task_id_fields(tasks[0]) != {expected_id}):
                return None
        ids = _task_id_fields(item)
        status = item.get("status")
        if len(ids) != 1 or not isinstance(status, str):
            continue
        task_id = next(iter(ids))
        if expected_id is not None and task_id != expected_id:
            continue
        wait = next((v for k, v in item.items()
                     if k.lower().replace("-", "_") == "recommended_wait_seconds"), None)
        records.append({"task_id": task_id, "status": status.lower(),
                        "recommended_wait_seconds": wait})
    return records[0] if len(records) == 1 else None


def _select_task_record(root: Any, expected_id: str) -> dict[str, Any] | None:
    if not isinstance(root, dict):
        return None
    ids = _task_id_fields(root)
    if ids:
        if ids == {expected_id} and str(root.get("status", "")).lower() == "completed":
            return root
        return None
    selected = []
    if "tasks" in root:
        tasks = root["tasks"]
        if (not isinstance(tasks, list) or len(tasks) != 1
                or not isinstance(tasks[0], dict)
                or _task_id_fields(tasks[0]) != {expected_id}
                or str(tasks[0].get("status", "")).lower() != "completed"):
            return None
        selected.append(tasks[0])
    if "task" in root:
        nested = _select_task_record(root["task"], expected_id)
        if nested is None:
            return None
        selected.append(nested)
    return selected[0] if len(selected) == 1 else None


async def _poll(session: Any, task_id: str, schema: dict[str, Any] | None,
                first_wait: Any) -> Any:
    if not isinstance(schema, dict):
        return {"isError": True, "message": "Async AWS MCP result has no supported status tool."}
    props = schema.get("properties", {})
    task_ids = props.get("task_ids", {})
    if (set(schema.get("required", [])) != {"task_ids"}
            or task_ids.get("type") != "array"
            or task_ids.get("items", {}).get("type") != "string"
            or not _schema_allows(props.get("poll_iteration"), "integer")):
        return {"isError": True, "message": "AWS MCP status schema is outside the bounded polling contract."}
    result = None
    recommended_wait = first_wait
    deadline = asyncio.get_running_loop().time() + AWS_OPERATION_TIMEOUT
    for iteration in range(1, 4):
        if (isinstance(recommended_wait, bool) or not isinstance(recommended_wait, (int, float))
                or recommended_wait < 0 or recommended_wait > 30):
            return {"isError": True, "message": "Async AWS MCP result lacks a supported recommended wait."}
        remaining = deadline - asyncio.get_running_loop().time()
        if recommended_wait + AWS_POLL_READ_TIMEOUT > remaining:
            return {"isError": True, "message": "Async AWS MCP polling exceeded the bounded wait budget."}
        if recommended_wait:
            await asyncio.sleep(recommended_wait)
        result = await session.call_tool(
            "aws___get_tasks", {"task_ids": [task_id], "poll_iteration": iteration},
            read_timeout_seconds=timedelta(seconds=AWS_POLL_READ_TIMEOUT))
        if getattr(result, "isError", False):
            return result
        info = _task_info(result, task_id)
        if not info:
            return result
        if info["status"] != "working":
            return result
        recommended_wait = info.get("recommended_wait_seconds")
    return result


def _task_result(value: Any, expected_id: str | None) -> Any:
    if isinstance(value, str):
        for candidate in _json_values(value):
            if candidate == value:
                continue
            found = _task_result(candidate, expected_id)
            if found is not None:
                return found
        return None
    if isinstance(value, dict):
        if "tasks" in value:
            tasks = value["tasks"]
            if expected_id is None or not isinstance(tasks, list) or len(tasks) != 1:
                return None
            matching = [task for task in tasks if isinstance(task, dict)
                        and _task_id_fields(task) == {expected_id}]
            if len(matching) != 1 or str(matching[0].get("status", "")).lower() != "completed":
                return None
            return _task_result(matching[0], expected_id)
        if _task_id_fields(value):
            if expected_id is None or _task_id_fields(value) != {expected_id}:
                return None
            if str(value.get("status", "")).lower() != "completed":
                return None
            if "jobs" in value:
                return value["jobs"]
            for key in ("result", "return_value", "returnValue", "output", "payload", "content"):
                if key in value:
                    found = _task_result(value[key], expected_id)
                    if found is not None:
                        return found
            return None
        if "jobs" in value:
            return value["jobs"]
        for key in ("return_value", "returnValue", "result", "output", "stdout", "payload", "content", "text"):
            if key in value:
                found = _task_result(value[key], expected_id)
                if found is not None:
                    return found
        return None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                found = _task_result(item["text"], expected_id)
                if found is not None:
                    return found
    return None


def _valid_jobs(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or len(value) != len(APP_IDS):
        return None
    result_rows = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"appId", "jobId", "status", "commitId"}:
            return None
        app_id = item.get("appId")
        if (app_id not in APP_IDS or item.get("jobId") != PINNED_JOBS[app_id]
                or not isinstance(item.get("status"), str) or not item["status"]
                or not isinstance(item.get("commitId"), str)
                or any(len(item[key]) > 256 for key in ("jobId", "status", "commitId"))):
            return None
        result_rows[app_id] = {key: item[key] for key in ("appId", "jobId", "status", "commitId")}
    if set(result_rows) != set(APP_IDS):
        return None
    return [result_rows[app_id] for app_id in APP_IDS]


def _jobs(result: Any, expected_task_id: str | None = None) -> list[dict[str, str]] | None:
    if (expected_task_id is not None and isinstance(result, dict)
            and _task_id_fields(result) == {expected_task_id}):
        return _valid_jobs(_task_result(result, expected_task_id))
    candidates = _candidate_values(result)
    if len(candidates) != 1:
        return None
    return _valid_jobs(_task_result(candidates[0], expected_task_id))


def _api_call_lists(value: Any, expected_task_id: str | None,
                    task_scope: bool = False) -> list[list[Any]]:
    found: list[list[Any]] = []
    if isinstance(value, dict):
        task_ids = _task_id_fields(value)
        if task_ids:
            if expected_task_id is None or task_ids != {expected_task_id}:
                return []
            task_scope = True
        if expected_task_id is not None and not task_scope:
            if "tasks" in value:
                tasks = value["tasks"]
                if (not isinstance(tasks, list) or len(tasks) != 1
                        or not isinstance(tasks[0], dict)
                        or _task_id_fields(tasks[0]) != {expected_task_id}):
                    return []
                return _api_call_lists(tasks[0], expected_task_id, task_scope=True)
            if "task" in value:
                return _api_call_lists(value["task"], expected_task_id)
            return []
        calls = value.get("api_calls")
        if isinstance(calls, list):
            found.append(calls)
        for key in ("task", "result", "return_value", "returnValue", "output", "payload", "content", "text"):
            if key in value:
                found.extend(_api_call_lists(value[key], expected_task_id, task_scope))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                for parsed in _json_values(item["text"]):
                    found.extend(_api_call_lists(parsed, expected_task_id))
    elif isinstance(value, str):
        for parsed in _json_values(value):
            found.extend(_api_call_lists(parsed, expected_task_id))
    return found


def _verified_api_calls(result: Any, expected_task_id: str | None) -> bool:
    if (expected_task_id is not None and isinstance(result, dict)
            and _task_id_fields(result) == {expected_task_id}):
        call_lists = _api_call_lists(result, expected_task_id, task_scope=True)
    else:
        candidates = _candidate_values(result)
        if len(candidates) != 1:
            return False
        call_lists = [call_list for candidate in candidates
                      for call_list in _api_call_lists(candidate, expected_task_id)]
    if not call_lists:
        return False
    for calls in call_lists:
        if len(calls) != 2:
            return False
        for call in calls:
            if (not isinstance(call, dict) or set(call) != {"service", "operation", "status"}
                    or call.get("service") != "amplify" or call.get("operation") != "GetJob"
                    or call.get("status") != "success"):
                return False
    return True


def _metadata_diagnostic(result: Any) -> dict[str, Any]:
    plain = _plain(result)
    blocks = plain.get("content", []) if isinstance(plain, dict) else []
    paths: set[str] = set()
    interesting = {"api_calls", "return_value", "returnValue", "tasks", "task", "task_id", "taskId",
                   "status", "recommended_wait_seconds", "jobs", "result", "output", "stdout", "params"}

    def walk(value: Any, path: str, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key in interesting:
                    paths.add(child_path)
                walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:8]):
                walk(child, f"{path}[{index}]", depth + 1)

    for index, candidate in enumerate(_candidate_values(result)):
        walk(candidate, f"candidate[{index}]")
    return {"content_blocks": [{"type": block.get("type", "unknown"),
                                "text_chars": len(block.get("text", "")) if isinstance(block.get("text"), str) else None}
                               for block in blocks if isinstance(block, dict)][:8],
            "structured_content_keys": sorted(plain.get("structuredContent", {}).keys())[:32]
            if isinstance(plain, dict) and isinstance(plain.get("structuredContent"), dict) else [],
            "recognized_paths": sorted(paths)[:64]}


def _safe_result(name: str, result: Any) -> dict[str, Any]:
    expected_task_id = None
    task_marker_invalid = False
    if isinstance(result, dict) and "_cm_readiness_result" in result:
        expected_task_id = result.get("_cm_expected_task_id")
        task_marker_invalid = result.get("_cm_task_marker_invalid") is True
        result = result["_cm_readiness_result"]
    plain = _plain(result)
    if getattr(result, "isError", False) or isinstance(plain, dict) and plain.get("isError"):
        return {"source": "AWS_MCP", "execution": "read_only_error", "error": "The read-only AWS MCP request failed."}
    if name == "aws___run_script":
        if task_marker_invalid:
            return {"source": "AWS_MCP", "execution": "read_only_incomplete",
                    "error": "The result contained ambiguous task metadata; raw output was withheld.",
                    "diagnostic": _metadata_diagnostic(result)}
        if expected_task_id:
            candidates = _candidate_values(result)
            # Select one completed task first; jobs and API receipts must both
            # be found under this exact record, never in sibling/root fields.
            task_record = _select_task_record(candidates[0], expected_task_id) if len(candidates) == 1 else None
            if task_record is None:
                return {"source": "AWS_MCP", "execution": "read_only_incomplete",
                        "error": "The returned task identity/status was missing or ambiguous.",
                        "diagnostic": _metadata_diagnostic(result)}
            jobs = _jobs(task_record, expected_task_id)
            receipts_valid = _verified_api_calls(task_record, expected_task_id)
        else:
            candidates = _candidate_values(result)
            if len(candidates) != 1 or not isinstance(candidates[0], dict) \
                    or candidates[0].get("status") != "success":
                return {"source": "AWS_MCP", "execution": "read_only_incomplete",
                        "error": "The synchronous AWS MCP response was not a single successful native result.",
                        "diagnostic": _metadata_diagnostic(result)}
            has_task_marker, _ = _async_task_id(result)
            if has_task_marker:
                return {"source": "AWS_MCP", "execution": "read_only_incomplete",
                        "error": "Task metadata was present without a uniquely bound task identity; raw output was withheld.",
                        "diagnostic": _metadata_diagnostic(result)}
            jobs = _jobs(result)
            receipts_valid = _verified_api_calls(result, None)
        if jobs is None or not receipts_valid:
            return {"source": "AWS_MCP", "execution": "read_only_incomplete",
                    "error": "The fixed script result or its two GetJob API-call receipts were incomplete; raw output was withheld.",
                    "diagnostic": _metadata_diagnostic(result)}
        return {"source": "AWS_MCP", "execution": "read_only_complete", "cloudmorph_jobs": jobs}
    return {"source": "AWS_MCP", "execution": "read_only_complete",
            "result": "\n".join(_plain_text(result))[:65536]}


def make_server(*, run_id: str, ledger_path: str | Path, token: str | None = None,
                port: int = 8767, max_requests: int = MAX_CALLS) -> ThreadingHTTPServer:
    secret = token if token is not None else os.environ.get(TOKEN_ENV, "")
    if (not isinstance(secret, str) or not 32 <= len(secret) <= 512 or not secret.isascii()
            or any(ch.isspace() for ch in secret)):
        raise ValueError(f"{TOKEN_ENV} must be 32-512 ASCII non-whitespace characters")
    if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
        raise ValueError("run-id must be a bounded identifier")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Invalid loopback port")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= MAX_CALLS:
        raise ValueError("max-requests must be from 1 through 100")
    ledger, aws = Ledger(ledger_path), AWSReadOnly()
    try:
        catalog = aws.discover()
    except Exception:
        ledger.close()
        raise RuntimeError("AWS MCP read-only tools/list discovery failed; server was not started") from None
    state = {"lock": threading.Lock(), "semaphore": threading.BoundedSemaphore(4),
             "calls": 0, "jobs": [], "fixture_noop_count": 0}
    tools = catalog

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version, sys_version = "CloudMorphReadinessMCP", ""

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def reply(self, status: int, value: Any = None) -> None:
            body = b"" if value is None else json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                       separators=(",", ":")).encode()
            if len(body) > MAX_REPLY:
                status, body = 500, b""
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)
            self.close_connection = True

        def auth(self) -> bool:
            auth_headers, hosts = self.headers.get_all("Authorization", []), self.headers.get_all("Host", [])
            expected = "Bearer " + secret
            if len(auth_headers) != 1 or not auth_headers[0].isascii() or not secrets.compare_digest(auth_headers[0], expected):
                self.reply(401, {"error": "Unauthorized"})
                return False
            if hosts != [f"127.0.0.1:{self.server.server_port}"] or self.headers.get_all("Origin", []):
                self.reply(403, {"error": "Invalid local host or browser origin"})
                return False
            return True

        def do_GET(self) -> None:
            self.connection.settimeout(5)
            if not self.auth():
                return
            if self.path != "/evidence":
                self.reply(404, {"error": "Not found"})
                return
            with state["lock"]:
                jobs, noops = [dict(job) for job in state["jobs"]], state["fixture_noop_count"]
            contacts = ledger.snapshot()
            self.reply(200, {"run_id": run_id,
                "label": "Real read-only AWS MCP results plus inert local proposals; no deployment or access change",
                "contacts": contacts, "cloudmorph_jobs": jobs, "fixture_noop_count": noops,
                "successful_aws_reads": sum(c["source"] == "aws_mcp_readonly" and c["outcome_type"] == "read_only_complete" for c in contacts),
                "inert_fixture_proposals": sum(c["source"] == "inert_fixture" for c in contacts)})

        def do_POST(self) -> None:
            self.connection.settimeout(10)
            if not self.auth():
                return
            if self.path != "/mcp":
                self.reply(404, {"error": "Not found"})
                return
            lengths, content_types = self.headers.get_all("Content-Length", []), self.headers.get_all("Content-Type", [])
            versions = self.headers.get_all("MCP-Protocol-Version", [])
            if self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self.error(400, -32600, "A single Content-Length is required")
                return
            length = int(lengths[0])
            if not 1 <= length <= MAX_BODY:
                self.error(413, -32600, "Request body outside bounds")
                return
            if len(content_types) != 1 or content_types[0].partition(";")[0].strip().lower() != "application/json":
                self.error(415, -32600, "Content-Type must be application/json")
                return
            if len(versions) > 1 or versions and versions[0] not in PROTOCOLS:
                self.error(400, -32600, "Unsupported MCP protocol version")
                return
            try:
                raw = self.rfile.read(length)
                request = json.loads(raw.decode(), object_pairs_hook=_unique, parse_constant=_bad_number)
                if len(raw) != length:
                    raise ValueError
            except (ValueError, UnicodeError, OSError, RecursionError):
                self.error(400, -32700, "Invalid JSON body")
                return
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)
                    or set(request) - {"jsonrpc", "id", "method", "params"}):
                self.error(400, -32600, "Invalid JSON-RPC request")
                return
            rid, method, params = request.get("id"), request["method"], request.get("params", {})
            if "id" in request and (isinstance(rid, bool) or not isinstance(rid, (str, int)) or len(str(rid)) > 128):
                self.error(400, -32600, "Invalid request id")
                return
            if not isinstance(params, dict):
                self.error(400, -32602, "Params must be an object", rid)
                return
            if "id" not in request:
                self.reply(202 if method == "notifications/initialized" and not params else 400)
                return
            if method == "initialize":
                version = params.get("protocolVersion")
                if version not in PROTOCOLS:
                    self.error(400, -32602, "Unsupported protocol version", rid)
                    return
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "CloudMorph AWS Readiness Adapter", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list" and not set(params) - {"_meta"}:
                result = {"tools": tools}
            elif method == "tools/call":
                if set(params) - {"name", "arguments", "_meta"}:
                    self.error(400, -32602, "Unsupported tool call fields", rid)
                    return
                result, failed = self.dispatch(params)
                result = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                          "structuredContent": result, "isError": failed}
            else:
                self.error(200, -32601, "Method not found", rid)
                return
            self.reply(200, {"jsonrpc": "2.0", "id": rid, "result": result})

        def error(self, status: int, code: int, message: str, rid: Any = None) -> None:
            self.reply(status, {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

        def dispatch(self, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            name, args, meta = params.get("name"), params.get("arguments", {}), params.get("_meta", {})
            if not isinstance(name, str) or not isinstance(args, dict) or not isinstance(meta, dict):
                return {"error": "Invalid tool call envelope."}, True
            request_id, use_id = meta.get("request_id"), meta.get("tool_use_id")
            if (not isinstance(request_id, str) or not ID_RE.fullmatch(request_id)
                    or not isinstance(use_id, str) or not ID_RE.fullmatch(use_id) or meta.get("run_id") != run_id):
                return {"error": "Required run correlation metadata is missing."}, True
            with state["lock"]:
                if state["calls"] >= max_requests:
                    return {"error": "Readiness call limit reached."}, True
                state["calls"] += 1
            spec = next((tool for tool in tools if tool["name"] == name), None)
            if not spec or not _accepts(spec["inputSchema"], args):
                self.record(request_id, use_id, name, args, "invalid_arguments", "local_validation")
                return {"error": "Tool or arguments are outside the fixed readiness catalog."}, True
            if not state["semaphore"].acquire(timeout=5):
                self.record(request_id, use_id, name, args, "busy", "local_limit")
                return {"error": "Readiness adapter concurrency limit reached."}, True
            try:
                if name.startswith("fixture__"):
                    with state["lock"]:
                        state["fixture_noop_count"] += 1
                    self.record(request_id, use_id, name, args, "no_action_performed", "inert_fixture")
                    return {"status": "no_action_performed", "label": REGION_LABEL,
                            "message": "Synthetic proposal recorded locally. No AWS or diagnostics call was made."}, False
                expected = {"aws___run_script": {"code": READONLY_SCRIPT}, "aws___list_regions": {},
                            "aws___get_regional_availability": REGION_ARGS}.get(name)
                if expected is None or args != expected:
                    self.record(request_id, use_id, name, args, "read_rejected", "local_guard")
                    return {"error": "Only the exact fixed read-only arguments may be dispatched."}, True
                try:
                    result = _safe_result(name, aws.call(name, args))
                    outcome = result["execution"]
                    if name == "aws___run_script" and outcome == "read_only_complete":
                        with state["lock"]:
                            state["jobs"] = result["cloudmorph_jobs"]
                    self.record(request_id, use_id, name, args, outcome, "aws_mcp_readonly")
                    return result, outcome != "read_only_complete"
                except Exception:
                    self.record(request_id, use_id, name, args, "read_only_error", "aws_mcp_readonly")
                    return {"source": "AWS_MCP", "execution": "read_only_error",
                            "error": "The bounded AWS MCP read failed; no automatic retry was made."}, True
            finally:
                state["semaphore"].release()

        def record(self, request_id: str, use_id: str, name: str, args: dict[str, Any], outcome: str, source: str) -> None:
            ledger.add({"request_id": request_id, "tool_use_id": use_id, "tool_name": name,
                        "argument_digest": hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
                        "outcome_type": outcome, "source": source})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads, server.ledger = True, ledger
    return server


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _bad_number(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated loopback AWS readiness MCP adapter")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--ledger", "--db", dest="ledger", required=True,
                        help="New metadata-only JSONL path outside the checkout; mode 0600")
    parser.add_argument("--max-requests", type=int, default=MAX_CALLS)
    args = parser.parse_args()
    server = make_server(run_id=args.run_id, ledger_path=args.ledger, port=args.port,
                         max_requests=args.max_requests)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.ledger.close()


if __name__ == "__main__":
    main()
