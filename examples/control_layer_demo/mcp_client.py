"""Ordinary HTTP MCP client: discovery and tool calls only through Tessera."""
from __future__ import annotations

import uuid
from typing import Any

import httpx


class MCPClient:
    def __init__(self, url: str, token: str, run_id: str, *, timeout_s: int = 30) -> None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 1 <= timeout_s <= 60:
            raise ValueError("MCP client timeout must be between 1 and 60 seconds")
        self.url, self.run_id = url, run_id
        self.timeout_s = timeout_s
        self.http = httpx.Client(timeout=timeout_s, follow_redirects=False, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25",
        })

    def close(self) -> None:
        self.http.close()

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
        rid = uuid.uuid4().hex
        response = self.http.post(self.url, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        response.raise_for_status()
        if len(response.content) > 1_048_576:
            raise ValueError("MCP response exceeds session bound")
        body = response.json()
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != rid:
            raise ValueError("MCP response correlation failed")
        if ("result" in body) == ("error" in body):
            raise ValueError("MCP response must contain result or error")
        target = body.get("result", body.get("error", {}))
        if "error" in body:
            target = target.get("data", {})
        if not isinstance(target, dict):
            target = {}
        audit_id = response.headers.get("X-Tessera-Audit-Event-Id") or target.get("_meta", {}).get("tessera_audit_event_id")
        return body, audit_id

    def discover(self) -> list[dict[str, Any]]:
        body, _ = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "control-layer-agent", "version": "1"}})
        if "error" in body or body["result"].get("protocolVersion") != "2025-11-25":
            raise ValueError("MCP initialization failed")
        response = self.http.post(self.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        if response.status_code != 202 or response.content:
            raise ValueError("MCP initialized notification was not accepted")
        tools, names, cursors = [], set(), set()
        cursor = None
        for _ in range(32):
            body, _ = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            if "error" in body or not isinstance(body["result"].get("tools"), list):
                raise ValueError("MCP catalog discovery failed")
            for tool in body["result"]["tools"]:
                name = tool.get("name") if isinstance(tool, dict) else None
                if not isinstance(name, str) or not name or name in names or not isinstance(tool.get("inputSchema"), dict):
                    raise ValueError("MCP catalog has invalid or duplicate tools")
                names.add(name)
                tools.append(tool)
                if len(tools) > 128:
                    raise ValueError("MCP catalog exceeds session bound")
            cursor = body["result"].get("nextCursor")
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("MCP catalog continuation is invalid")
            cursors.add(cursor)
        raise ValueError("MCP catalog exceeds pagination bound")

    def call(self, name: str, arguments: dict[str, Any], tool_use_id: str) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        body, audit_id = self.rpc("tools/call", {"name": name, "arguments": arguments, "_meta": {
            "run_id": self.run_id, "tool_use_id": tool_use_id, "request_id": request_id,
            "conversation_id": self.run_id,
        }})
        return {"request_id": request_id, "tool_use_id": tool_use_id, "tool_name": name,
                "arguments": arguments, "response": body, "audit_event_id": audit_id}
