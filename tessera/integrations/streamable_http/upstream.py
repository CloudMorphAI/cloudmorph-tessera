"""Bounded, session-isolated MCP Streamable HTTP upstream transport.

Downstream initialization is terminated by the proxy. This client negotiates
its own upstream lifecycle; session IDs never reach downstream clients.
See docs/STREAMABLE_HTTP_UPSTREAM.md for the supported subset.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
_SUPPORTED_VERSIONS = frozenset({"2025-11-25", "2025-06-18", "2025-03-26"})
_MAX_BYTES = 1_048_576
_MAX_LINE_CHARS = 65_536
_MAX_EVENT_CHARS = 262_144
_MAX_EVENTS = 256
_MAX_SCOPES = 128
_MAX_SCOPE_CHARS = 1024
_MAX_SESSION_CHARS = 1024


class _TransportError(Exception):
    """A fixed, non-sensitive reason safe to return to a caller."""


@dataclass
class _Session:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False
    session_id: str | None = None
    version: str | None = None
    last_used: float = field(default_factory=time.monotonic)
    active: int = 0


def _valid_id(value: Any) -> bool:
    return type(value) in (str, int)


def _same_id(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _decode_json(data: str | bytes) -> dict[str, Any]:
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig")
        value = json.loads(data, object_pairs_hook=_object_pairs,
                           parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise _TransportError("Upstream returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise _TransportError("Upstream JSON-RPC message must be an object")
    return value


def _message_kind(body: dict[str, Any]) -> str:
    if body.get("jsonrpc") != "2.0":
        raise _TransportError("JSON-RPC version must be 2.0")
    if "method" in body:
        if not isinstance(body["method"], str) or not body["method"]:
            raise _TransportError("JSON-RPC method must be a nonempty string")
        if "result" in body or "error" in body:
            raise _TransportError("JSON-RPC request contains response fields")
        if "params" in body and not isinstance(body["params"], dict):
            raise _TransportError("MCP params must be an object")
        if "id" not in body:
            return "notification"
        if not _valid_id(body["id"]):
            raise _TransportError("MCP request ID must be a string or integer")
        return "request"
    if "id" not in body or not _valid_id(body["id"]):
        raise _TransportError("MCP response ID must be a string or integer")
    if "params" in body or ("result" in body) == ("error" in body):
        raise _TransportError("JSON-RPC response must contain exactly one result or error")
    if "result" in body and not isinstance(body["result"], dict):
        raise _TransportError("MCP result must be an object")
    if "error" in body:
        error = body["error"]
        if (not isinstance(error, dict) or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)):
            raise _TransportError("JSON-RPC error must contain integer code and string message")
    return "response"


def _matching_response(body: dict[str, Any], request_id: Any) -> dict[str, Any]:
    kind = _message_kind(body)
    if kind == "request":
        raise _TransportError("Upstream server-initiated requests are unsupported")
    if kind != "response":
        raise _TransportError("Upstream did not return a JSON-RPC response")
    if not _same_id(body["id"], request_id):
        raise _TransportError("Upstream response ID does not match the request")
    return body


class _SSEParser:
    """Incremental UTF-8 SSE framing with bounded lines, events and count."""

    def __init__(self, request_id: Any) -> None:
        self.request_id = request_id
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.line: list[str] = []
        self.data: list[str] = []
        self.event_chars = 0
        self.events = 0
        self.after_cr = False

    def _line(self) -> dict[str, Any] | None:
        line = "".join(self.line)
        self.line.clear()
        if not line:
            self.events += 1
            if self.events > _MAX_EVENTS:
                raise _TransportError("Upstream SSE event limit exceeded")
            raw = "\n".join(self.data)
            self.data.clear()
            self.event_chars = 0
            if not raw:
                return None
            body = _decode_json(raw)
            kind = _message_kind(body)
            if kind == "request":
                raise _TransportError("Upstream server-initiated requests are unsupported")
            if kind == "notification":
                return None
            if _same_id(body["id"], self.request_id):
                return body
            return None
        if line.startswith(":"):
            return None
        field_name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field_name == "data":
            self.event_chars += len(value) + 1
            if self.event_chars > _MAX_EVENT_CHARS:
                raise _TransportError("Upstream SSE event size limit exceeded")
            self.data.append(value)
        return None

    def feed(self, chunk: bytes, *, final: bool = False) -> dict[str, Any] | None:
        try:
            text = self.decoder.decode(chunk, final=final)
        except UnicodeError as exc:
            raise _TransportError("Upstream SSE must be UTF-8") from exc
        for char in text:
            if self.after_cr:
                self.after_cr = False
                if char == "\n":
                    continue
            if char in ("\r", "\n"):
                self.after_cr = char == "\r"
                parsed = self._line()
                if parsed is not None:
                    return parsed
            else:
                self.line.append(char)
                if len(self.line) > _MAX_LINE_CHARS:
                    raise _TransportError("Upstream SSE line size limit exceeded")
        return None


class StreamableHttpUpstream:
    """Configured upstream/auth context with bounded per-principal sessions.

    Origin-only URLs retain /mcp. A URL path is used as the complete endpoint.
    Session scopes must be trusted proxy-derived keys. request_timeout_s bounds
    each HTTP phase and the entire forward, including lock and initialization.
    Idle records expire locally after session_timeout_s. Calls are never replayed.
    """

    _max_notification_body_bytes = 0

    def __init__(
        self, name: str, url: str, auth_header: str | None = None,
        session_timeout_s: int = 300, request_timeout_s: int = 10,
    ) -> None:
        for value in (session_timeout_s, request_timeout_s):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Streamable HTTP timeouts must be positive finite numbers")
        endpoint = httpx.URL(url)
        if (endpoint.scheme not in ("http", "https") or not endpoint.host
                or endpoint.fragment or endpoint.userinfo):
            raise ValueError("Streamable HTTP requires an HTTP(S) URL without credentials or fragment")
        if endpoint.path in ("", "/"):
            endpoint = endpoint.copy_with(path="/mcp")
        self.name = name
        self.url = str(endpoint)
        self.auth_header = auth_header
        self.session_timeout_s = session_timeout_s
        self.request_timeout_s = request_timeout_s
        self._client: httpx.AsyncClient | None = None
        self._sessions: dict[str, _Session] = {}

    async def __aenter__(self) -> StreamableHttpUpstream:
        if self._client is not None:
            raise RuntimeError("StreamableHttpUpstream already entered")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
        }
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        self._client = httpx.AsyncClient(headers=headers, auth=self._http_auth(), timeout=self.request_timeout_s, follow_redirects=False)
        return self

    def _http_auth(self) -> httpx.Auth | None:
        """Optional provider authentication, retaining this bounded transport."""
        return None

    def _enrich_response(self, body: dict[str, Any], response: httpx.Response) -> dict[str, Any]:
        """Provider metadata hook after bounded JSON-RPC validation."""
        return body

    def _accept_notification_body(self, body: bytes, response: httpx.Response) -> bool:
        return not body

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        client = self._client
        if client is None:
            return
        try:
            async with asyncio.timeout(self.request_timeout_s):
                for state in self._sessions.values():
                    if state.session_id and state.version:
                        try:
                            async with client.stream("DELETE", self.url, headers=self._headers(state)):
                                pass
                        except httpx.HTTPError:
                            continue
        except TimeoutError:
            pass
        finally:
            self._sessions.clear()
            self._client = None
            await client.aclose()

    def _state(self, session_scope: str) -> _Session:
        if not isinstance(session_scope, str) or not session_scope or len(session_scope) > _MAX_SCOPE_CHARS:
            raise _TransportError("Invalid upstream session scope")
        now = time.monotonic()
        for key, idle_state in list(self._sessions.items()):
            if idle_state.active == 0 and now - idle_state.last_used >= self.session_timeout_s:
                del self._sessions[key]
        state = self._sessions.get(session_scope)
        if state is None:
            if len(self._sessions) >= _MAX_SCOPES:
                raise _TransportError("Upstream session capacity reached; wait for an idle session to expire")
            state = _Session()
            self._sessions[session_scope] = state
        state.active += 1
        return state

    @staticmethod
    def _headers(state: _Session) -> dict[str, str]:
        headers: dict[str, str] = {}
        if state.version:
            headers["MCP-Protocol-Version"] = state.version
        if state.session_id:
            headers["Mcp-Session-Id"] = state.session_id
        return headers

    @staticmethod
    def _drop_session(state: _Session) -> None:
        state.initialized = False
        state.session_id = None
        state.version = None

    async def _exchange(
        self, body: dict[str, Any], state: _Session, *, initialize: bool = False,
    ) -> tuple[dict[str, Any], str | None]:
        if self._client is None:
            raise _TransportError("Upstream client is not running")
        expects_response = _message_kind(body) == "request"
        async with self._client.stream("POST", self.url, json=body, headers=self._headers(state)) as response:
            if response.status_code == 404 and state.session_id:
                self._drop_session(state)
                raise _TransportError("Upstream session expired (HTTP 404); request was not replayed. A new caller request can initialize a fresh session")
            if expects_response:
                if response.status_code != 200:
                    raise _TransportError(f"Upstream HTTP {response.status_code}; request was not replayed")
            elif response.status_code != 202:
                raise _TransportError(f"Upstream must accept notifications/responses with empty HTTP 202 (received {response.status_code})")
            session_id = response.headers.get("Mcp-Session-Id") if initialize else None
            if session_id is not None and (not session_id or len(session_id) > _MAX_SESSION_CHARS
                    or any(ord(char) < 0x21 or ord(char) > 0x7E for char in session_id)):
                raise _TransportError("Upstream returned an invalid session header")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise _TransportError("Compressed upstream responses are unsupported by this bounded transport")
            if expects_response and content_type not in ("application/json", "text/event-stream"):
                raise _TransportError("Upstream response Content-Type must be application/json or text/event-stream")
            parser = _SSEParser(body["id"]) if expects_response and content_type == "text/event-stream" else None
            total = 0
            data = bytearray()
            # Content-Encoding was rejected above; aiter_bytes also supports
            # already-buffered synthetic transports without re-reading streams.
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise _TransportError("Upstream response byte limit exceeded")
                if not expects_response:
                    data.extend(chunk)
                    if len(data) > self._max_notification_body_bytes:
                        raise _TransportError("Upstream HTTP 202 must have an empty body")
                elif parser is not None:
                    parsed = parser.feed(chunk)
                    if parsed is not None:
                        return self._enrich_response(parsed, response), session_id
                else:
                    data.extend(chunk)
            if not expects_response:
                if not self._accept_notification_body(bytes(data), response):
                    raise _TransportError("Upstream HTTP 202 returned an unsupported acknowledgment body")
                return {}, session_id
            if parser is not None:
                parser.feed(b"", final=True)
                raise _TransportError("Upstream SSE ended without a matching response; reconnection/resumption is unsupported and request was not replayed")
            parsed = _matching_response(_decode_json(bytes(data)), body["id"])
            return self._enrich_response(parsed, response), session_id

    async def _initialize_session(self, state: _Session) -> None:
        body = {
            "jsonrpc": "2.0", "id": "tessera-initialize", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "tessera-proxy", "version": "1.0.0"}},
        }
        parsed, session_id = await self._exchange(body, state, initialize=True)
        if "error" in parsed:
            raise _TransportError("Upstream initialization returned a JSON-RPC error")
        result = parsed["result"]
        version = result.get("protocolVersion")
        if not isinstance(version, str) or version not in _SUPPORTED_VERSIONS:
            raise _TransportError("Upstream negotiated an unsupported MCP protocol version")
        info = result.get("serverInfo")
        if (not isinstance(result.get("capabilities"), dict) or not isinstance(info, dict)
                or not isinstance(info.get("name"), str) or not isinstance(info.get("version"), str)):
            raise _TransportError("Upstream initialization result lacks valid capabilities/serverInfo")
        state.session_id = session_id
        state.version = version
        try:
            await self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}, state)
        except BaseException:
            self._drop_session(state)
            raise
        state.initialized = True

    async def forward(
        self, jsonrpc_body: dict[str, Any], *, session_scope: str = "default",
    ) -> dict[str, Any] | JSONResponse:
        """Forward once with isolated initialization and an overall deadline.

        Accepted notifications/responses return {}; inbound emits empty HTTP 202.
        Local failures return JSONResponse. Scope must be a trusted proxy-derived
        principal/context key, never a caller-supplied upstream session header.
        """
        request_id = jsonrpc_body.get("id") if isinstance(jsonrpc_body, dict) else None
        if not _valid_id(request_id):
            request_id = None
        state: _Session | None = None
        try:
            if not isinstance(jsonrpc_body, dict):
                raise _TransportError("MCP message must be an object")
            _message_kind(jsonrpc_body)
            if jsonrpc_body.get("method") in ("initialize", "notifications/initialized"):
                raise _TransportError("Downstream initialization must be terminated by the proxy")
            if self._client is None:
                raise _TransportError("Upstream client is not running")
            async with asyncio.timeout(self.request_timeout_s):
                state = self._state(session_scope)
                async with state.lock:
                    if not state.initialized:
                        await self._initialize_session(state)
                    parsed, _ = await self._exchange(jsonrpc_body, state)
                    return parsed
        except TimeoutError:
            return _sh_error_response(request_id, "Upstream total deadline exceeded; execution outcome may be unknown and request was not replayed")
        except _TransportError as exc:
            return _sh_error_response(request_id, str(exc))
        except httpx.HTTPError:
            logger.warning("event=streamable_http_transport_error")
            return _sh_error_response(request_id, "Upstream transport failed; execution outcome may be unknown and request was not replayed")
        except (ValueError, TypeError, RecursionError):
            return _sh_error_response(request_id, "Invalid MCP message or upstream transport data")
        finally:
            if state is not None:
                state.active -= 1
                state.last_used = time.monotonic()


def _sh_error(request_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32603, "message": "Internal error", "data": {"reason": message}}}


def _sh_error_response(request_id: Any, message: str) -> JSONResponse:
    return JSONResponse(_sh_error(request_id, message))
