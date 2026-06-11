"""Integration tests for AWSMcpUpstream using respx mocks.

respx intercepts httpx calls so no real AWS network traffic is made.
The mcp_proxy_for_aws library is assumed to return an httpx.AsyncClient-like
object whose .post() method is mockable via respx.
"""

from __future__ import annotations

import importlib.util
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp_proxy_for_aws") is None,
    reason="mcp_proxy_for_aws not installed",
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ENDPOINT = "https://mcp.us-east-1.aws.example.com"
_REGION = "us-east-1"


def _make_jsonrpc_body(method: str = "tools/list", tool_name: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if tool_name:
        body["params"] = {"name": tool_name, "arguments": {}}
    return body


def _make_aws_upstream() -> Any:
    """Return an AWSMcpUpstream with a mock aws_iam_streamablehttp_client."""
    from tessera.integrations.aws.upstream import AWSMcpUpstream

    return AWSMcpUpstream(
        name="aws-test",
        endpoint=_ENDPOINT,
        aws_region=_REGION,
        timeout_seconds=10,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_happy_path() -> None:
    """tools/list routes through aws_mcp kind and returns parsed response."""
    response_body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

    mock_client = AsyncMock()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = response_body
    mock_response.headers = {}  # AWSMcpUpstream.forward reads response.headers for aws_context enrichment
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("tessera.integrations.aws.upstream.aws_iam_streamablehttp_client", return_value=mock_client):
        upstream = _make_aws_upstream()
        await upstream.__aenter__()
        result = await upstream.forward(_make_jsonrpc_body("tools/list"))
        await upstream.__aexit__(None, None, None)

    assert isinstance(result, dict)
    assert result.get("result", {}).get("tools") == []


@pytest.mark.asyncio
async def test_tools_call_routes_through_aws_kind() -> None:
    """tools/call body is forwarded to the AWS client's post() method."""
    response_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": "ok"}]},
    }

    mock_client = AsyncMock()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = response_body
    mock_response.headers = {}  # AWSMcpUpstream.forward reads response.headers for aws_context enrichment
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    body = _make_jsonrpc_body("tools/call", tool_name="aws_s3_list_buckets")

    with patch("tessera.integrations.aws.upstream.aws_iam_streamablehttp_client", return_value=mock_client):
        upstream = _make_aws_upstream()
        await upstream.__aenter__()
        result = await upstream.forward(body)
        await upstream.__aexit__(None, None, None)

    # Confirm the POST was called with the correct body
    mock_client.post.assert_called_once_with(_ENDPOINT, json=body)
    assert isinstance(result, dict)
    assert "result" in result


@pytest.mark.asyncio
async def test_credentials_missing_failure_mode() -> None:
    """NoCredentialsError returns a -32603 error JSONResponse."""
    from fastapi.responses import JSONResponse

    class _FakeNoCredentialsError(Exception):
        pass

    # Simulate botocore NoCredentialsError by name check in upstream.py
    _FakeNoCredentialsError.__name__ = "NoCredentialsError"

    mock_client = AsyncMock()
    mock_client.post.side_effect = _FakeNoCredentialsError("Unable to locate credentials")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("tessera.integrations.aws.upstream.aws_iam_streamablehttp_client", return_value=mock_client):
        upstream = _make_aws_upstream()
        await upstream.__aenter__()
        result = await upstream.forward(_make_jsonrpc_body())
        await upstream.__aexit__(None, None, None)

    assert isinstance(result, JSONResponse)
    import json

    body = json.loads(result.body)
    assert body["error"]["code"] == -32603
    assert "credentials" in body["error"]["data"]["reason"].lower()


@pytest.mark.asyncio
async def test_audit_event_aws_context_payload_field() -> None:
    """Response headers aws:ViaAWSMCPService are captured in _aws_context."""
    response_body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = response_body
    mock_response.headers = {
        "aws:ViaAWSMCPService": "bedrock-agent-runtime",
        "aws:CalledViaAWSMCP": "true",
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("tessera.integrations.aws.upstream.aws_iam_streamablehttp_client", return_value=mock_client):
        upstream = _make_aws_upstream()
        await upstream.__aenter__()
        result = await upstream.forward(_make_jsonrpc_body("tools/list"))
        await upstream.__aexit__(None, None, None)

    assert isinstance(result, dict)
    assert "_aws_context" in result
    assert result["_aws_context"]["via_aws_mcp_service"] == "bedrock-agent-runtime"
    assert result["_aws_context"]["called_via_aws_mcp"] == "true"


@pytest.mark.asyncio
async def test_timeout_returns_error() -> None:
    """asyncio.TimeoutError produces a -32603 JSONResponse."""
    from fastapi.responses import JSONResponse

    mock_client = AsyncMock()
    mock_client.post.side_effect = TimeoutError()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("tessera.integrations.aws.upstream.aws_iam_streamablehttp_client", return_value=mock_client):
        upstream = _make_aws_upstream()
        await upstream.__aenter__()
        result = await upstream.forward(_make_jsonrpc_body())
        await upstream.__aexit__(None, None, None)

    assert isinstance(result, JSONResponse)
    import json

    body = json.loads(result.body)
    assert body["error"]["code"] == -32603
    assert "timeout" in body["error"]["data"]["reason"].lower()


@pytest.mark.asyncio
async def test_unknown_upstream_name_returns_error() -> None:
    """Requesting a non-existent upstream from state returns -32001."""
    from fastapi.responses import JSONResponse

    from tessera.proxy import _forward_upstream

    # Minimal state mock with no aws_clients or http_clients for "nonexistent"
    state = MagicMock()
    state.http_clients = {}
    state.aws_clients = {}

    # Config with one aws_mcp upstream, but we request "nonexistent"
    from tessera.config import TesseraConfig, UpstreamConfig

    cfg = TesseraConfig(
        upstreams=[
            UpstreamConfig(name="aws-real", url=_ENDPOINT, kind="aws_mcp", aws_region="us-east-1")
        ]
    )
    state.config = cfg

    result = await _forward_upstream(state, "nonexistent", _make_jsonrpc_body(), 1)

    assert isinstance(result, JSONResponse)
    import json

    body = json.loads(result.body)
    assert body["error"]["code"] == -32001
