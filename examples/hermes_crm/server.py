"""Run a loopback-only synthetic CRM behind the real Tessera MCP proxy.

The server exposes Tessera at ``/mcp/crm`` and serves Tessera's CRM upstream at
``/`` on the same listener. ``/__demo/status`` and ``/__demo/reset`` are local
test controls and are not included in the MCP tool catalog.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sqlite3
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tessera.config import (
    AuditConfig,
    IntentConfig,
    MetricsConfig,
    PoliciesConfig,
    PoliciesMode,
    RuntimeConfig,
    TesseraConfig,
    UpstreamConfig,
)
from tessera.proxy import create_app as create_tessera_app

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SEED_ID = "crm-demo-001"
_SEED_RECORD = {"id": _SEED_ID, "call_notes": "Initial note", "billing_status": "unpaid"}
_POLICY_SOURCE = Path(__file__).with_name("policy.yaml")


class SyntheticCRM:
    """In-memory CRM with a call spy; it applies updates without policy logic."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self.record = dict(_SEED_RECORD)
            self.tool_call_count = 0
            self.write_count = 0
            self.calls: list[dict[str, Any]] = []
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "record": dict(self.record),
                "tool_call_count": self.tool_call_count,
                "write_count": self.write_count,
                "calls": list(self.calls),
            }

    def handle(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            return _rpc_error(None, -32600, "Invalid Request")

        request_id = body.get("id")
        method = body.get("method")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "synthetic-hermes-crm", "version": "1.0.0"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": _tool_catalog()}}
        if method != "tools/call":
            return _rpc_error(request_id, -32601, "Method not found")

        params = body.get("params")
        if not isinstance(params, dict):
            params = {}
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        with self._lock:
            self.tool_call_count += 1
            call: dict[str, Any] = {
                "name": tool_name,
                "arguments": arguments,
                "forwarded": True,
            }
            self.calls.append(call)

            if tool_name == "crm.get_contact":
                record_id = arguments.get("record_id")
                if record_id != self.record["id"]:
                    result = _tool_error(f"Unknown contact: {record_id}")
                else:
                    record = dict(self.record)
                    result = {
                        "content": [{"type": "text", "text": json.dumps(record, sort_keys=True)}],
                        "structuredContent": {"record": record},
                    }
            elif tool_name == "crm.update_contact":
                record_id = arguments.get("record_id")
                fields = arguments.get("fields")
                if record_id != self.record["id"]:
                    result = _tool_error(f"Unknown contact: {record_id}")
                elif not isinstance(fields, dict) or not fields:
                    result = _tool_error("fields must be a non-empty object")
                else:
                    # Deliberately generic: the backend does not special-case paid.
                    self.record.update(fields)
                    self.write_count += 1
                    updated = dict(self.record)
                    result = {
                        "content": [{"type": "text", "text": "Contact updated"}],
                        "structuredContent": {"record": updated},
                    }
            else:
                result = _tool_error(f"Unknown tool: {tool_name}")

            call["outcome"] = "error" if result.get("isError") else "success"
            return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": "crm.update_contact",
            "description": "Update fields on a synthetic CRM contact.",
            "inputSchema": {
                "type": "object",
                "required": ["record_id", "fields"],
                "properties": {
                    "record_id": {"type": "string"},
                    "fields": {
                        "type": "object",
                        "properties": {
                            "call_notes": {"type": "string"},
                            "billing_status": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "crm.get_contact",
            "description": "Read a synthetic CRM contact.",
            "inputSchema": {
                "type": "object",
                "required": ["record_id"],
                "properties": {"record_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    ]


def _write_policies(policy_dir: Path) -> None:
    """Write the three one-policy YAML files expected by the current loader."""
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "block-paid.yaml").write_text(_POLICY_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    (policy_dir / "allow-call-notes.yaml").write_text(
        """\
id: hermes-crm-allow-call-notes-update
name: Allow call notes updates
match:
  upstream: crm
  tool: crm.update_contact
when:
  - condition: arg_path_matches_regex
    arg_path: fields.call_notes
    pattern: '(?s).*'
action: allow
reason: call_notes_update_allowed
priority: 10
""",
        encoding="utf-8",
    )
    (policy_dir / "allow-contact-read.yaml").write_text(
        """\
id: hermes-crm-allow-contact-read
name: Allow contact reads
match:
  upstream: crm
  tool: crm.get_contact
action: allow
reason: contact_read_allowed
priority: 10
""",
        encoding="utf-8",
    )


def _audit_decisions(audit_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not audit_path.exists():
        return [], None
    uri = f"{audit_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1) as connection:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                "SELECT payload_json FROM audit_events WHERE event_type = ? ORDER BY seq",
                ("decision",),
            ).fetchall()
        return [json.loads(row[0]) for row in rows], None
    except sqlite3.Error as exc:
        return [], f"{type(exc).__name__}: {exc}"


def create_demo_app(port: int = 8765) -> FastAPI:
    """Create the loopback demo application with a real Tessera proxy subapp."""
    scratch = tempfile.TemporaryDirectory(
        prefix="cm093-hermes-crm-",
        dir=str(_REPOSITORY_ROOT),
    )
    scratch_path = Path(scratch.name)
    policy_dir = scratch_path / "policies"
    _write_policies(policy_dir)
    audit_path = scratch_path / "audit.db"
    config = TesseraConfig(
        audit=AuditConfig(path=str(audit_path), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[UpstreamConfig(name="crm", url=f"http://127.0.0.1:{port}", timeout_seconds=5)],
        deployment_id="cm093-hermes-synthetic-demo",
    )
    tessera_app = create_tessera_app(config)
    crm = SyntheticCRM()

    @asynccontextmanager
    async def lifespan(outer_app: FastAPI):
        try:
            async with tessera_app.router.lifespan_context(tessera_app):
                yield
        finally:
            scratch.cleanup()

    app = FastAPI(title="CM-093 local synthetic Hermes CRM", lifespan=lifespan)
    app.state.crm = crm
    app.state.tessera_app = tessera_app
    app.state.audit_path = audit_path
    app.state.mcp_wire_headers = []

    @app.middleware("http")
    async def record_protocol_headers(request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            # Record transport/version only; never auth, cookies or session tokens.
            app.state.mcp_wire_headers.append({
                "path": request.url.path,
                "http_method": request.method,
                "protocol_version": request.headers.get("mcp-protocol-version"),
                "accept": request.headers.get("accept"),
            })
        return await call_next(request)

    @app.post("/")
    async def crm_jsonrpc(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"))
        return JSONResponse(crm.handle(body))

    def require_loopback(request: Request) -> bool:
        client = request.client
        if client is None:
            return False
        try:
            return ipaddress.ip_address(client.host).is_loopback
        except ValueError:
            return False

    @app.get("/__demo/status")
    async def demo_status(request: Request) -> JSONResponse:
        if not require_loopback(request):
            return JSONResponse({"detail": "loopback only"}, status_code=403)
        decisions, audit_error = _audit_decisions(audit_path)
        result = crm.snapshot()
        result["audit_decisions"] = decisions
        result["audit_error"] = audit_error
        result["mcp_wire_headers"] = list(app.state.mcp_wire_headers)
        return JSONResponse(result)

    @app.post("/__demo/reset")
    async def demo_reset(request: Request) -> JSONResponse:
        if not require_loopback(request):
            return JSONResponse({"detail": "loopback only"}, status_code=403)
        result = crm.reset()
        result["audit_history_cleared"] = False
        result["note"] = "Audit history is retained until the server restarts."
        return JSONResponse(result)

    # Mount after local routes: the Tessera app handles /mcp, while CRM's upstream
    # HTTP client posts to the root path on this same listener.
    app.mount("/", tessera_app)
    app.state.temp_directory = scratch
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind address (only 127.0.0.1 is allowed)")
    parser.add_argument("--port", type=int, default=8765, help="Local Tessera and CRM port")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("--host must be 127.0.0.1")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    app = create_demo_app(port=args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        app.state.temp_directory.cleanup()


if __name__ == "__main__":
    main()
