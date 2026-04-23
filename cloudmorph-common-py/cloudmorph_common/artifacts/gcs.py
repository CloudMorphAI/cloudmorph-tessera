"""Google Cloud Storage artifact writer."""

from __future__ import annotations

from typing import Any

from cloudmorph_common.artifacts.base import ArtifactWriter
from cloudmorph_common.errors import ArtifactUploadError
from cloudmorph_common.storage_pointers import build_gcs_pointer

try:
    from google.cloud import storage as gcs_storage  # type: ignore[import-not-found]
except ImportError as _imp_exc:  # pragma: no cover
    gcs_storage = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = _imp_exc
else:
    _IMPORT_ERROR = None


class GcsArtifactWriter(ArtifactWriter):
    """Write summary / result / logs to a GCS bucket."""

    def __init__(
        self,
        bucket: str,
        prefix: str | None = None,
        project: str | None = None,
        gcs_client: Any | None = None,
    ) -> None:
        if gcs_storage is None and gcs_client is None:
            raise ArtifactUploadError(
                "google-cloud-storage not installed; pip install cloudmorph-common[gcp]"
            )
        if not bucket:
            raise ValueError("GcsArtifactWriter: bucket is required")
        self.bucket_name = bucket
        self.prefix = (prefix or "").strip("/")
        self.project = project
        self._client = gcs_client

    def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        if gcs_storage is None:
            raise ArtifactUploadError("google-cloud-storage not installed") from _IMPORT_ERROR
        self._client = gcs_storage.Client(project=self.project) if self.project else gcs_storage.Client()
        return self._client

    def write(
        self,
        job_id: str,
        result: dict[str, Any],
        logs: str | None = None,
    ) -> list[dict[str, Any]]:
        base = self.prefix or f"controlcentre/jobs/{job_id}"
        client = self._client_lazy()
        bucket = client.bucket(self.bucket_name)
        keys = {
            "summary": f"{base}/summary.txt",
            "result": f"{base}/result.json",
            "logs": f"{base}/logs.jsonl",
        }
        pointers: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        try:
            bucket.blob(keys["summary"]).upload_from_string(
                self._serialize_summary(result), content_type="text/plain"
            )
            pointers.append(build_gcs_pointer(self.bucket_name, keys["summary"], {"role": "summary"}))
        except Exception as exc:  # noqa: BLE001
            errors["summary"] = str(exc)

        try:
            bucket.blob(keys["result"]).upload_from_string(
                self._serialize_result(result), content_type="application/json"
            )
            pointers.append(build_gcs_pointer(self.bucket_name, keys["result"], {"role": "result"}))
        except Exception as exc:  # noqa: BLE001
            errors["result"] = str(exc)

        log_bytes = self._serialize_logs(logs)
        if log_bytes is not None:
            try:
                bucket.blob(keys["logs"]).upload_from_string(log_bytes, content_type="application/json")
                pointers.append(build_gcs_pointer(self.bucket_name, keys["logs"], {"role": "logs"}))
            except Exception as exc:  # noqa: BLE001
                errors["logs"] = str(exc)

        if errors:
            raise ArtifactUploadError(
                f"GCS artifact upload failed: {', '.join(errors.keys())}",
                partial_failures={**errors, "uploaded_pointers": pointers},
            )
        return pointers
