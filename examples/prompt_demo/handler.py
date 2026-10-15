"""HTTP API Lambda adapter. AWS transports are created only on live admission.

Local verification injects both budget_fn and converse_fn and never imports
boto3. Application-runtime IAM permits only the model and daily-budget table.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

try:
    from .runtime import DemoRuntimeError, MakerError, MAX_MESSAGE_CHARS, MODEL_ID, run_prompt
except ImportError:
    from runtime import DemoRuntimeError, MakerError, MAX_MESSAGE_CHARS, MODEL_ID, run_prompt

MAX_BODY_BYTES = 8192
DAILY_REQUEST_LIMIT = 100
_CLIENTS: dict[str, Any] = {}


class BudgetExceeded(RuntimeError):
    pass


class BudgetUnavailable(RuntimeError):
    pass


def _client(service: str) -> Any:
    if service not in _CLIENTS:
        import boto3
        from botocore.config import Config

        # total_max_attempts includes the initial attempt; 1 means NO retries.
        config = Config(connect_timeout=2, read_timeout=8,
                        retries={"total_max_attempts": 1, "mode": "standard"})
        _CLIENTS[service] = boto3.client(service, region_name=os.environ.get("AWS_REGION", "us-east-1"), config=config)
    return _CLIENTS[service]


def reserve_daily_budget() -> None:
    table = os.environ.get("BUDGET_TABLE", "").strip()
    if not table:
        raise BudgetUnavailable("Daily budget is unavailable")
    now = datetime.now(timezone.utc)
    expires = int((now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)).timestamp())
    try:
        _client("dynamodb").update_item(
            TableName=table,
            Key={"budget_key": {"S": "prompt-demo#" + now.date().isoformat()}},
            UpdateExpression="SET expires_at = :ttl ADD #used :one",
            ConditionExpression="attribute_not_exists(#used) OR #used < :limit",
            ExpressionAttributeNames={"#used": "used"},
            ExpressionAttributeValues={":ttl": {"N": str(expires)}, ":one": {"N": "1"},
                                       ":limit": {"N": str(DAILY_REQUEST_LIMIT)}},
        )
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            raise BudgetExceeded("The shared daily demo limit has been reached") from exc
        raise BudgetUnavailable("Daily budget is unavailable") from exc


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    # CORS is configured by the dedicated API deployment; no wildcard here.
    return {"statusCode": status, "headers": {"Content-Type": "application/json; charset=utf-8",
                                               "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            "body": json.dumps(payload, ensure_ascii=True, separators=(",", ":"))}


def handle(event: dict[str, Any], context: Any = None, *,
           converse_fn: Callable[..., dict[str, Any]] | None = None,
           budget_fn: Callable[[], None] | None = None) -> dict[str, Any]:
    """Inject both callables for a completely offline handler check."""
    request_id = str(uuid.uuid4())
    deadline = time.monotonic() + 24
    if context is not None and callable(getattr(context, "get_remaining_time_in_millis", None)):
        deadline = min(deadline, time.monotonic() + max(0, context.get_remaining_time_in_millis() / 1000 - 1.5))

    def error(status: int, code: str, message: str) -> dict[str, Any]:
        return _response(status, {"request_id": request_id, "code": code, "error": message})

    if not isinstance(event, dict):
        return error(400, "invalid_request", "Invalid request.")
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod", "")
    path = event.get("rawPath") or event.get("path", "")
    if method == "GET" and path == "/health":
        return _response(200, {"ok": True, "synthetic_tools": True, "service": "tessera-prompt-demo", "model": MODEL_ID,
                               "mode": "live_model_synthetic_tools", "checker": "tessera", "tools": "synthetic",
                               "configured": bool(os.environ.get("BUDGET_TABLE") and os.environ.get("ALLOWED_ORIGINS"))})
    if path != "/chat" or method != "POST":
        return error(404, "not_found", "Route not found.")
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return error(400, "invalid_request", "Invalid headers.")
    headers = {str(k).lower(): v for k, v in headers.items()}
    origins = {origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",") if origin.strip()}
    if not headers.get("origin") or headers["origin"] not in origins or headers["origin"] == "*":
        return error(403, "origin_not_allowed", "This origin is not allowed to run the demo.")
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return error(415, "invalid_content_type", "Send an application/json request.")
    body = event.get("body", "")
    if not isinstance(body, str):
        return error(400, "invalid_request", "Invalid request body.")
    try:
        # Reject before decoding/parsing. Base64 is permitted by API Gateway,
        # but its encoded representation also has a strict bounded ceiling.
        encoded = body.encode("utf-8")
        if len(encoded) > (4 * ((MAX_BODY_BYTES + 2) // 3) if event.get("isBase64Encoded") else MAX_BODY_BYTES):
            return error(413, "request_too_large", "Request body exceeds the demo limit.")
        raw = base64.b64decode(encoded, validate=True) if event.get("isBase64Encoded") else encoded
        if len(raw) > MAX_BODY_BYTES:
            return error(413, "request_too_large", "Request body exceeds the demo limit.")
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        return error(400, "invalid_json", "Request must contain valid UTF-8 JSON.")
    if not isinstance(payload, dict) or set(payload) != {"message"}:
        return error(400, "invalid_request", "Provide only the message field.")
    message = payload["message"]
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
        return error(400, "invalid_message", "Enter a message of 1 to 1,400 characters.")
    # No raw prompt, arguments, model output, audit rows or exceptions are logged.
    try:
        (budget_fn if budget_fn is not None else reserve_daily_budget)()
    except BudgetExceeded:
        return error(429, "daily_limit", "The shared daily demo limit has been reached. Try again tomorrow.")
    except Exception:
        return error(503, "budget_unavailable", "The demo admission budget is temporarily unavailable.")
    try:
        provider = converse_fn if converse_fn is not None else _client("bedrock-runtime").converse
        result = run_prompt(message, converse_fn=provider, request_id=request_id, deadline=deadline)
        return _response(200, result)
    except MakerError:
        return error(502, "model_unavailable", "The model could not complete this run. No successful result is being substituted.")
    except DemoRuntimeError:
        return error(502, "checker_unavailable", "The checker could not verify this run. Please try again later.")
    except Exception:
        return error(502, "run_unavailable", "The demo could not complete this run. Please try again later.")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context)
