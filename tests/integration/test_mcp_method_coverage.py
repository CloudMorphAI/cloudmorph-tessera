"""Integration tests: MCP method dispatch coverage (MCP-AUDIT-2026-05-11).

Verifies:
1. Every method in _PASS_THROUGH_METHODS is forwarded (no -32601).
2. tools/call is NOT in pass-through — it reaches policy evaluation.
3. Truly unknown methods still return -32601.
4. notifications/* prefix is passed through regardless of suffix.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tessera.config import TesseraConfig
from tessera.proxy import _PASS_THROUGH_METHODS, create_app

# ── Shared fixtures / helpers ─────────────────────────────────────────────────

_HEADERS_ALICE = {"Authorization": "Bearer tk_test_alice_xxxxxxxxxxxxxxxxxx"}

_GENERIC_UPSTREAM_OK = {"jsonrpc": "2.0", "id": 1, "result": {}}


def _make_mock_transport(response_json: dict | None = None, status_code: int = 200) -> httpx.MockTransport:
    resp_data = response_json if response_json is not None else _GENERIC_UPSTREAM_OK

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"Content-Type": "application/json"},
            content=json.dumps(resp_data).encode(),
        )

    return httpx.MockTransport(_handler)


def _proxy_client(config: TesseraConfig, transport: httpx.MockTransport):
    """Context manager: TestClient with mock upstream injected."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            if transport is not None:
                app.state.http_clients["mock"] = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://mock-upstream",
                    timeout=5.0,
                )
            yield client, app

    return _ctx()


def _jsonrpc_body(method: str, req_id: int = 1, params: dict | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


# ── Test 1: All _PASS_THROUGH_METHODS are forwarded, not -32601 ───────────────


@pytest.mark.parametrize("method", sorted(_PASS_THROUGH_METHODS))
def test_pass_through_method_is_forwarded(method: str, test_config: TesseraConfig) -> None:
    """Each method in _PASS_THROUGH_METHODS must NOT return -32601 (Method not found)."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_jsonrpc_body(method),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200, f"method={method!r}: expected HTTP 200"
    body = resp.json()
    err = body.get("error")
    assert err is None or err.get("code") != -32601, (
        f"method={method!r} returned -32601 (Method not found) — it should be forwarded"
    )


# ── Test 2: tools/call reaches policy evaluation (not pass-through) ───────────


def test_tools_call_is_policy_evaluated(test_config: TesseraConfig) -> None:
    """tools/call must NOT be in _PASS_THROUGH_METHODS — it undergoes policy evaluation."""
    assert "tools/call" not in _PASS_THROUGH_METHODS, (
        "tools/call must not be in _PASS_THROUGH_METHODS — it needs policy evaluation"
    )
    # Confirm it actually reaches the policy engine (blocked by default_action=block)
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "some_unknown_tool", "arguments": {}},
            },
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    # default_action=block in test_config → unknown tool must be blocked.
    # Blocks surface as MCP tool-errors (result.isError=true), not JSON-RPC
    # -32603 — agents treat -32603 as transient and retry; isError reads as
    # final. See the enforcement-path comment in proxy.py.
    result = body.get("result")
    assert result is not None, "tools/call with unknown tool should be policy-blocked"
    assert result.get("isError") is True, "policy block must set result.isError"
    assert "POLICY_BLOCK" in result["content"][0]["text"]


# ── Test 3: Unknown methods still return -32601 ───────────────────────────────


@pytest.mark.parametrize(
    "method",
    [
        "weird/custom",
        "vendor/proprietary",
        "tools/execute",   # not a real MCP method
        "resources/create",  # not a real MCP method
    ],
)
def test_unknown_method_returns_32601(method: str, test_config: TesseraConfig) -> None:
    """Methods not in _PASS_THROUGH_METHODS and not tools/call must return -32601."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_jsonrpc_body(method),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200, f"method={method!r}: expected HTTP 200"
    body = resp.json()
    assert body.get("error", {}).get("code") == -32601, (
        f"method={method!r} should return -32601 but got: {body}"
    )


# ── Test 4: notifications/* prefix passes through regardless of suffix ─────────


@pytest.mark.parametrize(
    "method",
    [
        "notifications/initialized",
        "notifications/cancelled",
        "notifications/progress",
        "notifications/tools/list_changed",
        "notifications/resources/list_changed",
        "notifications/prompts/list_changed",
    ],
)
def test_notifications_prefix_passes_through(method: str, test_config: TesseraConfig) -> None:
    """Any notifications/* method must be forwarded (handled by prefix check in proxy)."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_jsonrpc_body(method),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200, f"method={method!r}: expected HTTP 200"
    body = resp.json()
    err = body.get("error")
    assert err is None or err.get("code") != -32601, (
        f"notifications method {method!r} should be forwarded, got -32601"
    )


# ── Test 5: Policy block error contract ───────────────────────────────────────


def test_policy_block_uses_tool_error_with_reason(test_config: TesseraConfig) -> None:
    """Policy blocks surface as MCP tool-errors (result.isError=true) with a reason line."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "aws_s3_delete_bucket", "arguments": {"bucket": "x"}},
            },
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    result = body["result"]
    assert result["isError"] is True
    block_text = result["content"][0]["text"]
    assert "POLICY_BLOCK" in block_text
    assert "reason:" in block_text
