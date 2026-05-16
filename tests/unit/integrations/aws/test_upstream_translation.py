"""Unit tests for AWSMcpUpstream translation layer (SA-3B, v0.3.0 Batch 3).

Coverage:
  1. Routing config parsing — UpstreamConfig with aws_mcp_server + aws_mcp_routing parses cleanly.
  2. Default routing — aws_mcp_server=None, calls pass through unchanged.
  3. aws-api-mcp-server translation — _translate_call_aws_op returns call_aws shape.
  4. Unknown op + specific-first — fallback (no exception, original args returned).
  5. Unknown op + call-aws-only — log ERROR + still returns original args (no exception).
  6. forward() rewrite — tools/call body is rewritten when aws_mcp_server is set.
  7. forward() passthrough — non-aws_ tool names are not rewritten.
"""

from __future__ import annotations

import logging
import sys
import types
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: TC002 — runtime import in test file

# ---------------------------------------------------------------------------
# Inject mcp_proxy_for_aws stub into sys.modules so upstream.py can import it
# without the real [aws] optional dep being installed in CI.
# ---------------------------------------------------------------------------
_mcp_proxy_stub = types.ModuleType("mcp_proxy_for_aws")
_mcp_proxy_client_stub = types.ModuleType("mcp_proxy_for_aws.client")
_mcp_proxy_client_stub.aws_iam_streamablehttp_client = MagicMock()
sys.modules.setdefault("mcp_proxy_for_aws", _mcp_proxy_stub)
sys.modules.setdefault("mcp_proxy_for_aws.client", _mcp_proxy_client_stub)

# Now safe to import
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from tessera.config import UpstreamConfig
    from tessera.integrations.aws.upstream import AWSMcpUpstream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_upstream(
    aws_mcp_server: str | None = None,
    aws_mcp_routing: str = "specific-first",
) -> AWSMcpUpstream:
    """Build an AWSMcpUpstream without entering the async context (no real client needed)."""
    upstream = AWSMcpUpstream(
        name="test-upstream",
        endpoint="https://mcp.aws.example.com",
        aws_region="us-east-1",
        aws_mcp_routing=aws_mcp_routing,  # type: ignore[arg-type]
        aws_mcp_server=aws_mcp_server,
    )
    # Inject a mock client so forward() doesn't blow up on _client is None.
    upstream._client = MagicMock()
    return upstream


# ---------------------------------------------------------------------------
# 1. Config parsing
# ---------------------------------------------------------------------------

class TestRoutingConfigParsing:
    def test_parses_aws_mcp_server_and_routing(self) -> None:
        cfg = UpstreamConfig(
            name="aws-api",
            kind="aws_mcp",
            url="https://mcp.aws.example.com",
            aws_region="us-east-1",
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="specific-first",
        )
        assert cfg.aws_mcp_server == "aws-api-mcp-server"
        assert cfg.aws_mcp_routing == "specific-first"

    def test_default_routing_is_specific_first(self) -> None:
        cfg = UpstreamConfig(
            name="aws-api",
            kind="aws_mcp",
            url="https://mcp.aws.example.com",
            aws_region="us-east-1",
        )
        assert cfg.aws_mcp_routing == "specific-first"
        assert cfg.aws_mcp_server is None

    def test_call_aws_only_routing_accepted(self) -> None:
        cfg = UpstreamConfig(
            name="aws-api",
            kind="aws_mcp",
            url="https://mcp.aws.example.com",
            aws_region="us-east-1",
            aws_mcp_routing="call-aws-only",
        )
        assert cfg.aws_mcp_routing == "call-aws-only"


# ---------------------------------------------------------------------------
# 2. Default routing — aws_mcp_server=None, forward() posts unchanged body
# ---------------------------------------------------------------------------

class TestDefaultRoutingPassthrough:
    def test_forward_does_not_rewrite_when_no_server(self) -> None:
        """When aws_mcp_server is None, forward() posts the body unchanged."""
        upstream = _make_upstream(aws_mcp_server=None)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        upstream._client.post = AsyncMock(return_value=mock_response)

        import asyncio

        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "aws_ec2_RunInstances", "arguments": {"InstanceType": "m5.large"}},
        }

        asyncio.get_event_loop().run_until_complete(upstream.forward(body))
        call_args = upstream._client.post.call_args
        # Extract json kwarg
        posted_body: dict[str, Any] = call_args.kwargs.get("json") or call_args.args[1]
        # Tool name must NOT be rewritten to call_aws
        assert posted_body["params"]["name"] == "aws_ec2_RunInstances"


# ---------------------------------------------------------------------------
# 3. aws-api-mcp-server translation — known op
# ---------------------------------------------------------------------------

class TestAwsApiMcpServerTranslation:
    def test_translate_iam_pass_role(self) -> None:
        upstream = _make_upstream(aws_mcp_server="aws-api-mcp-server")
        result = upstream._translate_call_aws_op(
            "aws_iam_PassRole",
            {"RoleArn": "arn:aws:iam::123456789012:role/AdminRole"},
        )
        assert result["tool"] == "call_aws"
        assert "aws iam pass-role" in result["command"]
        assert "arn:aws:iam::123456789012:role/AdminRole" in result["command"]

    def test_translate_ec2_run_instances(self) -> None:
        upstream = _make_upstream(aws_mcp_server="aws-api-mcp-server")
        result = upstream._translate_call_aws_op(
            "aws_ec2_RunInstances",
            {"InstanceType": "m5.large", "ImageId": "ami-12345678"},
        )
        assert result["tool"] == "call_aws"
        assert "aws ec2 run-instances" in result["command"]
        assert "m5.large" in result["command"]


# ---------------------------------------------------------------------------
# 4. Unknown op + specific-first — fallback, no exception, log WARNING
# ---------------------------------------------------------------------------

class TestUnknownOpSpecificFirst:
    def test_returns_original_args_on_unknown_op(self) -> None:
        upstream = _make_upstream(
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="specific-first",
        )
        args: dict[str, Any] = {"SomeFutureArg": "value"}
        result = upstream._translate_call_aws_op("aws_future_UnknownOp9999", args)
        # Falls back to original args — not a call_aws shape
        assert isinstance(result, dict)
        assert result.get("tool") != "call_aws"

    def test_logs_warning_on_unknown_op(self, caplog: pytest.LogCaptureFixture) -> None:
        upstream = _make_upstream(
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="specific-first",
        )
        with caplog.at_level(logging.WARNING, logger="tessera.integrations.aws.upstream"):
            upstream._translate_call_aws_op("aws_future_UnknownOp9999", {})
        assert any("cli_translator_unknown_op" in r.message for r in caplog.records)
        assert any("specific-first" in r.message for r in caplog.records)

    def test_does_not_raise(self) -> None:
        upstream = _make_upstream(
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="specific-first",
        )
        result = upstream._translate_call_aws_op("aws_totally_NotReal", {"x": 1})
        assert result is not None


# ---------------------------------------------------------------------------
# 5. Unknown op + call-aws-only — log ERROR + still attempts (no exception)
# ---------------------------------------------------------------------------

class TestUnknownOpCallAwsOnly:
    def test_logs_error_on_unknown_op(self, caplog: pytest.LogCaptureFixture) -> None:
        upstream = _make_upstream(
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="call-aws-only",
        )
        with caplog.at_level(logging.ERROR, logger="tessera.integrations.aws.upstream"):
            upstream._translate_call_aws_op("aws_future_UnknownOp9999", {})
        assert any("cli_translator_unknown_op" in r.message for r in caplog.records)
        assert any("call-aws-only" in r.message for r in caplog.records)

    def test_does_not_raise_in_call_aws_only_mode(self) -> None:
        upstream = _make_upstream(
            aws_mcp_server="aws-api-mcp-server",
            aws_mcp_routing="call-aws-only",
        )
        result = upstream._translate_call_aws_op("aws_totally_NotReal", {"x": 1})
        assert result is not None


# ---------------------------------------------------------------------------
# 6. forward() body rewrite when aws_mcp_server is set
# ---------------------------------------------------------------------------

class TestForwardBodyRewrite:
    def test_forward_rewrites_canonical_to_call_aws(self) -> None:
        upstream = _make_upstream(aws_mcp_server="aws-api-mcp-server")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        upstream._client.post = AsyncMock(return_value=mock_response)

        import asyncio

        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "aws_iam_PassRole",
                "arguments": {"RoleArn": "arn:aws:iam::123456789012:role/Exec"},
            },
        }

        asyncio.get_event_loop().run_until_complete(upstream.forward(body))
        call_args = upstream._client.post.call_args
        posted_body: dict[str, Any] = call_args.kwargs.get("json") or call_args.args[1]
        assert posted_body["params"]["name"] == "call_aws"
        assert "command" in posted_body["params"]["arguments"]
        assert "iam" in posted_body["params"]["arguments"]["command"]


# ---------------------------------------------------------------------------
# 7. forward() passthrough for non-aws_ tool names
# ---------------------------------------------------------------------------

class TestForwardPassthroughNonAws:
    def test_non_aws_tool_not_rewritten(self) -> None:
        upstream = _make_upstream(aws_mcp_server="aws-api-mcp-server")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        upstream._client.post = AsyncMock(return_value=mock_response)

        import asyncio

        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_web", "arguments": {"query": "hello"}},
        }

        asyncio.get_event_loop().run_until_complete(upstream.forward(body))
        call_args = upstream._client.post.call_args
        posted_body: dict[str, Any] = call_args.kwargs.get("json") or call_args.args[1]
        assert posted_body["params"]["name"] == "search_web"
