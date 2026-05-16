"""Integration tests: full proxy round-trip using httpx.ASGITransport (no real HTTP)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

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
from tessera.proxy import create_app

# ── Helpers ───────────────────────────────────────────────────────────────────


def _tools_call_body(
    tool_name: str,
    arguments: dict | None = None,
    meta: dict | None = None,
    req_id: int = 1,
) -> dict[str, Any]:
    params: dict[str, Any] = {"name": tool_name, "arguments": arguments or {}}
    if meta is not None:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params}


_MOCK_UPSTREAM_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"content": [{"type": "text", "text": "upstream ok"}]},
}

_TOKEN_ALICE = "tk_test_alice_xxxxxxxxxxxxxxxxxx"
_TOKEN_BOB = "tk_test_bob_yyyyyyyyyyyyyyyyyy"
_HEADERS_ALICE = {"Authorization": f"Bearer {_TOKEN_ALICE}"}
_HEADERS_BOB = {"Authorization": f"Bearer {_TOKEN_BOB}"}


def _make_mock_transport(response_json: dict | None = None, status_code: int = 200) -> httpx.MockTransport:
    """Create an httpx.MockTransport that returns the given JSON response."""
    import json as _json

    resp_data = response_json if response_json is not None else _MOCK_UPSTREAM_RESPONSE

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"Content-Type": "application/json"},
            content=_json.dumps(resp_data).encode(),
        )

    return httpx.MockTransport(_handler)


def _make_timeout_transport() -> httpx.MockTransport:
    """Create a MockTransport that raises TimeoutException."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timed out", request=request)

    return httpx.MockTransport(_handler)


def _build_client_with_transport(config: TesseraConfig, transport: httpx.MockTransport) -> TestClient:
    """Build a TestClient where the 'mock' upstream uses the given httpx transport."""
    app = create_app(config)

    # Patch the httpx client after startup by overriding it in app.state
    # We use lifespan manually via TestClient context
    with TestClient(app) as client:
        # Replace the mock upstream client with our controlled transport
        app.state.http_clients["mock"] = httpx.AsyncClient(
            transport=transport,
            base_url="http://mock-upstream",
            timeout=5.0,
        )
        yield client, app


# Use a context manager approach for the client


@contextlib.contextmanager
def _proxy_client(
    config: TesseraConfig,
    transport: httpx.MockTransport | None = None,
):
    """Yield a TestClient with the app's mock upstream patched."""
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        if transport is not None:
            app.state.http_clients["mock"] = httpx.AsyncClient(
                transport=transport,
                base_url="http://mock-upstream",
                timeout=5.0,
            )
        yield client, app


# ── Tests: enforcement mode ──────────────────────────────────────────────────


def test_allow_tool_call(test_config: TesseraConfig) -> None:
    """Enforcement mode: tool matched by allow policy → upstream response returned."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    # No error key — upstream result returned
    assert "error" not in body or body.get("error") is None
    assert "result" in body


def test_block_tool_call(test_config: TesseraConfig) -> None:
    """Enforcement mode: tool matched by block policy → JSON-RPC error -32603."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_delete_bucket", {"bucket": "test"}),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32603
    assert "reason" in body["error"]["data"]


def test_lockdown_blocks_all(test_config_lockdown: TesseraConfig) -> None:
    """runtime.lockdown=True → -32603 reason=lockdown_active for any tool."""
    with _proxy_client(test_config_lockdown, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["reason"] == "lockdown_active"


def test_intent_required_missing(test_config_intent_required: TesseraConfig) -> None:
    """intent.required=True; no _meta.tessera_intent → -32603 reason=intent_required."""
    with _proxy_client(test_config_intent_required, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["reason"] == "intent_required"


def test_upstream_timeout(test_config: TesseraConfig) -> None:
    """Upstream timeout → JSON-RPC error -32000."""
    with _proxy_client(test_config, _make_timeout_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32000


# ── Tests: log_only mode ──────────────────────────────────────────────────────


def test_log_only_forwards_with_headers(test_config_log_only: TesseraConfig) -> None:
    """mode=log_only; would-block decision still returns upstream response + X-Tessera-Mode header."""
    with _proxy_client(test_config_log_only, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_delete_bucket", {"bucket": "test"}),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    # Upstream result present (not blocked)
    body = resp.json()
    assert "result" in body
    # log_only headers present
    assert resp.headers.get("X-Tessera-Mode") == "log_only"
    assert resp.headers.get("X-Tessera-Decision") == "would_block"
    # Policy ID and reason injected for would_block
    assert resp.headers.get("X-Tessera-Policy-Id") == "test-block-deletes"


def test_log_only_would_allow(test_config_log_only: TesseraConfig) -> None:
    """mode=log_only; tool matched by allow policy → would_allow header."""
    with _proxy_client(test_config_log_only, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert resp.headers.get("X-Tessera-Mode") == "log_only"
    assert resp.headers.get("X-Tessera-Decision") == "would_allow"


def test_log_only_no_match(test_config_log_only: TesseraConfig) -> None:
    """mode=log_only; no policy match (unknown tool with default block) → no_match header.

    Note: default_action=block with no matching policy means Decision.policy_id=None → no_match.
    """
    with _proxy_client(test_config_log_only, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("completely_unknown_tool_xyz"),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert resp.headers.get("X-Tessera-Mode") == "log_only"
    assert resp.headers.get("X-Tessera-Decision") == "no_match"


# ── Tests: observation mode ──────────────────────────────────────────────────


def test_observation_skips_engine(test_config_observation: TesseraConfig) -> None:
    """mode=observation; engine never blocks; upstream response returned unconditionally."""
    with _proxy_client(test_config_observation, _make_mock_transport()) as (client, app):
        # Even a tool that would normally be blocked is forwarded
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_delete_bucket", {"bucket": "test"}),
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    # No error — upstream response returned
    assert "result" in body
    # No tessera mode headers (observation doesn't inject them)
    assert "X-Tessera-Mode" not in resp.headers


# ── Tests: multi-token scope ──────────────────────────────────────────────────


def test_multi_token_scope_in_audit(tmp_path: Path) -> None:
    """Two tokens with different scopes: each call's audit event has correct tenantId."""
    from tessera.audit.sinks.sqlite import SqliteSink

    db_path = tmp_path / "multi_token_audit.db"
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "allow-all.yaml").write_text(
        'id: allow-all\nname: Allow all\nmatch:\n  upstream: "*"\n  tool: "*"\naction: allow\n',
        encoding="utf-8",
    )

    config = TesseraConfig(
        audit=AuditConfig(path=str(db_path), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="allow",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test",
    )

    # Set environment for two tokens
    original_env = os.environ.get("TESSERA_BEARER_TOKENS")
    os.environ["TESSERA_BEARER_TOKENS"] = (
        "alice:tk_test_alice_xxxxxxxxxxxxxxxxxx,bob:tk_test_bob_yyyyyyyyyyyyyyyyyy"
    )
    try:
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.http_clients["mock"] = httpx.AsyncClient(
                transport=_make_mock_transport(),
                base_url="http://mock-upstream",
                timeout=5.0,
            )

            # Alice calls
            client.post(
                "/mcp/mock",
                json=_tools_call_body("some_tool"),
                headers={"Authorization": f"Bearer {_TOKEN_ALICE}"},
            )
            # Bob calls
            client.post(
                "/mcp/mock",
                json=_tools_call_body("some_tool"),
                headers={"Authorization": f"Bearer {_TOKEN_BOB}"},
            )

        # Check audit DB — should have events for both scopes
        sink = SqliteSink(path=str(db_path))
        alice_events = list(sink.iter_events("alice"))
        bob_events = list(sink.iter_events("bob"))
        # At least the startup event + one decision event per scope
        assert len(alice_events) >= 1
        assert len(bob_events) >= 1
        # Verify tenant isolation
        for evt in alice_events:
            assert evt["tenantId"] == "alice"
        for evt in bob_events:
            assert evt["tenantId"] == "bob"
    finally:
        if original_env is None:
            os.environ.pop("TESSERA_BEARER_TOKENS", None)
        else:
            os.environ["TESSERA_BEARER_TOKENS"] = original_env


# ── Tests: pass-through methods ───────────────────────────────────────────────


def test_pass_through_methods(test_config: TesseraConfig) -> None:
    """All methods in _PASS_THROUGH_METHODS are forwarded without policy evaluation.

    Covers the expanded set added in MCP-AUDIT-2026-05-11:
    initialize, ping, tools/list, prompts/list, prompts/get,
    resources/list, resources/read, resources/subscribe,
    resources/unsubscribe, roots/list, logging/setLevel,
    completion/complete, sampling/createMessage.
    """
    _all_pass_through = [
        # Lifecycle
        "initialize",
        "ping",
        # Discovery
        "tools/list",
        "prompts/list",
        "resources/list",
        "roots/list",
        # Config
        "logging/setLevel",
        # Action-category (pass-through per MCP-AUDIT-2026-05-11)
        "resources/unsubscribe",
        "prompts/get",
        "resources/read",
        "resources/subscribe",
        "completion/complete",
        "sampling/createMessage",
    ]
    with _proxy_client(
        test_config, _make_mock_transport({"jsonrpc": "2.0", "id": 1, "result": {}})
    ) as (client, app):
        for method in _all_pass_through:
            resp = client.post(
                "/mcp/mock",
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
                headers=_HEADERS_ALICE,
            )
            assert resp.status_code == 200, f"method={method!r} returned non-200"
            body = resp.json()
            assert "error" not in body or body.get("error") is None, (
                f"method={method!r} returned error: {body.get('error')}"
            )


def test_unknown_method_rejected(test_config: TesseraConfig) -> None:
    """An unknown method (not in allowed list) → -32601."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json={"jsonrpc": "2.0", "id": 1, "method": "weird/custom", "params": {}},
            headers=_HEADERS_ALICE,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32601


# ── Tests: audit event id injection ──────────────────────────────────────────


def test_audit_event_id_injected_in_response(test_config: TesseraConfig) -> None:
    """tessera_audit_event_id is present in the upstream response body after proxying."""
    with _proxy_client(test_config, _make_mock_transport()) as (client, app):
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body("aws_s3_list_buckets"),
            headers=_HEADERS_ALICE,
        )
    body = resp.json()
    result = body.get("result", {})
    assert "_meta" in result
    assert "tessera_audit_event_id" in result["_meta"]
    event_id = result["_meta"]["tessera_audit_event_id"]
    assert event_id.startswith("evt_")


# ── Tests: healthz ────────────────────────────────────────────────────────────


def test_healthz_includes_policy_state(test_config: TesseraConfig) -> None:
    """/healthz returns policy_state with loaded count."""
    with _proxy_client(test_config) as (client, app):
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "policy_state" in body
    assert body["policy_state"]["loaded"] >= 1
