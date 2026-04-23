"""ControlCenterClient — extracted from 5 byte-identical copies in aws/azure/gcp/databricks/snowflake executors.

Speaks the upstream control-plane protocol: claim_job, fetch_job, post_status,
post_heartbeat, post_complete. All HTTP interactions go through `_request`,
which uses urllib (stdlib-only, zero install friction for executors).

This module is intentionally minimal — the client is just the wire protocol.
Lifecycle decisions (when to heartbeat, how to back off on claim failures,
how to handle 409 Conflict on claim) live in BaseExecutor.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from cloudmorph_common.errors import CloudMorphCommonError


class ControlCenterError(CloudMorphCommonError):
    """Raised by ControlCenterClient on non-2xx HTTP responses or network errors."""

    def __init__(self, status: int, payload: dict[str, Any] | None = None):
        super().__init__(f"Control Center error {status}")
        self.status = status
        self.payload = payload or {}


def _parse_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    return json.loads(text)


class ControlCenterClient:
    """Wire-protocol client for the upstream Control Center API.

    Args:
        base_url: Upstream API base URL (e.g., https://api.cloudmorph.io).
        install_token: Long-lived executor install token. Used for `claim_job`.
        timeout_seconds: HTTP request timeout. Default 30s.

    Notes:
        - `claim_job` uses `install_token`; subsequent per-job calls use the
          short-lived `job_token` returned by claim.
        - On HTTP 204 / 404 / 409 from claim, returns None instead of raising;
          callers treat these as "no job available right now" and back off.
    """

    def __init__(self, base_url: str, install_token: str, timeout_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.install_token = install_token
        self.timeout_seconds = timeout_seconds

    def claim_job(
        self,
        tenant_id: str,
        account_id: str,
        capabilities: list[str],
        executor_id: str | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "tenantId": tenant_id,
            "accountId": account_id,
            "capabilities": capabilities,
        }
        if executor_id:
            payload["executorId"] = executor_id

        try:
            status, data = self._request(
                "POST",
                "/controlcenter/executor/claim",
                self.install_token,
                payload,
            )
        except ControlCenterError as exc:
            if exc.status in {204, 404, 409}:
                return None
            raise

        if status == 204:
            return None
        if not data:
            return None
        return data

    def fetch_job(self, job_id: str, job_token: str) -> dict[str, Any]:
        _, data = self._request(
            "GET",
            f"/controlcenter/executor/jobs/{job_id}",
            job_token,
            None,
        )
        return data

    def post_status(
        self,
        job_id: str,
        job_token: str,
        status: str,
        logs: str | None = None,
        lease_until: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": status}
        if logs:
            payload["logs"] = logs
        if lease_until:
            payload["leaseUntil"] = lease_until
        if reason:
            payload["reason"] = reason
        _, data = self._request(
            "POST",
            f"/controlcenter/executor/jobs/{job_id}/status",
            job_token,
            payload,
        )
        return data

    def post_heartbeat(
        self,
        job_id: str,
        job_token: str,
        lease_until: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if lease_until:
            payload["leaseUntil"] = lease_until
        _, data = self._request(
            "POST",
            f"/controlcenter/executor/jobs/{job_id}/heartbeat",
            job_token,
            payload,
        )
        return data

    def post_complete(
        self,
        job_id: str,
        job_token: str,
        status: str,
        artifacts: list[dict[str, Any]] | None = None,
        logs: str | None = None,
        reason: str | None = None,
        summary: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": status}
        if artifacts is not None:
            payload["artifacts"] = artifacts
        if logs:
            payload["logs"] = logs
        if reason:
            payload["reason"] = reason
        if summary:
            payload["summary"] = summary
        if result is not None:
            payload["result"] = result
        _, data = self._request(
            "POST",
            f"/controlcenter/executor/jobs/{job_id}/complete",
            job_token,
            payload,
        )
        return data

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:  # noqa: S310
                text = resp.read().decode("utf-8")
                data = _parse_json(text)
                return resp.status, data
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8") if exc.fp else ""
            payload_data: dict[str, Any] = {}
            if text:
                try:
                    payload_data = _parse_json(text)
                except json.JSONDecodeError:
                    payload_data = {"message": text}
            raise ControlCenterError(exc.code, payload_data) from exc
        except urllib.error.URLError as exc:
            raise ControlCenterError(0, {"message": str(exc)}) from exc
