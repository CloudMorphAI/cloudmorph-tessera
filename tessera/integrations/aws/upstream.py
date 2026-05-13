"""AWS MCP upstream client using IAM-signed streamable HTTP.

Routes JSON-RPC traffic to AWS-hosted MCP servers via SigV4-signed requests.
Requires the `aws` optional dependency group:
    pip install cloudmorph-tessera[aws]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi.responses import JSONResponse

# Resolved when the `aws` optional-dependency group is installed.
from mcp_proxy_for_aws import aws_iam_streamablehttp_client  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Header names AWS MCP service injects to signal service routing context.
_AWS_VIA_HEADER = "aws:ViaAWSMCPService"
_AWS_CALLED_VIA_HEADER = "aws:CalledViaAWSMCP"


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
    """

    def __init__(
        self,
        name: str,
        endpoint: str,
        aws_region: str,
        aws_service: str = "aws-mcp",
        aws_endpoint_override: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.name = name
        self.endpoint = endpoint
        self.aws_region = aws_region
        self.aws_service = aws_service
        self.aws_endpoint_override = aws_endpoint_override
        self.timeout_seconds = timeout_seconds
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

    async def forward(self, jsonrpc_body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        """POST *jsonrpc_body* through the AWS IAM streamable HTTP client.

        Returns:
            Parsed JSON-RPC response dict on success, or a ``JSONResponse``
            carrying a ``-32603`` error on failure.  The dict may have an
            extra ``_aws_context`` key with header values captured from the
            response for audit enrichment.
        """
        if self._client is None:
            logger.error("event=aws_upstream_not_initialized upstream=%s", self.name)
            return _aws_error(jsonrpc_body.get("id", 1), "AWS upstream not initialized")

        try:
            response: httpx.Response = await asyncio.wait_for(
                self._client.post(self.endpoint, json=jsonrpc_body),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
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
