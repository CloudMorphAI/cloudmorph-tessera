"""BaseExecutor — common claim/run/heartbeat/complete loop with signal handling.

Replaces ~70% of the structurally near-duplicate code across the 5 executor
main.py files. Each cloud subclasses BaseExecutor and implements `run_action`
(typically by dispatching through a per-cloud action registry).

Lifecycle:
1. SIGTERM/SIGINT registers shutdown
2. claim_job loop (exponential backoff on no-job / errors)
3. On claim: validate via job schema, post status=running, start heartbeat thread
4. run_action(job) → result dict
5. Upload artifacts via ArtifactWriter
6. post_complete with status, artifacts, logs, summary, reason, result
7. Shutdown drains heartbeat thread; ready for next job

One-shot mode (JOB_ID + JOB_TOKEN env): fetch one job, run, exit. No claim loop.
"""

from __future__ import annotations

import os
import random
import signal
import socket
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from cloudmorph_common.artifacts.base import ArtifactWriter
from cloudmorph_common.audit.emitter import AuditEmitter
from cloudmorph_common.client import ControlCenterClient, ControlCenterError
from cloudmorph_common.errors import ArtifactUploadError, BaseExecutorError
from cloudmorph_common.log import StructuredLogger
from cloudmorph_common.redact import redact_token, safe_payload_summary
from cloudmorph_common.settings import ExecutorSettings


class BaseExecutor(ABC):
    """Common executor lifecycle. Subclasses implement run_action.

    Args:
        settings: Loaded ExecutorSettings (Pydantic).
        client: ControlCenterClient instance.
        artifact_writer: Per-cloud ArtifactWriter.
        audit_emitter: Optional AuditEmitter; one is constructed if omitted.
        logger: Optional StructuredLogger; one is constructed if omitted.
    """

    def __init__(
        self,
        settings: ExecutorSettings,
        client: ControlCenterClient,
        artifact_writer: ArtifactWriter,
        audit_emitter: AuditEmitter | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.artifact_writer = artifact_writer
        self.audit = audit_emitter
        self.log = logger or StructuredLogger(
            service=self.__class__.__name__.replace("Executor", "").lower() + "-executor",
            min_level=settings.log_level,
        )
        self._shutdown = threading.Event()
        self._executor_id = settings.executor_id or socket.gethostname()

    # ── Subclass surface ──────────────────────────────────────────

    @abstractmethod
    def run_action(self, job: dict[str, Any]) -> dict[str, Any]:
        """Dispatch the job's action. Must return a dict with at least
        {status: 'completed'|'failed', summary, result, logs, reason?, artifacts?}.
        """
        ...

    def validate_job(self, job: dict[str, Any]) -> None:
        """Optional validation hook. Default: no-op. Subclasses may override."""

    # ── Public entrypoint ─────────────────────────────────────────

    def run(self) -> None:
        """Main entrypoint. Runs daemon loop or one-shot per settings."""
        self._log_startup()
        self._install_signal_handlers()

        if self.settings.job_id and self.settings.job_token:
            self._run_oneshot(self.settings.job_id, self.settings.job_token)
            return
        self._run_daemon()

    # ── Daemon mode ───────────────────────────────────────────────

    def _run_daemon(self) -> None:
        backoff = self.settings.poll_base_seconds
        capabilities = self.settings.capabilities_list

        while not self._shutdown.is_set():
            try:
                claim = self.client.claim_job(
                    self.settings.control_center_tenant_id,
                    self.settings.control_center_account_id,
                    capabilities,
                    self._executor_id,
                )
            except ControlCenterError as exc:
                self.log.warn("executor.claim.error", status=exc.status, payload=exc.payload)
                self._sleep_backoff(backoff)
                backoff = min(self.settings.poll_max_seconds, backoff * 2)
                continue

            if not claim:
                self._sleep_backoff(backoff)
                backoff = min(self.settings.poll_max_seconds, backoff * 2)
                continue

            backoff = self.settings.poll_base_seconds
            self._execute_claim(claim)

        self.log.info("executor.shutdown.complete")
        if self.audit is not None:
            self.audit.close()

    def _execute_claim(self, claim: dict[str, Any]) -> None:
        job_id = claim.get("jobId")
        job_token = claim.get("jobToken")
        if not job_id or not job_token:
            self.log.warn("executor.claim.missing_tokens", claim=claim)
            return
        job = claim.get("job") or claim

        self.log.info(
            "executor.job.claimed",
            jobId=job_id,
            jobToken=redact_token(job_token),
            **safe_payload_summary(job.get("payload")),
        )
        if self.audit is not None:
            self.audit.emit("executor.job.claimed", payload={"jobId": job_id})

        try:
            self.validate_job(job)
        except Exception as exc:  # noqa: BLE001
            self._fail_job(job_id, job_token, f"job_validation_failed: {exc}")
            return

        try:
            self.client.post_status(job_id, job_token, "running")
        except ControlCenterError as exc:
            self.log.warn("executor.job.status.error", status=exc.status, payload=exc.payload)

        self._execute_with_heartbeat(job_id, job_token, job)

    def _execute_with_heartbeat(self, job_id: str, job_token: str, job: dict[str, Any]) -> None:
        stop_event = threading.Event()
        heartbeat = self._start_heartbeat(job_id, job_token, stop_event) if self.settings.heartbeat_seconds > 0 else None

        status = "completed"
        artifacts: list[dict[str, Any]] = []
        logs: Optional[str] = None
        reason: Optional[str] = None
        summary: Optional[str] = None
        result_payload: Optional[dict[str, Any]] = None

        try:
            result = self.run_action(job)
            status = result.get("status", "completed")
            artifacts = list(result.get("artifacts", []))
            logs = result.get("logs")
            reason = result.get("reason")
            summary = result.get("summary")
            result_payload = result.get("result")

            try:
                upload_refs = self.artifact_writer.write(job_id, result, logs)
                artifacts.extend(upload_refs)
            except ArtifactUploadError as exc:
                status = "failed"
                reason = f"artifact_upload:{exc}"
                if exc.partial_failures.get("uploaded_pointers"):
                    artifacts.extend(exc.partial_failures["uploaded_pointers"])
        except Exception as exc:  # noqa: BLE001 — fail-safe wrap
            status = "failed"
            reason = str(exc)
            summary = summary or f"Job {status}"
            self.log.error("executor.job.run.error", jobId=job_id, error=str(exc))
            if self.audit is not None:
                self.audit.emit("executor.job.failed", payload={"jobId": job_id, "error": str(exc)})
        finally:
            stop_event.set()
            if heartbeat is not None:
                heartbeat.join(timeout=1)

        try:
            summary = summary or f"Job {status}"
            self.log.info(
                "executor.job.output",
                jobId=job_id,
                status=status,
                reason=reason,
                summary=summary,
                artifactsCount=len(artifacts),
            )
            self.client.post_complete(
                job_id,
                job_token,
                status,
                artifacts=artifacts,
                logs=logs,
                reason=reason,
                summary=summary,
                result=result_payload,
            )
            if self.audit is not None and status == "completed":
                self.audit.emit("executor.job.completed", payload={"jobId": job_id})
        except ControlCenterError as exc:
            self.log.error("executor.job.complete.error", status=exc.status, payload=exc.payload)
        self.log.info("executor.job.done", jobId=job_id, status=status)

    # ── One-shot mode ─────────────────────────────────────────────

    def _run_oneshot(self, job_id: str, job_token: str) -> None:
        self.log.info("executor.oneshot.start", jobId=job_id, jobToken=redact_token(job_token))
        try:
            job_payload = self.client.fetch_job(job_id, job_token)
        except ControlCenterError as exc:
            self.log.error("executor.oneshot.fetch.error", status=exc.status, payload=exc.payload)
            sys.exit(1)

        try:
            self.client.post_status(job_id, job_token, "running")
        except ControlCenterError as exc:
            self.log.warn("executor.oneshot.status.error", status=exc.status, payload=exc.payload)

        self._execute_with_heartbeat(job_id, job_token, job_payload)
        sys.exit(0)

    # ── Helpers ───────────────────────────────────────────────────

    def _start_heartbeat(self, job_id: str, job_token: str, stop_event: threading.Event) -> threading.Thread:
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, job_token, stop_event),
            daemon=True,
            name=f"heartbeat-{job_id}",
        )
        thread.start()
        return thread

    def _heartbeat_loop(self, job_id: str, job_token: str, stop_event: threading.Event) -> None:
        interval = self.settings.heartbeat_seconds
        while not stop_event.wait(interval):
            try:
                self.client.post_heartbeat(job_id, job_token)
            except ControlCenterError as exc:
                self.log.warn("executor.heartbeat.error", status=exc.status, payload=exc.payload)
            except Exception as exc:  # noqa: BLE001
                self.log.warn("executor.heartbeat.unexpected", error=str(exc))

    def _fail_job(self, job_id: str, job_token: str, reason: str) -> None:
        self.log.warn("executor.job.fail", jobId=job_id, reason=reason)
        try:
            self.client.post_complete(job_id, job_token, "failed", reason=reason)
        except ControlCenterError as exc:
            self.log.error("executor.job.complete.error", status=exc.status, payload=exc.payload)
        if self.audit is not None:
            self.audit.emit("executor.job.failed", payload={"jobId": job_id, "reason": reason})

    @staticmethod
    def _sleep_backoff(current: float, jitter: float = 1.0) -> None:
        time.sleep(current + random.uniform(0, jitter))  # noqa: S311 — jitter, not crypto

    def _install_signal_handlers(self) -> None:
        def _handle(signum: int, _frame: Any) -> None:
            self.log.info("executor.shutdown.signal", signal=signum)
            self._shutdown.set()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    def _log_startup(self) -> None:
        self.log.info(
            "executor.starting",
            host=socket.gethostname(),
            pid=os.getpid(),
            python=sys.version.split()[0],
            executorId=self._executor_id,
            tenantId=self.settings.control_center_tenant_id,
            accountId=self.settings.control_center_account_id,
            capabilities=self.settings.capabilities_list,
            tokenPreview=redact_token(self.settings.control_center_executor_token),
        )
        if self.audit is None:
            self.log.warn("executor.audit.disabled", reason="no AuditEmitter configured")
        else:
            self.audit.emit("executor.starting", payload={"executorId": self._executor_id})


__all__ = ["BaseExecutor", "BaseExecutorError"]
