"""S3 artifact writer. Used by aws/executor (and any cloud where customer chose S3 as storage)."""

from __future__ import annotations

from typing import Any

from cloudmorph_common.artifacts.base import ArtifactWriter
from cloudmorph_common.errors import ArtifactUploadError
from cloudmorph_common.storage_pointers import build_s3_pointer


class S3ArtifactWriter(ArtifactWriter):
    """Write summary.txt / result.json / logs.jsonl to S3.

    Args:
        bucket: S3 bucket name.
        region: AWS region for the boto3 client.
        prefix: Key prefix; default `controlcentre/jobs/<jobId>` per existing convention.
        boto3_client: Optional pre-built client (for tests / DI).
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        prefix: str | None = None,
        boto3_client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3ArtifactWriter: bucket is required")
        if not region:
            raise ValueError("S3ArtifactWriter: region is required")
        self.bucket = bucket
        self.region = region
        self.prefix = (prefix or "").strip("/")
        self._client = boto3_client

    def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ArtifactUploadError("boto3 not installed; pip install cloudmorph-common[aws]") from exc
        self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def write(
        self,
        job_id: str,
        result: dict[str, Any],
        logs: str | None = None,
    ) -> list[dict[str, Any]]:
        base = self.prefix or f"controlcentre/jobs/{job_id}"
        client = self._client_lazy()
        keys = {
            "summary": f"{base}/summary.txt",
            "result": f"{base}/result.json",
            "logs": f"{base}/logs.jsonl",
        }
        pointers: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        try:
            client.put_object(
                Bucket=self.bucket,
                Key=keys["summary"],
                Body=self._serialize_summary(result),
                ContentType="text/plain",
            )
            pointers.append(build_s3_pointer(self.bucket, keys["summary"], {"role": "summary"}))
        except Exception as exc:  # noqa: BLE001
            errors["summary"] = str(exc)

        try:
            client.put_object(
                Bucket=self.bucket,
                Key=keys["result"],
                Body=self._serialize_result(result),
                ContentType="application/json",
            )
            pointers.append(build_s3_pointer(self.bucket, keys["result"], {"role": "result"}))
        except Exception as exc:  # noqa: BLE001
            errors["result"] = str(exc)

        log_bytes = self._serialize_logs(logs)
        if log_bytes is not None:
            try:
                client.put_object(
                    Bucket=self.bucket,
                    Key=keys["logs"],
                    Body=log_bytes,
                    ContentType="application/json",
                )
                pointers.append(build_s3_pointer(self.bucket, keys["logs"], {"role": "logs"}))
            except Exception as exc:  # noqa: BLE001
                errors["logs"] = str(exc)

        if errors:
            raise ArtifactUploadError(
                f"S3 artifact upload failed: {', '.join(errors.keys())}",
                partial_failures={**errors, "uploaded_pointers": pointers},
            )
        return pointers
