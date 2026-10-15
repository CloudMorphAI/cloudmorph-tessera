"""Synthetic regression coverage for 2026 HTTP metadata with legacy upstreams."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

from tessera import proxy as proxy_module
from tessera.config import PoliciesMode, TesseraConfig, UpstreamConfig
from tessera.errors import UnauthorizedError
from tessera.mcp_http import (
    AUDIT_ID_HEADER,
    MAX_MODERN_RESPONSE_BYTES,
    MODERN_PROTOCOL_VERSION,
    adapt_modern_http_response,
    modernize_http_response,
)
from tessera.proxy import create_app


def _request(method: str, request_id: int = 7, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "synthetic-test", "version": "1"},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": request_params}


def _headers(method: str, *, name: str | None = None) -> dict[str, str]:
    result = {
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if name is not None:
        result["Mcp-Name"] = name
    return result


def _legacy_upstream(*, invalid_call_meta: bool = False) -> tuple[httpx.MockTransport, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        method = message.get("method")
        calls.append(message)
        if method == "initialize":
            payload = {
                "jsonrpc": "2.0", "id": message["id"],
                "result": {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic-upstream", "version": "1"},
                },
            }
            return httpx.Response(200, json=payload)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": message["id"],
                "result": {"tools": [{
                    "name": "aws_s3_list_buckets", "inputSchema": {"type": "object"},
                }]},
            })
        if method == "tools/call":
            result: dict[str, Any] = {"content": [{"type": "text", "text": "synthetic ok"}]}
            if invalid_call_meta:
                result["_meta"] = "malformed upstream metadata"
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": message["id"],
                "result": result,
            })
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message.get("id"), "result": {}})

    return httpx.MockTransport(handle), calls


def _client(config: TesseraConfig, transport: httpx.MockTransport):
    app = create_app(config)
    client_ctx = TestClient(app, raise_server_exceptions=False)
    return app, client_ctx, transport


def test_server_discover_advertises_only_modern_subset(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp", json=_request("server/discover"), headers=_headers("server/discover")
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert result["capabilities"] == {"tools": {}}
    assert result["resultType"] == "complete"
    assert result["cacheScope"] == "private"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "tessera"
    assert calls == []


def test_modern_header_mismatch_is_rejected_before_upstream(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp", json=_request("tools/list"), headers=_headers("tools/call")
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
    assert calls == []


def test_modern_request_missing_method_header_is_rejected(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    headers = _headers("tools/list")
    headers.pop("Mcp-Method")
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp", json=_request("tools/list"), headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
    assert calls == []


def test_legacy_initialize_path_remains_available(test_config: TesseraConfig) -> None:
    transport, _ = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "legacy-test", "version": "1"},
        },
    }
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp/mock", json=body)

    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


def test_unsupported_modern_version_returns_negotiation_data(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    body = _request("tools/list")
    body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2027-01-01"
    headers = _headers("tools/list")
    headers["MCP-Protocol-Version"] = "2027-01-01"
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp", json=body, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32022
    assert response.json()["error"]["data"]["supported"] == [MODERN_PROTOCOL_VERSION]
    assert calls == []


def test_modern_request_requires_per_request_client_capabilities(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    body = _request("tools/list")
    body["params"]["_meta"].pop("io.modelcontextprotocol/clientCapabilities")
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp", json=body, headers=_headers("tools/list")
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32021
    assert calls == []


def test_present_but_null_protocol_version_is_not_treated_as_legacy(
    test_config: TesseraConfig,
) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    body = _request("tools/list")
    body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = None
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32022
    assert calls == []


def test_authentication_errors_keep_http_status_on_both_routes(
    test_config: TesseraConfig,
) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    with client_ctx as client:
        def reject_auth(_request: Any) -> None:
            raise UnauthorizedError("invalid token")

        app.state.authenticator.authenticate = reject_auth
        for path in ("/mcp", "/mcp/mock"):
            response = client.post(
                path, json=_request("tools/list"), headers=_headers("tools/list")
            )
            assert response.status_code == 401
            assert response.json() == {"error": "invalid token"}
        routing_error = client.post(
            "/mcp/unmatched/path", json=_request("tools/list"),
            headers=_headers("tools/list"),
        )
        assert routing_error.status_code == 404
        assert routing_error.json() == {"detail": "Not Found"}
    assert calls == []


def test_modern_request_uses_streamable_http_upstream_and_preserves_tessera_metadata(
    test_config: TesseraConfig,
) -> None:
    calls: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            # Streamable HTTP cleanup sends an empty-body session DELETE.
            return httpx.Response(202)
        message = json.loads(request.content)
        calls.append(message)
        if message.get("method") == "initialize":
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": message["id"], "result": {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic-streamable", "version": "1"},
                },
            }, headers={"Mcp-Session-Id": "synthetic-session"})
        if message.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": message["id"], "result": {
                "content": [{"type": "text", "text": "synthetic streamable ok"}],
            },
        })

    config = test_config.model_copy(update={
        "upstreams": [UpstreamConfig(
            name="mock", url="http://mock-upstream", kind="mcp_streamable_http",
        )],
    })
    app, client_ctx, _ = _client(config, httpx.MockTransport(handle))
    request = _request("tools/call", params={
        "name": "aws_s3_list_buckets", "arguments": {},
    })
    request["params"]["_meta"].update({
        "tessera_intent": {"verbs": ["read"], "purpose": "verify policy path"},
        "tessera_audit_event_id": "audit-synthetic-1",
    })
    with client_ctx as client:
        upstream = app.state.streamable_http_clients["mock"]
        upstream._client._transport = httpx.MockTransport(handle)
        response = client.post(
            "/mcp/mock", json=request,
            headers=_headers("tools/call", name="aws_s3_list_buckets"),
        )
        denied_request = _request("tools/call", params={
            "name": "aws_s3_delete_bucket", "arguments": {"bucket": "synthetic"},
        })
        denied_response = client.post(
            "/mcp/mock", json=denied_request,
            headers=_headers("tools/call", name="aws_s3_delete_bucket"),
        )

    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["text"] == "synthetic streamable ok"
    tool_call = next(call for call in calls if call.get("method") == "tools/call")
    assert tool_call["params"]["_meta"] == {
        "tessera_intent": {"verbs": ["read"], "purpose": "verify policy path"},
        "tessera_audit_event_id": "audit-synthetic-1",
    }
    assert denied_response.status_code == 200
    denied_result = denied_response.json()["result"]
    assert denied_result["isError"] is True
    assert "POLICY_BLOCK" in denied_result["content"][0]["text"]
    assert len([call for call in calls if call.get("method") == "tools/call"]) == 1


def test_modern_accept_requires_both_concrete_mime_types(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    headers = _headers("tools/list")
    headers["Accept"] = "*/*"
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp", json=_request("tools/list"), headers=headers)

    assert response.status_code == 406
    assert calls == []


def test_modern_tool_call_is_checked_then_translated_to_legacy_upstream(
    test_config: TesseraConfig,
) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    request = _request("tools/call", params={
        "name": "mock__aws_s3_list_buckets", "arguments": {},
    })
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp", json=request,
            headers=_headers("tools/call", name="mock__aws_s3_list_buckets"),
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["content"][0]["text"] == "synthetic ok"
    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "tessera"
    forwarded = [call for call in calls if call.get("method") == "tools/call"]
    assert len(forwarded) == 1
    assert forwarded[0]["params"]["name"] == "aws_s3_list_buckets"
    assert "_meta" not in forwarded[0]["params"]


def test_mcp_name_uses_spec_base64_sentinel_for_non_ascii(test_config: TesseraConfig) -> None:
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    tool_name = "mock__工具"
    encoded_name = base64.b64encode(tool_name.encode("utf-8")).decode("ascii")
    request = _request("tools/call", params={"name": tool_name, "arguments": {}})
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp", json=request,
            headers=_headers("tools/call", name=f"=?base64?{encoded_name}?="),
        )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["resultType"] == "complete"
    assert calls == []


def test_per_upstream_route_accepts_modern_metadata(test_config: TesseraConfig) -> None:
    transport, _ = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp/mock", json=_request("tools/list"), headers=_headers("tools/list")
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"


def test_malformed_upstream_result_meta_becomes_bounded_error(test_config: TesseraConfig) -> None:
    transport, _ = _legacy_upstream(invalid_call_meta=True)
    app, client_ctx, _ = _client(test_config, transport)
    request = _request("tools/call", params={"name": "aws_s3_list_buckets", "arguments": {}})
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post(
            "/mcp/mock", json=request,
            headers=_headers("tools/call", name="aws_s3_list_buckets"),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == -32603


@pytest.mark.parametrize("mode", list(PoliciesMode))
@pytest.mark.parametrize("unified", [False, True])
def test_legacy_metadata_is_preserved_before_upstream_forwarding(
    test_config: TesseraConfig, mode: PoliciesMode, unified: bool,
) -> None:
    test_config.policies.mode = mode
    transport, calls = _legacy_upstream()
    app, client_ctx, _ = _client(test_config, transport)
    metadata = {
        "io.modelcontextprotocol/clientCapabilities": {"opaque": True},
        "io.modelcontextprotocol/clientInfo": {"custom": "legacy opaque value"},
        "io.modelcontextprotocol/logLevel": "debug",
        "example/extension": {"keep": [1, 2]},
    }
    body = {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
        "name": "mock__aws_s3_list_buckets" if unified else "aws_s3_list_buckets",
        "arguments": {}, "_meta": metadata,
    }}
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(transport=transport, base_url="http://mock")
        response = client.post("/mcp" if unified else "/mcp/mock", json=body)
    assert response.status_code == 200
    forwarded = next(call for call in calls if call["method"] == "tools/call")
    assert forwarded["params"]["_meta"] == metadata
    assert "resultType" not in response.json()["result"]


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("error_response", [False, True])
@pytest.mark.parametrize("invalid_meta", [None, [], "private-upstream-value"])
def test_malformed_metadata_keeps_the_emitted_audit_identity(
    test_config: TesseraConfig, monkeypatch: pytest.MonkeyPatch,
    modern: bool, error_response: bool, invalid_meta: Any,
) -> None:
    emitted_ids: list[str] = []
    original_emit = proxy_module._emit

    def record_emit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        event = original_emit(*args, **kwargs)
        emitted_ids.append(event["eventId"])
        return event

    monkeypatch.setattr(proxy_module, "_emit", record_emit)

    def handle(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        if error_response:
            payload = {"error": {"code": -32000, "message": "upstream failure", "data": {
                "_meta": invalid_meta,
            }}}
        else:
            payload = {"result": {"content": [], "_meta": invalid_meta}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], **payload})

    app, client_ctx, _ = _client(test_config, httpx.MockTransport(handle))
    body = _request("tools/call", params={"name": "aws_s3_list_buckets", "arguments": {}})
    if not modern:
        body["params"].pop("_meta")
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://mock",
        )
        response = client.post(
            "/mcp/mock", json=body,
            headers=_headers("tools/call", name="aws_s3_list_buckets") if modern else {},
        )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == -32603
    audit_id = error["data"]["_meta"]["tessera_audit_event_id"]
    assert audit_id in emitted_ids
    assert response.headers[AUDIT_ID_HEADER] == audit_id
    assert "private-upstream-value" not in response.text
    assert len(response.content) < 1024


def test_modern_oversized_response_keeps_audit_identity(test_config: TesseraConfig) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": json.loads(request.content)["id"],
            "result": {"content": [{"type": "text", "text": "x" * MAX_MODERN_RESPONSE_BYTES}]},
        })

    app, client_ctx, _ = _client(test_config, httpx.MockTransport(handle))
    with client_ctx as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://mock",
        )
        response = client.post(
            "/mcp/mock", json=_request("tools/call", params={
                "name": "aws_s3_list_buckets", "arguments": {},
            }), headers=_headers("tools/call", name="aws_s3_list_buckets"),
        )
    assert response.status_code == 502
    assert response.json()["id"] == 7
    assert len(response.content) < 1024
    audit_id = response.json()["error"]["data"]["_meta"]["tessera_audit_event_id"]
    assert response.headers[AUDIT_ID_HEADER] == audit_id


@pytest.mark.asyncio
async def test_response_adapter_stops_and_closes_oversized_iterator() -> None:
    consumed: list[int] = []
    closed: list[bool] = []

    async def chunks():
        try:
            for number in range(3):
                consumed.append(number)
                yield b"x" * (MAX_MODERN_RESPONSE_BYTES // 2 + 1)
        finally:
            closed.append(True)

    response = StreamingResponse(chunks(), headers={AUDIT_ID_HEADER: "audit-iterator"})
    adapted = await adapt_modern_http_response(response, "tools/call", 17)
    assert adapted.status_code == 502
    assert consumed == [0, 1]
    assert closed == [True]
    assert json.loads(adapted.body)["error"]["data"]["_meta"]["tessera_audit_event_id"] == "audit-iterator"


def test_response_metadata_growth_cannot_exceed_output_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    response = JSONResponse({"jsonrpc": "2.0", "id": 7, "result": {"padding": "x" * 800}})
    monkeypatch.setattr("tessera.mcp_http.MAX_MODERN_RESPONSE_BYTES", len(response.body))
    adapted = modernize_http_response(response, "tools/list", rpc_id=7)
    assert adapted.status_code == 502
    assert len(adapted.body) < len(response.body)


@pytest.mark.asyncio
async def test_bounded_adaptation_retains_background_cleanup() -> None:
    completed: list[bool] = []
    response = JSONResponse(
        {"jsonrpc": "2.0", "id": 7, "result": {"padding": "x" * MAX_MODERN_RESPONSE_BYTES}},
        background=BackgroundTask(completed.append, True),
    )
    adapted = await adapt_modern_http_response(response, "tools/list", 7)
    assert adapted.status_code == 502
    assert adapted.background is not None
    await adapted.background()
    assert completed == [True]


def test_scalar_upstream_error_data_retains_value_and_audit_identity() -> None:
    body = {"jsonrpc": "2.0", "id": 7, "error": {
        "code": -32000, "message": "upstream error", "data": ["opaque", 3],
    }}
    proxy_module._inject_audit_id(body, "trusted-audit-id")
    response = proxy_module._audited_upstream_response(body, event_id="trusted-audit-id")
    assert response.status_code == 200
    assert body["error"]["data"] == ["opaque", 3]
    assert response.headers[AUDIT_ID_HEADER] == "trusted-audit-id"


def test_oversized_request_id_cannot_defeat_response_error_limit() -> None:
    response = JSONResponse({"result": {"padding": "x" * MAX_MODERN_RESPONSE_BYTES}})
    adapted = modernize_http_response(response, "tools/list", rpc_id="x" * MAX_MODERN_RESPONSE_BYTES)
    assert adapted.status_code == 502
    assert len(adapted.body) < 1024
    assert json.loads(adapted.body)["id"] is None


@pytest.mark.asyncio
async def test_oversized_string_chunk_is_rejected_before_encoding() -> None:
    class NeverEncode(str):
        def encode(self, *args: Any, **kwargs: Any) -> bytes:
            raise AssertionError("oversized chunk must not be encoded")

    async def chunks():
        yield NeverEncode("x" * (MAX_MODERN_RESPONSE_BYTES + 1))

    adapted = await adapt_modern_http_response(StreamingResponse(chunks()), "tools/call", 7)
    assert adapted.status_code == 502


def test_ambiguous_upstream_envelope_cannot_spoof_audit_identity() -> None:
    body = {"jsonrpc": "2.0", "id": 7,
            "result": {"_meta": {"tessera_audit_event_id": "upstream-spoof"}},
            "error": {"code": -32000, "data": {"_meta": {"tessera_audit_event_id": "other-spoof"}}}}
    proxy_module._inject_audit_id(body, "trusted-audit-id")
    response = proxy_module._audited_upstream_response(body, event_id="trusted-audit-id")
    assert response.status_code == 502
    assert "result" not in body
    assert body["error"]["data"]["_meta"]["tessera_audit_event_id"] == "trusted-audit-id"
    assert response.headers[AUDIT_ID_HEADER] == "trusted-audit-id"
