"""FastMCP streamable-HTTP upstream client.

Routes JSON-RPC traffic to MCP servers that implement the MCP 2025-06-18
streamable-HTTP transport (a single ``/mcp`` endpoint that accepts POST).

Handles two response shapes from the upstream:
* ``application/json``: direct JSON-RPC envelope (used for ``initialize``).
* ``text/event-stream``: SSE stream; each ``data:`` line carries a JSON-RPC
  envelope.  Tessera returns the first envelope whose ``id`` matches the
  request (subsequent events are MCP notifications; logged, not forwarded).

Session-id lifecycle
--------------------
1. On the first tool call to a given upstream URL, Tessera issues an
   ``initialize`` JSON-RPC request.  The upstream returns ``Mcp-Session-Id``
   in the response headers.
2. The session-id is cached in an in-memory dict keyed by upstream name.
3. Every subsequent POST carries the ``Mcp-Session-Id`` header.
4. On 401 / 403 or a JSON-RPC ``session_expired`` error, Tessera drops the
   cached session-id, re-initialises, and retries once.  On a second
   failure the error is returned to the caller.

The cache is process-local.  Sessions do not survive a Tessera restart.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# One session cache keyed by upstream name.  Concurrent tenants share the
# session (the MCP server is stateless WRT session; session-id is just a
# routing hint).  Thread-safe in CPython due to GIL; asyncio-safe because
# only one coroutine writes at a time behind the await boundary.
_SESSION_CACHE: dict[str, str] = {}  # upstream_name → session_id

# MCP capabilities Tessera announces when it initialises a session.
_INITIALIZE_PARAMS: dict[str, Any] = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "tessera-proxy", "version": "0.5.1"},
}


class StreamableHttpUpstream:
    """Async upstream client for FastMCP streamable-HTTP servers.

    Each upstream configured with ``kind: mcp_streamable_http`` gets one
    instance.  The instance holds a long-lived ``httpx.AsyncClient`` for
    keep-alive connection reuse.  Create via the async context manager so
    the client is properly shut down on process exit.

    Args:
        name: Upstream name from tessera.yaml (for logging and cache key).
        url: Base URL of the upstream MCP server (e.g. ``http://127.0.0.1:8000``).
        auth_header: Optional ``Authorization: Bearer <token>`` header value
            (the whole header *value* string, not the header name).  Pass
            ``None`` for unauthenticated upstreams (``AUTH_TYPE=no-auth``).
        session_timeout_s: Idle-session TTL in seconds (not enforced client-side
            today; reserved for future server-driven expiry handling).
        request_timeout_s: Per-request HTTP timeout in seconds.  Default 10.
    """

    def __init__(
        self,
        name: str,
        url: str,
        auth_header: str | None = None,
        session_timeout_s: int = 300,
        request_timeout_s: int = 10,
    ) -> None:
        self.name = name
        # Normalise: strip trailing slash so we can always append "/mcp".
        self.url = url.rstrip("/")
        self.auth_header = auth_header
        self.session_timeout_s = session_timeout_s
        self.request_timeout_s = request_timeout_s
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> StreamableHttpUpstream:
        # MCP 2025-06-18 spec: client MUST declare Accept for both shapes since
        # initialize returns application/json and tools/call returns text/event-stream.
        # Upstreams (e.g. awslabs.aws-api-mcp-server) enforce this and return 406
        # if only one content-type is listed.
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=self.request_timeout_s,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        # Drop cached session on shutdown so a restart always re-initialises.
        _SESSION_CACHE.pop(self.name, None)

    # ── Session management ────────────────────────────────────────────────────

    async def _initialize_session(self) -> str | None:
        """Issue a JSON-RPC ``initialize`` and return the session-id header.

        Returns ``None`` when the upstream does not echo ``Mcp-Session-Id``
        (i.e. when the server does not require session pinning; calls still
        work — we just won't send the header).
        """
        if self._client is None:
            return None

        init_body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": _INITIALIZE_PARAMS,
        }

        mcp_url = f"{self.url}/mcp"
        try:
            resp = await self._client.post(mcp_url, json=init_body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=streamable_http_init_error upstream=%s error=%s",
                self.name,
                exc,
            )
            return None

        session_id: str | None = resp.headers.get("Mcp-Session-Id")
        logger.info(
            "event=streamable_http_session_initialized upstream=%s session_id=%s",
            self.name,
            session_id,
        )
        return session_id

    async def _get_or_init_session(self) -> str | None:
        """Return cached session-id, initialising on first call."""
        if self.name not in _SESSION_CACHE:
            sid = await self._initialize_session()
            if sid:
                _SESSION_CACHE[self.name] = sid
            else:
                # Upstream has no session pinning — mark with sentinel so we
                # don't re-init on every call.
                _SESSION_CACHE[self.name] = ""
        return _SESSION_CACHE.get(self.name) or None

    def _drop_session(self) -> None:
        """Evict the cached session-id so the next call triggers re-init."""
        _SESSION_CACHE.pop(self.name, None)

    # ── Response parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_sse(body: str, request_id: Any) -> dict[str, Any] | None:
        """Parse an SSE response body and return the first envelope matching *request_id*.

        SSE format:
            event: message\\n
            data: {json}\\n
            \\n

        We split on double-newlines, find lines starting with ``data:``, and
        JSON-parse each value.  The first envelope whose ``id`` matches is
        returned.  Subsequent envelopes are MCP notifications (no ``id``);
        they are logged at DEBUG level and discarded.

        Returns ``None`` when no matching envelope is found.
        """
        import json  # noqa: PLC0415 — stdlib, zero overhead

        events = body.split("\n\n")
        for event in events:
            for line in event.splitlines():
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    envelope: dict[str, Any] = json.loads(raw)
                except (ValueError, TypeError):
                    logger.debug(
                        "event=streamable_http_sse_parse_error upstream=%s raw=%r",
                        "sse",
                        raw[:200],
                    )
                    continue
                # Match by id — tools/call responses have an id; notifications don't.
                if envelope.get("id") == request_id:
                    return envelope
                logger.debug(
                    "event=streamable_http_sse_notification upstream=%s method=%s",
                    "sse",
                    envelope.get("method", "<unknown>"),
                )
        return None

    def _parse_response(self, resp: httpx.Response, request_id: Any) -> dict[str, Any]:
        """Decode an httpx response (JSON or SSE) into a JSON-RPC envelope dict."""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            parsed = self._parse_sse(resp.text, request_id)
            if parsed is None:
                # Fallback: no matching id found — return whatever the server sent.
                logger.warning(
                    "event=streamable_http_no_matching_id upstream=%s request_id=%s",
                    self.name,
                    request_id,
                )
                # Try to return any envelope we found (first data: line).
                for event in resp.text.split("\n\n"):
                    for line in event.splitlines():
                        if line.startswith("data:"):
                            raw = line[len("data:"):].strip()
                            if raw:
                                import json  # noqa: PLC0415
                                try:
                                    return dict(json.loads(raw))
                                except (ValueError, TypeError):
                                    pass
                return _sh_error(request_id, "Upstream SSE contained no parseable envelope")
            return parsed
        # application/json or anything else — parse directly.
        try:
            return dict(resp.json())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=streamable_http_json_parse_error upstream=%s error=%s",
                self.name,
                exc,
            )
            return _sh_error(request_id, f"Upstream returned unparseable JSON: {exc}")

    # ── Forward ───────────────────────────────────────────────────────────────

    async def _post(
        self,
        body: dict[str, Any],
        session_id: str | None,
    ) -> httpx.Response:
        """Issue a single POST to the upstream /mcp endpoint."""
        if self._client is None:
            raise RuntimeError("StreamableHttpUpstream not entered — call __aenter__ first")

        mcp_url = f"{self.url}/mcp"
        extra_headers: dict[str, str] = {}
        if session_id:
            extra_headers["Mcp-Session-Id"] = session_id

        return await self._client.post(mcp_url, json=body, headers=extra_headers)

    async def forward(self, jsonrpc_body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        """Forward *jsonrpc_body* to the upstream streamable-HTTP /mcp endpoint.

        Handles session establishment, SSE response parsing, and a single
        retry on session-expired errors.  Returns a parsed JSON-RPC envelope
        dict on success, or a ``JSONResponse`` with a -32603 error on failure.
        """
        if self._client is None:
            logger.error("event=streamable_http_not_initialized upstream=%s", self.name)
            return _sh_error_response(jsonrpc_body.get("id", 1), "Upstream not initialized")

        request_id = jsonrpc_body.get("id", 1)

        # Ensure session is established.
        session_id = await self._get_or_init_session()

        # Attempt the call (with one retry on session expiry).
        for attempt in range(2):
            try:
                resp = await self._post(jsonrpc_body, session_id)
            except httpx.TimeoutException:
                logger.warning("event=streamable_http_timeout upstream=%s", self.name)
                return _sh_error_response(request_id, "Upstream timeout")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "event=streamable_http_network_error upstream=%s attempt=%d error=%s",
                    self.name,
                    attempt,
                    exc,
                )
                return _sh_error_response(request_id, f"Upstream network error: {exc}")

            # Check for session expiry signals.
            if resp.status_code in (401, 403) and attempt == 0:
                logger.info(
                    "event=streamable_http_session_expired upstream=%s status=%d retry=1",
                    self.name,
                    resp.status_code,
                )
                self._drop_session()
                session_id = await self._get_or_init_session()
                continue

            if resp.status_code >= 500:
                logger.warning(
                    "event=streamable_http_5xx upstream=%s status=%d",
                    self.name,
                    resp.status_code,
                )
                return _sh_error_response(request_id, f"Upstream 5xx: {resp.status_code}")

            # Parse the response.
            parsed = self._parse_response(resp, request_id)

            # Check for JSON-RPC session-expired error and retry once.
            if attempt == 0:
                rpc_error = parsed.get("error")
                if isinstance(rpc_error, dict):
                    err_msg = str(rpc_error.get("message", "")).lower()
                    err_data = rpc_error.get("data") or {}
                    err_reason = str(err_data.get("reason", "")).lower() if isinstance(err_data, dict) else ""
                    if "session" in err_msg or "session" in err_reason or "expired" in err_msg:
                        logger.info(
                            "event=streamable_http_rpc_session_error upstream=%s retry=1",
                            self.name,
                        )
                        self._drop_session()
                        session_id = await self._get_or_init_session()
                        continue

            return parsed

        # Both attempts failed — return last error response.
        logger.error(
            "event=streamable_http_all_attempts_failed upstream=%s",
            self.name,
        )
        return _sh_error_response(request_id, "Upstream failed after session refresh retry")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sh_error(request_id: Any, message: str) -> dict[str, Any]:
    """Return a JSON-RPC -32603 error envelope as a plain dict."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32603,
            "message": "Internal error",
            "data": {"reason": message},
        },
    }


def _sh_error_response(request_id: Any, message: str) -> JSONResponse:
    """Return a JSON-RPC -32603 error as a FastAPI JSONResponse."""
    return JSONResponse(_sh_error(request_id, message))
