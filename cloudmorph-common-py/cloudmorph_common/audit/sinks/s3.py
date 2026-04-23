"""S3 audit sink. Writes per-event objects under a tenant-prefixed key.

For higher throughput, a future variant batches events into Parquet files
flushed every N seconds.

Object key shape:
  s3://<bucket>/<prefix>/<tenantId>/<YYYY>/<MM>/<DD>/<eventId>.json

Object body: the event JSON (already canonicalized and stamped by AuditEmitter).
ContentType: application/json. Optional ObjectLock for WORM compliance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cloudmorph_common.errors import AuditSinkError


class S3Sink:
    """Write each AuditEvent as one S3 object.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix (default "audit"). Per-tenant subdirectory appended.
        region: AWS region for the boto3 client. Required when not running on AWS.
        boto3_client: Optional pre-built boto3 S3 client (for tests/DI).
        object_lock_mode: None (off) | "GOVERNANCE" | "COMPLIANCE". When set,
            requires the bucket to have Object Lock enabled.
        retain_until_days: Object-Lock retention period in days. Required when
            object_lock_mode is set.
    """

    name: str = "s3"

    def __init__(
        self,
        bucket: str,
        prefix: str = "audit",
        region: str | None = None,
        boto3_client: Any | None = None,
        object_lock_mode: str | None = None,
        retain_until_days: int | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3Sink: bucket is required")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self.object_lock_mode = object_lock_mode
        self.retain_until_days = retain_until_days
        self._client = boto3_client
        if object_lock_mode and object_lock_mode not in {"GOVERNANCE", "COMPLIANCE"}:
            raise ValueError(f"S3Sink: invalid object_lock_mode {object_lock_mode!r}")
        if object_lock_mode and not retain_until_days:
            raise ValueError("S3Sink: retain_until_days required when object_lock_mode is set")

    def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AuditSinkError("s3", "boto3 not installed; pip install cloudmorph-common[aws]") from exc
        self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def emit(self, event: dict[str, Any]) -> None:
        tenant = event.get("tenantId", "unknown")
        event_id = event.get("eventId", "unknown")
        occurred = event.get("occurredAt") or datetime.now(timezone.utc).isoformat()
        try:
            ts = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        key = f"{self.prefix}/{tenant}/{ts:%Y}/{ts:%m}/{ts:%d}/{event_id}.json"
        body = json.dumps(event, ensure_ascii=False, default=str).encode("utf-8")
        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/json",
        }
        if self.object_lock_mode and self.retain_until_days:
            put_kwargs["ObjectLockMode"] = self.object_lock_mode
            put_kwargs["ObjectLockRetainUntilDate"] = ts.replace(tzinfo=timezone.utc).timestamp() + (
                self.retain_until_days * 86400
            )

        try:
            self._client_lazy().put_object(**put_kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raise as AuditSinkError for emitter to handle
            raise AuditSinkError("s3", f"put_object failed: {exc}") from exc

    def close(self) -> None:
        # boto3 clients don't need explicit close
        pass
