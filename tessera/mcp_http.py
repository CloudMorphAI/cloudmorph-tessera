"""Stateless MCP HTTP boundary; JSON replies, with no server-push stream.

Upstream transport sessions are deliberately independent of downstream clients.
The proxy never accepts a client-supplied session as an authentication identity.
"""

from __future__ import annotations

import json
import math
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from tessera import __version__

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_REQUEST_BYTES = 1024 * 1024


def canonical_origin(value: str) -> str:
    """Validate a serialized HTTP origin without trusting a request Host header."""
    if not value or value != value.strip() or any(ord(c) < 33 for c in value) or "?" in value or "#" in value:
        raise ValueError("Use an explicit HTTP(S) origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("Origin must contain only scheme, hostname and optional port")
    port = parsed.port  # raises ValueError for an invalid port
    if port == 0:
        raise ValueError("Invalid origin port")
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    if port is None or port == {"http": 80, "https": 443}[parsed.scheme]:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def rpc_http_error(message: str, status: int, code: int = -32600, rpc_id: Any = None) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _accepts_json(value: str) -> bool:
    """Honor explicit q=0 exclusions ahead of less-specific wildcard ranges."""
    matches: list[tuple[int, float]] = []
    for entry in value.lower().split(","):
        parts = [part.strip() for part in entry.split(";")]
        specificity = {"application/json": 2, "application/*": 1, "*/*": 0}.get(parts[0])
        if specificity is None:
            continue
        quality = 1.0
        for part in parts[1:]:
            if part.startswith("q="):
                try:
                    quality = float(part[2:])
                except ValueError:
                    quality = 0.0
                if not 0.0 <= quality <= 1.0:
                    quality = 0.0
        matches.append((specificity, quality))
    if not matches:
        return False
    highest = max(specificity for specificity, _ in matches)
    return max(q for specificity, q in matches if specificity == highest) > 0


def validate_http_headers(request: Request, allowed_origins: list[str]) -> JSONResponse | None:
    origins = request.headers.getlist("origin")
    if len(origins) > 1:
        return rpc_http_error("Multiple Origin headers are not allowed", 403)
    if origins:
        try:
            origin = canonical_origin(origins[0])
            host = urlsplit(origin).hostname
        except ValueError:
            return rpc_http_error("Origin is not allowed", 403)
        if host not in {"localhost", "127.0.0.1", "::1"} and origin not in allowed_origins:
            return rpc_http_error("Origin is not allowed", 403)

    versions = request.headers.getlist("mcp-protocol-version")
    if len(versions) > 1 or (versions and versions[0] not in SUPPORTED_PROTOCOL_VERSIONS):
        return rpc_http_error("Unsupported MCP-Protocol-Version", 400)
    if request.headers.get("mcp-session-id") is not None:
        return rpc_http_error("This Tessera endpoint is stateless; omit MCP-Session-Id", 400)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return rpc_http_error("Content-Type must be application/json", 415)
        if not _accepts_json(request.headers.get("accept", "*/*")):
            return rpc_http_error("This endpoint returns application/json", 406)
    return None


async def read_message(request: Request) -> dict[str, Any] | JSONResponse:
    """Read one UTF-8 envelope with bounded allocation, rejecting batch input."""
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            return rpc_http_error("MCP request exceeds 1 MiB", 413)
        raw.extend(chunk)
    try:
        def reject_constant(value: str) -> Any:
            raise ValueError(f"Invalid JSON constant: {value}")

        def finite_float(value: str) -> float:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("JSON numbers must be finite")
            return number

        def unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON member")
                result[key] = value
            return result

        body = json.loads(raw.decode("utf-8"), parse_constant=reject_constant,
                          parse_float=finite_float, object_pairs_hook=unique_members)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return rpc_http_error("Parse error", 400, -32700)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return rpc_http_error("Expected one JSON-RPC 2.0 message", 400)
    rpc_id = body.get("id")
    if "id" in body and type(rpc_id) not in {str, int}:
        return rpc_http_error("Request id must be a string or integer", 400)
    method = body.get("method")
    if not isinstance(method, str) or not method or "result" in body or "error" in body:
        return rpc_http_error("Unsolicited responses and invalid methods are not accepted", 400)
    if not isinstance(body.get("params", {}), dict):
        return rpc_http_error("MCP params must be an object", 400, -32602, rpc_id)
    if not isinstance(body.get("params", {}).get("_meta", {}), dict):
        return rpc_http_error("MCP metadata must be an object", 400, -32602, rpc_id)
    if method.startswith("notifications/"):
        if "id" in body:
            # Preserve the legacy request/response form for existing proxy callers.
            # Real MCP notifications never contain an id and receive empty 202.
            return body
    elif "id" not in body:
        return rpc_http_error("MCP requests require an id", 400)
    if method == "tools/call":
        params = body.get("params", {})
        if not isinstance(params.get("name"), str) or not params["name"]:
            return rpc_http_error("tools/call requires a tool name", 400, -32602, rpc_id)
        if not isinstance(params.get("arguments", {}), dict) or not isinstance(params.get("_meta", {}), dict):
            return rpc_http_error("Tool arguments and metadata must be objects", 400, -32602, rpc_id)
    return body


def local_lifecycle_response(body: dict[str, Any]) -> Response | None:
    """Terminate downstream initialization independently of upstream sessions."""
    method = body.get("method")
    if method == "initialize":
        params = body.get("params", {})
        requested = params.get("protocolVersion", "2025-03-26")
        if not isinstance(requested, str):
            return rpc_http_error("protocolVersion must be a string", 400, -32602, body.get("id"))
        if not isinstance(params.get("capabilities", {}), dict):
            return rpc_http_error("capabilities must be an object", 400, -32602, body.get("id"))
        info = params.get("clientInfo")
        if "clientInfo" in params and (
            not isinstance(info, dict) or not isinstance(info.get("name"), str)
            or not isinstance(info.get("version"), str)
        ):
            return rpc_http_error("clientInfo requires string name and version", 400, -32602, body.get("id"))
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return JSONResponse({
            "jsonrpc": "2.0", "id": body["id"],
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tessera", "version": __version__},
                "instructions": "Stateless policy proxy. Tools are checked before forwarding. No server push or resumption.",
            },
        })
    if method == "notifications/initialized":
        if "id" not in body:
            return Response(status_code=202)
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": {}})
    return None
