import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


class ControlCenterError(Exception):
    def __init__(self, status: int, payload: Optional[Dict[str, Any]] = None):
        super().__init__(f"Control Center error {status}")
        self.status = status
        self.payload = payload or {}


def _parse_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    return json.loads(text)


class ControlCenterClient:
    def __init__(self, base_url: str, install_token: str, timeout_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.install_token = install_token
        self.timeout_seconds = timeout_seconds

    def claim_job(
        self,
        tenant_id: str,
        account_id: str,
        capabilities: list[str],
        executor_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
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

    def fetch_job(self, job_id: str, job_token: str) -> Dict[str, Any]:
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
        logs: Optional[str] = None,
        lease_until: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": status}
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
        lease_until: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
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
        artifacts: Optional[list[Dict[str, Any]]] = None,
        logs: Optional[str] = None,
        reason: Optional[str] = None,
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": status}
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
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                text = resp.read().decode("utf-8")
                data = _parse_json(text)
                return resp.status, data
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8") if exc.fp else ""
            payload_data = {}
            if text:
                try:
                    payload_data = _parse_json(text)
                except json.JSONDecodeError:
                    payload_data = {"message": text}
            raise ControlCenterError(exc.code, payload_data) from exc
        except urllib.error.URLError as exc:
            raise ControlCenterError(0, {"message": str(exc)}) from exc
