"""Authenticated localhost HTTP MCP service for the control-layer demo.

Start separately from the agent and Tessera proxy. Supply CM_SANDBOX_TOKEN only
in the process environment and a private SQLite path outside the checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from .sandbox_store import VERSION, SandboxStore, tool_catalog
except ImportError:
    from sandbox_store import VERSION, SandboxStore, tool_catalog

MAX_BODY_BYTES = 65536
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def make_server(
    *, run_id: str, db_path: str, token: str | None = None, port: int = 8766
) -> ThreadingHTTPServer:
    secret = token if token is not None else os.environ.get("CM_SANDBOX_TOKEN", "")
    if (
        not isinstance(secret, str)
        or len(secret) < 32
        or len(secret) > 512
        or not secret.isascii()
        or any(char.isspace() for char in secret)
    ):
        raise ValueError(
            "CM_SANDBOX_TOKEN must be a session-only ASCII secret of 32 through 512 non-whitespace characters"
        )
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Invalid localhost port")
    store = SandboxStore(db_path, run_id)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SandboxMCP"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            # No request headers, body, URL or secret enters HTTP access logs.
            return

        def _reply(self, status: int, value: Any = None) -> None:
            body = (
                b""
                if value is None
                else json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            )
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

        def _error(self, status: int, code: int, message: str, rpc_id: Any = None) -> None:
            self._reply(status, {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})

        def _authenticated(self) -> bool:
            headers = self.headers.get_all("Authorization", [])
            expected = "Bearer " + secret
            if (
                len(headers) != 1
                or not headers[0].isascii()
                or not secrets.compare_digest(headers[0], expected)
            ):
                self._error(401, -32001, "Unauthorized")
                return False
            # This service has no browser access or CORS grant, even on localhost.
            if self.headers.get_all("Origin", []):
                self._error(403, -32001, "Browser origins are not allowed")
                return False
            return True

        def do_GET(self) -> None:
            if not self._authenticated():
                return
            if self.path != "/evidence":
                self._error(404, -32601, "Not found")
                return
            self._reply(200, store.evidence())

        def do_POST(self) -> None:
            self.connection.settimeout(10)
            if not self._authenticated():
                return
            if self.path != "/mcp":
                self._error(404, -32601, "Not found")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (
                self.headers.get_all("Transfer-Encoding", [])
                or len(lengths) != 1
                or len(lengths[0]) > 6
                or not lengths[0].isdigit()
            ):
                self._error(400, -32600, "A single Content-Length is required")
                return
            length = int(lengths[0])
            if length < 1 or length > MAX_BODY_BYTES:
                self._error(413, -32600, "Request body outside allowed bounds")
                return
            content_types = self.headers.get_all("Content-Type", [])
            if (
                len(content_types) != 1
                or content_types[0].partition(";")[0].strip().lower() != "application/json"
            ):
                self._error(415, -32600, "Content-Type must be application/json")
                return
            versions = self.headers.get_all("MCP-Protocol-Version", [])
            if len(versions) > 1 or (versions and versions[0] not in PROTOCOL_VERSIONS):
                self._error(400, -32600, "Unsupported MCP protocol version")
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request")
                body = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_json_object, parse_constant=_invalid_constant
                )
            except (ValueError, UnicodeError, OSError, RecursionError):
                self._error(400, -32700, "Invalid JSON body")
                return
            if (
                not isinstance(body, dict)
                or body.get("jsonrpc") != "2.0"
                or not isinstance(body.get("method"), str)
                or set(body) - {"jsonrpc", "id", "method", "params"}
            ):
                self._error(400, -32600, "Invalid JSON-RPC request")
                return
            rpc_id = body.get("id")
            if "id" in body and (
                isinstance(rpc_id, bool) or not isinstance(rpc_id, (str, int)) or len(str(rpc_id)) > 128
            ):
                self._error(400, -32600, "Invalid JSON-RPC request identifier")
                return
            method = body["method"]
            params = body.get("params", {})
            if not isinstance(params, dict):
                self._error(400, -32602, "Params must be an object", rpc_id)
                return
            if "id" not in body:
                if method == "notifications/initialized":
                    self._reply(202)
                else:
                    self._error(400, -32600, "Unsupported notification")
                return
            if method == "initialize":
                version = params.get("protocolVersion")
                if version not in PROTOCOL_VERSIONS:
                    self._error(400, -32602, "Unsupported protocol version", rpc_id)
                    return
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "Acme fictional persistent sandbox", "version": VERSION},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                if set(params) - {"_meta"}:
                    self._error(400, -32602, "No pagination parameters are supported", rpc_id)
                    return
                result = {"tools": tool_catalog()}
            elif method == "tools/call":
                # Once authenticated JSON-RPC is a tools/call, even malformed tool
                # arguments must appear in the independent service contact ledger.
                if set(params) - {"name", "arguments", "_meta"}:
                    envelope = store.invoke(params.get("name"), None, params.get("_meta"), rpc_id)
                else:
                    envelope = store.invoke(
                        params.get("name"), params.get("arguments", {}), params.get("_meta"), rpc_id
                    )
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(envelope, ensure_ascii=False, allow_nan=False)}
                    ],
                    "structuredContent": envelope,
                    "isError": envelope["status"] != "ok",
                }
            else:
                self._error(200, -32601, "Method not found", rpc_id)
                return
            self._reply(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.store = store
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Fictional persistent sandbox HTTP MCP service")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db", required=True, help="Private SQLite path outside source checkout")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = make_server(run_id=args.run_id, db_path=args.db, port=args.port)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
