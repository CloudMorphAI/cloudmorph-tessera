import json
import os
import random
import socket
import threading
import time
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from azure.storage.blob import BlobClient

from controlcenter_client import ControlCenterClient, ControlCenterError
from job_runner import run as run_job

try:
    from jsonschema import ValidationError, validate as jsonschema_validate
except ImportError:  # pragma: no cover
    ValidationError = Exception
    jsonschema_validate = None


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _load_job_schema() -> Optional[Dict[str, Any]]:
    override = os.getenv("CONTROL_CENTER_JOB_SCHEMA_PATH")
    if override:
        schema_path = Path(override)
    else:
        try:
            repo_root = Path(__file__).resolve().parents[3]
        except IndexError:
            repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "contracts" / "job.schema.json"

    if not schema_path.exists():
        return None
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_job(job: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> None:
    if not schema or jsonschema_validate is None:
        return
    required = set(schema.get("required", []))
    if required and not required.issubset(job.keys()):
        return
    jsonschema_validate(job, schema)


def _heartbeat_loop(
    client: ControlCenterClient,
    job_id: str,
    job_token: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(interval_seconds):
        try:
            client.post_heartbeat(job_id, job_token)
        except ControlCenterError as exc:
            print(f"heartbeat error status={exc.status} payload={exc.payload}")


def _sleep_with_backoff(current: float, jitter: float = 1.0) -> None:
    time.sleep(current + random.uniform(0, jitter))


def _upload_artifacts(job_id: str, result: Dict[str, Any], logs: Optional[str]) -> list[Dict[str, Any]]:
    provider = os.getenv("STORAGE_PROVIDER", "azure").lower()
    if provider != "azure":
        return []
    account = os.getenv("STORAGE_ACCOUNT")
    container = os.getenv("STORAGE_CONTAINER")
    sas_token = os.getenv("STORAGE_SAS_TOKEN") or ""
    prefix = os.getenv("STORAGE_PREFIX", "").strip("/")
    artifact_base = os.getenv("ARTIFACT_BASE_PREFIX") or f"controlcentre/jobs/{job_id}"
    if not account or not container:
        raise RuntimeError("storage account/container missing for artifact upload")
    base = f"{prefix}/{artifact_base}".strip("/") if prefix else artifact_base
    refs: list[Dict[str, Any]] = []
    errors: list[str] = []

    def _upload(kind: str, content: str, content_type: str):
        blob_name = f"{base}/{kind}"
        url = f"https://{account}.blob.core.windows.net/{container}/{blob_name}"
        if sas_token:
            url = f"{url}?{sas_token.lstrip('?')}"
        client = BlobClient.from_blob_url(url)
        client.upload_blob(content, overwrite=True, content_settings=None)
        refs.append({"kind": kind.split('.')[0], "uri": f"https://{account}.blob.core.windows.net/{container}/{blob_name}"})

    summary_text = str(result.get("summary") or f"Job {result.get('status', 'completed')}")[: 8 * 1024]
    result_payload = result.get("result", result)
    try:
        _upload("summary.txt", summary_text, "text/plain")
    except Exception as exc:  # pragma: no cover - safety net
        errors.append(f"summary:{exc}")
    try:
        _upload("result.json", json.dumps(result_payload), "application/json")
    except Exception as exc:  # pragma: no cover - safety net
        errors.append(f"result:{exc}")
    if logs:
        try:
            _upload("logs.jsonl", str(logs), "application/json")
        except Exception as exc:  # pragma: no cover - safety net
            errors.append(f"logs:{exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return refs


def main() -> None:
    base_url = _require_env("CONTROL_CENTER_API_URL")
    install_token = _require_env("CONTROL_CENTER_EXECUTOR_TOKEN")
    tenant_id = _require_env("CONTROL_CENTER_TENANT_ID")
    account_id = _require_env("CONTROL_CENTER_ACCOUNT_ID")

    capabilities_raw = os.getenv("CONTROL_CENTER_CAPABILITIES", "agent.run")
    capabilities = [cap.strip() for cap in capabilities_raw.split(",") if cap.strip()]
    executor_id = os.getenv("EXECUTOR_ID") or socket.gethostname()

    heartbeat_seconds = _float_env("HEARTBEAT_SECONDS", 20.0)
    base_backoff = _float_env("POLL_BASE_SECONDS", 2.0)
    max_backoff = _float_env("POLL_MAX_SECONDS", 15.0)

    schema = _load_job_schema()
    client = ControlCenterClient(base_url, install_token)

    oneshot_job_id = os.getenv("JOB_ID")
    oneshot_job_token = os.getenv("JOB_TOKEN")
    if oneshot_job_id and oneshot_job_token:
        try:
            fetched = client.fetch_job(oneshot_job_id, oneshot_job_token)
            job_payload = fetched.get("payload") or fetched
        except ControlCenterError as exc:
            print(f"fetch error status={exc.status} payload={exc.payload}")
            sys.exit(1)
        try:
            client.post_status(oneshot_job_id, oneshot_job_token, "running")
        except ControlCenterError as exc:
            print(f"status error status={exc.status} payload={exc.payload}")
        result = run_job(job_payload)
        status = result.get("status", "completed")
        artifacts = result.get("artifacts", [])
        logs = result.get("logs")
        summary = result.get("summary") or f"Job {status}"
        reason = result.get("reason")
        try:
            upload_refs = _upload_artifacts(oneshot_job_id, result, logs)
            artifacts = artifacts + upload_refs
        except Exception as exc:  # pragma: no cover - safety net
            status = "failed"
            reason = f"artifact_upload:{exc}"
        try:
            client.post_complete(
                oneshot_job_id,
                oneshot_job_token,
                status,
                artifacts=artifacts,
                logs=logs,
                reason=reason,
                summary=summary,
                result=result.get("result"),
            )
        except ControlCenterError as exc:
            print(f"complete error status={exc.status} payload={exc.payload}")
            sys.exit(1)
        sys.exit(0 if status == "completed" else 1)

    backoff = base_backoff
    while True:
        try:
            claim = client.claim_job(tenant_id, account_id, capabilities, executor_id)
        except ControlCenterError as exc:
            print(f"claim error status={exc.status} payload={exc.payload}")
            _sleep_with_backoff(backoff)
            backoff = min(max_backoff, backoff * 2)
            continue

        if not claim:
            _sleep_with_backoff(backoff)
            backoff = min(max_backoff, backoff * 2)
            continue

        backoff = base_backoff

        job_id = claim.get("jobId")
        job_token = claim.get("jobToken")
        if not job_id or not job_token:
            print("claim response missing jobId/jobToken")
            continue

        job = claim.get("job") or claim
        try:
            _validate_job(job, schema)
        except ValidationError as exc:
            print(f"job schema validation failed: {exc}")
            try:
                client.post_complete(job_id, job_token, "failed", reason=str(exc))
            except ControlCenterError as complete_exc:
                print(
                    "complete error after validation failure "
                    f"status={complete_exc.status} payload={complete_exc.payload}"
                )
            continue

        try:
            client.post_status(job_id, job_token, "running")
        except ControlCenterError as exc:
            print(f"status error status={exc.status} payload={exc.payload}")

        stop_event = threading.Event()
        heartbeat_thread = None
        if heartbeat_seconds > 0:
            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop,
                args=(client, job_id, job_token, heartbeat_seconds, stop_event),
                daemon=True,
            )
            heartbeat_thread.start()

        status = "completed"
        artifacts: list[Dict[str, Any]] = []
        logs: Optional[str] = None
        reason: Optional[str] = None
        summary: Optional[str] = None
        result_payload: Optional[Dict[str, Any]] = None

        try:
            result = run_job(job)
            status = result.get("status", "completed")
            artifacts = result.get("artifacts", [])
            logs = result.get("logs")
            reason = result.get("reason")
            summary = result.get("summary")
            result_payload = result.get("result")
            upload_refs = _upload_artifacts(job_id, result, logs)
            artifacts = artifacts + upload_refs
        except Exception as exc:
            status = "failed"
            reason = str(exc)
            summary = summary or f"Job {status}"
        finally:
            stop_event.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=1)

        try:
            summary = summary or f"Job {status}"
            client.post_complete(
                job_id,
                job_token,
                status,
                artifacts=artifacts,
                logs=logs,
                reason=reason,
                summary=summary,
                result=result_payload,
            )
        except ControlCenterError as exc:
            print(f"complete error status={exc.status} payload={exc.payload}")


if __name__ == "__main__":
    main()
