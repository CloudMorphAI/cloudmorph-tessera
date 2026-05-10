"""Mock MCP server for proxy integration tests."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

mock_app = FastAPI()

# Configurable per-test response registry
_responses: dict[str, dict] = {}
_delay_seconds: float = 0.0
_force_5xx: bool = False


def configure_tool_response(tool_name: str, response: dict) -> None:
    """Register a canned response for a specific tool call."""
    _responses[tool_name] = response


def configure_delay(seconds: float) -> None:
    """Simulate an upstream delay (use to test timeout handling)."""
    global _delay_seconds
    _delay_seconds = seconds


def configure_5xx(enabled: bool) -> None:
    """Force the mock to return a 500 error."""
    global _force_5xx
    _force_5xx = enabled


def reset_responses() -> None:
    global _delay_seconds, _force_5xx
    _responses.clear()
    _delay_seconds = 0.0
    _force_5xx = False


@mock_app.post("/")
async def handle_mcp_call(request: Request) -> JSONResponse:
    if _force_5xx:
        return JSONResponse({"error": "internal server error"}, status_code=500)

    if _delay_seconds > 0:
        await asyncio.sleep(_delay_seconds)

    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)

    if method == "tools/call":
        tool_name = body.get("params", {}).get("name", "unknown")
        response_body = _responses.get(
            tool_name,
            {"result": {"content": [{"type": "text", "text": "ok"}]}},
        )
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, **response_body})

    if method == "tools/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": []},
            }
        )

    if method in ("initialize", "ping"):
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    if method.startswith("notifications/"):
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})
