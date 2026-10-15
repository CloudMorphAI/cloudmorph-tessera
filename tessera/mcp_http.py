"""Stateless MCP HTTP boundary; JSON replies, with no server-push stream.

Upstream transport sessions are deliberately independent of downstream clients.
The proxy never accepts a client-supplied session as an authentication identity.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from tessera import __version__

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MODERN_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_MODERN_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION,)
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
HEADER_MISMATCH_CODE = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY_CODE = -32021
UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022
MAX_REQUEST_BYTES = 1024 * 1024
MAX_MODERN_RESPONSE_BYTES = 1024 * 1024
AUDIT_ID_HEADER = "X-Tessera-Audit-Event-Id"


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


def _explicitly_accepts(value: str, media_type: str) -> bool:
    """Check that a required media type is named explicitly, not via a wildcard."""
    qualities: list[float] = []
    for entry in value.lower().split(","):
        parts = [part.strip() for part in entry.split(";")]
        if parts[0] != media_type.lower():
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
        qualities.append(quality)
    return bool(qualities) and max(qualities) > 0


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
    if len(versions) > 1:
        return rpc_http_error(
            "Duplicate MCP-Protocol-Version headers are not allowed", 400, HEADER_MISMATCH_CODE
        )
    is_modern = bool(versions and versions[0] == MODERN_PROTOCOL_VERSION)
    if request.headers.get("mcp-session-id") is not None and not is_modern:
        return rpc_http_error("This Tessera endpoint is stateless; omit MCP-Session-Id", 400)
    if request.method == "POST":
        content_type_header = request.headers.get("content-type")
        if is_modern and content_type_header is None:
            return rpc_http_error("Modern MCP requests require Content-Type: application/json", 415)
        content_type = (content_type_header or "application/json").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return rpc_http_error("Content-Type must be application/json", 415)
        accept_values = request.headers.getlist("accept")
        accept = ",".join(accept_values) if accept_values else "*/*"
        if is_modern and not all(_explicitly_accepts(accept, media_type) for media_type in (
            "application/json", "text/event-stream"
        )):
            return rpc_http_error(
                "Modern MCP requests must accept application/json and text/event-stream", 406
            )
        if not is_modern and not _accepts_json(accept):
            return rpc_http_error("This endpoint returns application/json", 406)
    return None


def _adaptation_error(response: Response, message: str, rpc_id: Any = None) -> JSONResponse:
    """Return a small failure without losing the proxy's audit correlation."""
    headers = {
        key: value for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    error: dict[str, Any] = {"code": -32603, "message": message}
    audit_id = response.headers.get(AUDIT_ID_HEADER)
    if audit_id:
        error["data"] = {"_meta": {"tessera_audit_event_id": audit_id}}
    failure = JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": error}, status_code=502,
        headers=headers, background=response.background,
    )
    if len(failure.body) > MAX_MODERN_RESPONSE_BYTES:
        # An oversized client-supplied request ID must not defeat the output cap.
        return _adaptation_error(response, message)
    return failure


async def adapt_modern_http_response(response: Response, method: str, rpc_id: Any) -> Response:
    """Buffer at most 1 MiB for adaptation, stopping oversized iterators early.

    This bounds the adapter's allocation, not the legacy upstream transport's
    existing response materialization. Auth/routing failures never enter here.
    """
    response_body = getattr(response, "body", None)
    if not isinstance(response_body, bytes):
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response
        buffered = bytearray()
        try:
            async for chunk in body_iterator:
                if isinstance(chunk, str) and len(chunk) > MAX_MODERN_RESPONSE_BYTES - len(buffered):
                    return _adaptation_error(response, "Upstream response exceeds adaptation limit", rpc_id)
                chunk = chunk if isinstance(chunk, bytes) else str(chunk).encode()
                if len(chunk) > MAX_MODERN_RESPONSE_BYTES - len(buffered):
                    return _adaptation_error(response, "Upstream response exceeds adaptation limit", rpc_id)
                buffered.extend(chunk)
        finally:
            close = getattr(body_iterator, "aclose", None)
            if close is not None:
                await close()
        response_body = bytes(buffered)
    if not response_body:
        return response
    return modernize_http_response(response, method, response_body, rpc_id=rpc_id)


def modernize_http_response(
    response: Response, method: str, response_body: bytes | None = None, *, rpc_id: Any = None
) -> Response:
    """Add modern result metadata without changing the legacy upstream contract."""
    if response_body is None:
        response_body = getattr(response, "body", None)
    if not isinstance(response_body, bytes):
        return response
    if len(response_body) > MAX_MODERN_RESPONSE_BYTES:
        return _adaptation_error(response, "Upstream response exceeds adaptation limit", rpc_id)
    headers = {
        key: value for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _adaptation_error(response, "Invalid upstream JSON-RPC response", rpc_id)
    if not isinstance(payload, dict):
        return _adaptation_error(response, "Invalid upstream JSON-RPC response", rpc_id)
    error = payload.get("error")
    if isinstance(error, dict) and error.get("code") == -32601 and response.status_code < 400:
        return JSONResponse(payload, status_code=404, headers=headers, background=response.background)
    if "error" in payload:
        if not isinstance(error, dict):
            return _adaptation_error(response, "Invalid upstream JSON-RPC error", rpc_id)
        return Response(
            response_body, status_code=response.status_code, headers=headers,
            media_type="application/json", background=response.background,
        )
    if not isinstance(payload.get("result"), dict):
        return _adaptation_error(response, "Invalid upstream JSON-RPC result", rpc_id)

    result = payload["result"]
    meta = result.get("_meta")
    if meta is None:
        meta = {}
        result["_meta"] = meta
    elif not isinstance(meta, dict):
        return _adaptation_error(response, "Invalid upstream result metadata", rpc_id)
    # The legacy upstream translation has no MRTR/InputRequiredResult support.
    result["resultType"] = "complete"
    meta[SERVER_INFO_META_KEY] = {"name": "tessera", "version": __version__}
    if method in {
        "server/discover", "tools/list", "prompts/list", "resources/list",
        "resources/read", "resources/templates/list",
    }:
        # All data is authenticated and tenant-scoped. Never permit shared caching.
        result["ttlMs"] = 0
        result["cacheScope"] = "private"

    try:
        adapted = JSONResponse(
            payload, status_code=response.status_code, headers=headers, background=response.background
        )
    except (ValueError, RecursionError):
        return _adaptation_error(response, "Invalid upstream JSON-RPC response", rpc_id)
    if len(adapted.body) > MAX_MODERN_RESPONSE_BYTES:
        return _adaptation_error(response, "Upstream response exceeds adaptation limit", rpc_id)
    return adapted


def _modern_error(message: str, rpc_id: Any, code: int = HEADER_MISMATCH_CODE,
                  data: dict[str, Any] | None = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": error}, status_code=400
    )


def _decode_modern_name_header(value: str) -> str | None:
    """Decode a safe MCP name header, including the spec's base64 sentinel."""
    prefix, suffix = "=?base64?", "?="
    if value.startswith(prefix) and value.endswith(suffix):
        encoded = value[len(prefix):-len(suffix)]
        try:
            raw = base64.b64decode(encoded, validate=True)
            return raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
    if any(ord(char) < 0x20 and char != "\t" for char in value):
        return None
    if any(ord(char) > 0x7e for char in value) or value != value.strip():
        return None
    return value


def validate_modern_request(request: Request, body: dict[str, Any]) -> JSONResponse | None:
    """Validate 2026 per-request metadata and its routing headers.

    This intentionally runs before unified namespaced tools are rewritten for
    a legacy upstream, so validation is against the actual client's request.
    """
    versions = request.headers.getlist("mcp-protocol-version")
    params = body.get("params", {})
    meta = params.get("_meta", {}) if isinstance(params, dict) else {}
    meta_version_present = isinstance(meta, dict) and PROTOCOL_VERSION_META_KEY in meta
    meta_version = meta.get(PROTOCOL_VERSION_META_KEY) if isinstance(meta, dict) else None
    header_version = versions[0] if len(versions) == 1 else None
    is_modern = (
        (header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS)
        or meta_version_present
        or bool(request.headers.getlist("mcp-method"))
        or bool(request.headers.getlist("mcp-name"))
    )
    if not is_modern:
        return None

    rpc_id = body.get("id")
    if header_version != meta_version:
        return _modern_error("MCP-Protocol-Version header does not match request metadata", rpc_id)
    if header_version not in SUPPORTED_MODERN_PROTOCOL_VERSIONS:
        return _modern_error(
            "Unsupported protocol version", rpc_id, UNSUPPORTED_PROTOCOL_VERSION_CODE,
            {"supported": list(SUPPORTED_MODERN_PROTOCOL_VERSIONS), "requested": header_version},
        )

    if not isinstance(meta, dict) or not isinstance(meta.get(CLIENT_CAPABILITIES_META_KEY), dict):
        return _modern_error(
            "Missing required client capabilities", rpc_id,
            MISSING_REQUIRED_CLIENT_CAPABILITY_CODE,
        )
    client_info = meta.get(CLIENT_INFO_META_KEY)
    if client_info is not None and (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return _modern_error("clientInfo requires string name and version", rpc_id)

    method = body.get("method")
    if method.startswith("notifications/") or method in {"initialize", "ping", "logging/setLevel"}:
        return rpc_http_error("Method not found", 404, -32601, rpc_id)
    method_headers = request.headers.getlist("mcp-method")
    if len(method_headers) != 1 or method_headers[0] != method:
        return _modern_error("Mcp-Method header does not match request method", rpc_id)

    name_required = {
        "tools/call": "name",
        "resources/read": "uri",
        "prompts/get": "name",
    }
    name_headers = request.headers.getlist("mcp-name")
    expected_key = name_required.get(method)
    if expected_key is None:
        if name_headers:
            return _modern_error("Mcp-Name is not valid for this method", rpc_id)
    else:
        expected_name = params.get(expected_key) if isinstance(params, dict) else None
        if not isinstance(expected_name, str) or not expected_name or len(name_headers) != 1:
            return _modern_error("Mcp-Name header is required and must match the request", rpc_id)
        decoded_name = _decode_modern_name_header(name_headers[0])
        if decoded_name is None or decoded_name != expected_name:
            return _modern_error("Mcp-Name header does not match request parameters", rpc_id)
    request.state.mcp_modern_validated = True
    request.state.mcp_rpc_id = rpc_id
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
    params = body.get("params", {})
    meta = params.get("_meta", {}) if isinstance(params, dict) else {}
    modern = isinstance(meta, dict) and meta.get(PROTOCOL_VERSION_META_KEY) == MODERN_PROTOCOL_VERSION
    if method == "server/discover":
        if modern:
            return JSONResponse({
                "jsonrpc": "2.0", "id": body.get("id"),
                "result": {
                    "resultType": "complete",
                    "supportedVersions": list(SUPPORTED_MODERN_PROTOCOL_VERSIONS),
                    "capabilities": {"tools": {}},
                    "_meta": {SERVER_INFO_META_KEY: {"name": "tessera", "version": __version__}},
                    "instructions": "Stateless policy proxy. Tool calls are policy checked before forwarding.",
                    "ttlMs": 0,
                    "cacheScope": "private",
                },
            })
        return rpc_http_error("server/discover requires modern request metadata", 400, -32601, body.get("id"))
    if modern and method in {"initialize", "notifications/initialized", "ping", "logging/setLevel"}:
        return rpc_http_error("Method not found", 404, -32601, body.get("id"))
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
