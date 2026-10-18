"""AWS MCP SigV4 authentication over Tessera's bounded Streamable HTTP transport.

The AWS SDK streamable client is a context manager yielding MCP streams, not an
HTTPX client. Reuse its official auth implementation and existing provider chain
without replacing Tessera's parsing, session isolation, deadlines or no-replay rule.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

import boto3
from botocore.exceptions import BotoCoreError, NoCredentialsError
from fastapi.responses import JSONResponse
from mcp_proxy_for_aws.client import SigV4HTTPXAuth, validate_endpoint_url

from tessera.integrations.streamable_http.upstream import StreamableHttpUpstream

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)
_AWS_API_MCP_SERVER = "aws-api-mcp-server"


class AWSMcpUpstream(StreamableHttpUpstream):
    """Signed MCP transport; legacy canonical translation is opt-in only.

    ``aws_mcp_server=None`` preserves actual tools/list names unchanged. The
    ``aws-api-mcp-server`` option retains the older awslabs call_aws translation.
    Session scopes must come from the authenticated proxy, never HTTP headers.
    """

    # Official AWS MCP returns application/json {} on initialized HTTP 202.
    # Permit precisely that two-byte acknowledgment; retain the generic limit
    # and strict rejection of all other body shapes.
    _max_notification_body_bytes = 2

    def _accept_notification_body(self, body: bytes, response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return not body or (body == b"{}" and content_type == "application/json")

    def __init__(
        self, name: str, endpoint: str, aws_region: str,
        aws_service: str = "aws-mcp", aws_endpoint_override: str | None = None,
        timeout_seconds: int = 30,
        aws_mcp_routing: Literal["specific-first", "call-aws-only"] = "specific-first",
        aws_mcp_server: str | None = None,
    ) -> None:
        # The SDK supports an explicit endpoint, not an endpoint_override keyword.
        # Refuse unsupported routing rather than silently signing another target.
        if aws_endpoint_override is not None:
            raise ValueError("AWS MCP endpoint override is unsupported; configure endpoint explicitly")
        validate_endpoint_url(endpoint)
        super().__init__(name, endpoint, request_timeout_s=timeout_seconds)
        self.endpoint = self.url
        self.aws_region = aws_region
        self.aws_service = aws_service
        self.aws_endpoint_override = aws_endpoint_override
        self.timeout_seconds = timeout_seconds
        self.aws_mcp_routing = aws_mcp_routing
        self.aws_mcp_server = aws_mcp_server

    def _http_auth(self) -> httpx.Auth:
        # Keep the refreshable provider object, as the official SDK does. Never
        # freeze, serialize, persist or log credential material.
        session = boto3.Session(region_name=self.aws_region)
        return SigV4HTTPXAuth(session.get_credentials(), self.aws_service, self.aws_region)

    def _enrich_response(self, body: dict[str, Any], response: httpx.Response) -> dict[str, Any]:
        context = {}
        for header, key in (("aws:ViaAWSMCPService", "via_aws_mcp_service"),
                            ("aws:CalledViaAWSMCP", "called_via_aws_mcp")):
            if value := response.headers.get(header):
                context[key] = value
        return {**body, "_aws_context": context} if context else body

    def _translate_call_aws_op(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Translate a canonical aws_*_* invocation into a call_aws-shaped request.

        Called when the upstream targets ``aws-api-mcp-server``.  Invokes
        ``cli_translator.to_call_aws()``.

        When the translator returns ``None`` (unknown op):
        - ``specific-first``: log WARNING and return ``args`` unchanged so the
          caller can fall through to legacy direct-service routing.
        - ``call-aws-only``: log ERROR (operator explicitly chose this mode but
          the op isn't translatable) and still return ``args`` unchanged so the
          call is attempted rather than hard-failing.

        Returns:
            Either ``{"tool": "call_aws", "command": "<cli string>"}`` when
            translation succeeds, or the original ``args`` dict when it doesn't.
        """
        from tessera.integrations.aws import cli_translator  # noqa: PLC0415

        result = cli_translator.to_call_aws(tool_name, args)
        if result is not None:
            return result

        # Translation unknown — log and decide fallback path.
        if self.aws_mcp_routing == "call-aws-only":
            logger.error(
                "event=cli_translator_unknown_op canonical=%s routing=call-aws-only "
                "upstream=%s reason=op_not_translatable_but_mode_requires_it",
                tool_name,
                self.name,
            )
        else:
            logger.warning(
                "event=cli_translator_unknown_op canonical=%s routing=specific-first "
                "upstream=%s reason=falling_through_to_legacy",
                tool_name,
                self.name,
            )
        return args

    async def forward(
        self, jsonrpc_body: dict[str, Any], *, session_scope: str = "default",
    ) -> dict[str, Any] | JSONResponse:
        """Forward once using the bounded, principal-isolated signed transport."""
        body = jsonrpc_body
        if isinstance(body, dict) and self.aws_mcp_server == _AWS_API_MCP_SERVER:
            params = body.get("params") or {}
            if body.get("method") == "tools/call" and isinstance(params, dict):
                tool_name = params.get("name", "")
                tool_args = params.get("arguments") or {}
                if isinstance(tool_name, str) and tool_name.startswith("aws_") and isinstance(tool_args, dict):
                    translated = self._translate_call_aws_op(tool_name, tool_args)
                    if translated.get("tool") == "call_aws":
                        body = {**body, "params": {**params, "name": "call_aws",
                                "arguments": {"command": translated["command"]}}}
        try:
            return await super().forward(body, session_scope=session_scope)
        except BotoCoreError as exc:
            # Signing/refresh failures must not escape or expose provider messages.
            logger.warning("event=aws_upstream_auth_error upstream=%s exception=%s", self.name, type(exc).__name__)
            reason = "AWS credentials not found - check boto3 chain" if isinstance(exc, NoCredentialsError) else "AWS upstream authentication failed; request was not replayed"
            request_id = jsonrpc_body.get("id") if isinstance(jsonrpc_body, dict) else None
            return _aws_error(request_id, reason)


def _aws_error(request_id: Any, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": -32603, "message": "Internal error", "data": {"reason": message}}})
