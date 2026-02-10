"""CloudMorph Python SDK client.

Provides a developer-friendly interface to the CloudMorph MCP server.
Zero external dependencies — uses only stdlib (urllib, json).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class CloudMorphError(Exception):
    """Base error for CloudMorph API failures."""

    def __init__(self, message: str, status: int = -1, code: str = "unknown", data: Optional[Dict] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data or {}


class RateLimitError(CloudMorphError):
    """Raised when rate limits are exceeded."""

    def __init__(self, message: str, retry_after_seconds: int = 60, data: Optional[Dict] = None):
        super().__init__(message, status=429, code="rate_limit_exceeded", data=data)
        self.retry_after_seconds = retry_after_seconds


class CloudMorph:
    """CloudMorph Control Centre SDK client.

    Args:
        token: Integration token (from Control Centre).
        base_url: MCP server URL. Defaults to ``https://mcp.cloudmorph.io``.
        timeout: Request timeout in seconds. Defaults to 60.

    Example::

        cm = CloudMorph(token="cm_...")
        result = cm.request("aws.s3.list_buckets", wait=True)
        print(result["output"])
    """

    DEFAULT_BASE_URL = "https://mcp.cloudmorph.io"
    JSON_RPC_VERSION = "2.0"

    def __init__(
        self,
        token: str,
        base_url: Optional[str] = None,
        timeout: int = 60,
    ):
        if not token:
            raise ValueError("CloudMorph token is required.")
        self.token = token
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._rpc_id = 0

    def request(
        self,
        action: str,
        *,
        targets: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        account_id: Optional[str] = None,
        wait: bool = False,
        wait_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Submit a policy request.

        Args:
            action: Action name (e.g., ``aws.s3.list_buckets``).
            targets: Target account IDs.
            payload: Action-specific parameters.
            account_id: Shorthand for ``targets=[account_id]``.
            wait: Wait for result using default timeout.
            wait_seconds: Wait for result up to N seconds.

        Returns:
            Dict with requestId, decision, status, output, etc.
        """
        args: Dict[str, Any] = {"action": action}
        resolved_targets = targets or (([account_id] if account_id else None))
        if resolved_targets:
            args["targets"] = resolved_targets
        if payload:
            args["payload"] = payload
        if wait_seconds is not None:
            args["waitSeconds"] = wait_seconds
        elif wait:
            args["wait"] = True

        return self._call_tool("cloudmorph_request", args)

    def request_and_wait(
        self,
        action: str,
        *,
        targets: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        account_id: Optional[str] = None,
        poll_interval: float = 2.0,
        max_wait: float = 120.0,
    ) -> Dict[str, Any]:
        """Submit a request and poll until completion.

        Args:
            action: Action name.
            targets: Target account IDs.
            payload: Action-specific parameters.
            account_id: Shorthand for targets.
            poll_interval: Seconds between polls (default 2).
            max_wait: Maximum wait time in seconds (default 120).

        Returns:
            Final result dict with output.
        """
        result = self.request(
            action, targets=targets, payload=payload,
            account_id=account_id, wait=True,
        )

        if self._is_terminal(result.get("status", "")) or result.get("decision") == "block":
            return result

        start = time.monotonic()
        request_id = result.get("requestId", "")
        while time.monotonic() - start < max_wait:
            time.sleep(poll_interval)
            status = self.get_request_status(request_id)
            if self._is_terminal(status.get("status", "")) or status.get("decision") == "block":
                return status

        return result

    def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """Get status of a request."""
        return self._call_tool("cloudmorph_request_status", {"requestId": request_id})

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a job."""
        return self._call_tool("cloudmorph_job_status", {"jobId": job_id})

    # ─── JSON-RPC transport ───────────────────────────────────────

    def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._rpc_id += 1
        body = {
            "jsonrpc": self.JSON_RPC_VERSION,
            "id": self._rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        resp = self._http_post(f"{self.base_url}/mcp", body)

        if "error" in resp:
            err = resp["error"]
            raise CloudMorphError(
                err.get("message", "Unknown error"),
                code=err.get("message", "unknown"),
                data=err.get("data"),
            )

        result = resp.get("result", {})
        content = result.get("content", [])
        if not content:
            return {}

        text = content[0].get("text", "{}")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

        if result.get("isError"):
            raise CloudMorphError(
                parsed.get("error", parsed.get("message", "Request failed")),
                status=parsed.get("statusCode", -1),
                code=parsed.get("error", "unknown"),
                data=parsed,
            )

        return parsed

    def _http_post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8") if exc.fp else ""
            if exc.code == 429:
                retry_after = int(exc.headers.get("Retry-After", "60")) if exc.headers else 60
                payload = {}
                if text:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        pass
                raise RateLimitError(
                    payload.get("message", "Rate limit exceeded"),
                    retry_after,
                    payload,
                ) from exc
            payload = {}
            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {"message": text}
            raise CloudMorphError(
                payload.get("message", f"HTTP {exc.code}"),
                status=exc.code,
                code=payload.get("error", "http_error"),
                data=payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise CloudMorphError(str(exc), code="connection_error") from exc

    @staticmethod
    def _is_terminal(status: str) -> bool:
        return status.lower() in {"completed", "failed", "cancelled", "canceled", "blocked"}
