"""AWS MCP upstream client using IAM-signed streamable HTTP.

Routes JSON-RPC traffic to AWS-hosted MCP servers via SigV4-signed requests.
Requires the `aws` optional dependency group:
    pip install cloudmorph-tessera[aws]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx
from fastapi.responses import JSONResponse

# Resolved when the `aws` optional-dependency group is installed.
# mcp_proxy_for_aws v1.4.x exports aws_iam_streamablehttp_client from .client submodule
# (not the top-level package). Imported here so attribute access still works.
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Header names AWS MCP service injects to signal service routing context.
_AWS_VIA_HEADER = "aws:ViaAWSMCPService"
_AWS_CALLED_VIA_HEADER = "aws:CalledViaAWSMCP"

# The identifier for the official awslabs aws-api-mcp-server.
_AWS_API_MCP_SERVER = "aws-api-mcp-server"


class AWSMcpUpstream:
    """Async context-manager wrapper around the AWS IAM streamable-HTTP MCP client.

    Each upstream configured with ``kind: aws_mcp`` gets one instance of this
    class.  ``_lifespan`` enters/exits the async context so the client lives
    for the process lifetime.

    Args:
        name: The upstream name from tessera.yaml (used for logging / metrics).
        endpoint: Full HTTPS URL of the AWS MCP server endpoint.
        aws_region: AWS region the endpoint is in (e.g. ``us-east-1``).
        aws_service: SigV4 service name (default ``aws-mcp``).
        aws_endpoint_override: Optional endpoint override passed through to
            botocore (useful for testing against LocalStack).
        timeout_seconds: Per-request timeout in seconds (default 30).
        aws_mcp_routing: Routing mode — ``"specific-first"`` (default, per Q2
            locked decision 2026-05-16) or ``"call-aws-only"``.  Controls
            fallback behaviour when ``to_call_aws`` returns None for an op.
        aws_mcp_server: When set to ``"aws-api-mcp-server"``, the upstream
            targets the official ``awslabs/mcp/aws-api-mcp-server`` and
            canonical ops are wrapped via ``cli_translator.to_call_aws()``
            before the SigV4 POST.  When ``None`` (default), pass through
            unchanged (legacy direct-service routing).
    """

    def __init__(
        self,
        name: str,
        endpoint: str,
        aws_region: str,
        aws_service: str = "aws-mcp",
        aws_endpoint_override: str | None = None,
        timeout_seconds: int = 30,
        aws_mcp_routing: Literal["specific-first", "call-aws-only"] = "specific-first",
        aws_mcp_server: str | None = None,
    ) -> None:
        self.name = name
        self.endpoint = endpoint
        self.aws_region = aws_region
        self.aws_service = aws_service
        self.aws_endpoint_override = aws_endpoint_override
        self.timeout_seconds = timeout_seconds
        self.aws_mcp_routing = aws_mcp_routing
        self.aws_mcp_server = aws_mcp_server
        self._client: Any = None

    async def __aenter__(self) -> AWSMcpUpstream:
        kwargs: dict[str, Any] = {
            "endpoint_url": self.endpoint,
            "region_name": self.aws_region,
            "service_name": self.aws_service,
        }
        if self.aws_endpoint_override:
            kwargs["endpoint_override"] = self.aws_endpoint_override

        self._client = aws_iam_streamablehttp_client(**kwargs)
        # Enter the client's own async context if it is one.
        if hasattr(self._client, "__aenter__"):
            await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client is not None and hasattr(self._client, "__aexit__"):
            try:
                await self._client.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:  # noqa: BLE001
                pass
        self._client = None

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

    async def forward(self, jsonrpc_body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        """POST *jsonrpc_body* through the AWS IAM streamable HTTP client.

        When ``aws_mcp_server == "aws-api-mcp-server"``, the tools/call body is
        pre-processed by ``_translate_call_aws_op`` before the SigV4 POST so
        that canonical op names are mapped to the ``call_aws`` surface expected
        by the official awslabs server.

        Returns:
            Parsed JSON-RPC response dict on success, or a ``JSONResponse``
            carrying a ``-32603`` error on failure.  The dict may have an
            extra ``_aws_context`` key with header values captured from the
            response for audit enrichment.
        """
        if self._client is None:
            logger.error("event=aws_upstream_not_initialized upstream=%s", self.name)
            return _aws_error(jsonrpc_body.get("id", 1), "AWS upstream not initialized")

        # When targeting aws-api-mcp-server, translate canonical ops to call_aws.
        body = jsonrpc_body
        if self.aws_mcp_server == _AWS_API_MCP_SERVER:
            method = body.get("method", "")
            params = body.get("params") or {}
            if method == "tools/call" and isinstance(params, dict):
                tool_name = params.get("name", "")
                tool_args: dict[str, Any] = params.get("arguments") or {}
                if tool_name and tool_name.startswith("aws_"):
                    translated = self._translate_call_aws_op(tool_name, tool_args)
                    # translated is either {"tool": "call_aws", "command": ...} or original args
                    if "tool" in translated and translated.get("tool") == "call_aws":
                        body = {
                            **body,
                            "params": {
                                **params,
                                "name": "call_aws",
                                "arguments": {"command": translated["command"]},
                            },
                        }

        try:
            response: httpx.Response = await asyncio.wait_for(
                self._client.post(self.endpoint, json=body),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            logger.warning("event=aws_upstream_timeout upstream=%s", self.name)
            return _aws_error(jsonrpc_body.get("id", 1), "AWS upstream timeout")
        except httpx.HTTPStatusError as exc:
            logger.error(
                "event=aws_upstream_http_error upstream=%s status=%d",
                self.name,
                exc.response.status_code,
            )
            return _aws_error(jsonrpc_body.get("id", 1), f"AWS upstream HTTP error: {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            # Catches botocore.exceptions.NoCredentialsError and any transport error.
            exc_name = type(exc).__name__
            logger.error("event=aws_upstream_error upstream=%s exc=%s error=%s", self.name, exc_name, exc)
            if "NoCredentials" in exc_name or "CredentialNotFound" in exc_name:
                return _aws_error(jsonrpc_body.get("id", 1), "AWS credentials not found — check boto3 chain")
            return _aws_error(jsonrpc_body.get("id", 1), f"AWS upstream error: {exc}")

        if response.status_code >= 500:
            logger.warning(
                "event=aws_upstream_5xx upstream=%s status=%d", self.name, response.status_code
            )
            return _aws_error(jsonrpc_body.get("id", 1), "AWS upstream 5xx error")

        # Capture AWS service-context headers for audit enrichment.
        aws_context: dict[str, str] = {}
        via = response.headers.get(_AWS_VIA_HEADER)
        called_via = response.headers.get(_AWS_CALLED_VIA_HEADER)
        if via:
            aws_context["via_aws_mcp_service"] = via
        if called_via:
            aws_context["called_via_aws_mcp"] = called_via

        parsed: dict[str, Any] = response.json()
        if aws_context:
            parsed["_aws_context"] = aws_context
        return parsed


def _aws_error(request_id: Any, message: str) -> JSONResponse:
    """Return a JSON-RPC -32603 error response as a JSONResponse."""
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": {"reason": message},
            },
        }
    )
