"""Azure Blob artifact writer."""

from __future__ import annotations

from typing import Any

from cloudmorph_common.artifacts.base import ArtifactWriter
from cloudmorph_common.errors import ArtifactUploadError
from cloudmorph_common.storage_pointers import build_blob_pointer

try:
    from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
except ImportError as _imp_exc:  # pragma: no cover
    BlobServiceClient = None  # type: ignore[assignment, misc]
    _IMPORT_ERROR: ImportError | None = _imp_exc
else:
    _IMPORT_ERROR = None


class BlobArtifactWriter(ArtifactWriter):
    """Write summary / result / logs to an Azure Blob container.

    Auth options (in precedence order):
        1. Connection string via `connection_string=` arg or env
        2. Account key via `account_key=` arg
        3. SAS token via `sas_token=` arg
        4. Default credential chain (managed identity, az login, etc.)
    """

    def __init__(
        self,
        account: str,
        container: str,
        prefix: str | None = None,
        connection_string: str | None = None,
        account_key: str | None = None,
        sas_token: str | None = None,
        blob_service_client: Any | None = None,
    ) -> None:
        if BlobServiceClient is None and blob_service_client is None:
            raise ArtifactUploadError(
                "azure-storage-blob not installed; pip install cloudmorph-common[azure]"
            )
        if not account:
            raise ValueError("BlobArtifactWriter: account is required")
        if not container:
            raise ValueError("BlobArtifactWriter: container is required")
        self.account = account
        self.container = container
        self.prefix = (prefix or "").strip("/")
        self._connection_string = connection_string
        self._account_key = account_key
        self._sas_token = sas_token
        self._client = blob_service_client

    def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        if BlobServiceClient is None:  # pragma: no cover
            raise ArtifactUploadError("azure-storage-blob not installed") from _IMPORT_ERROR
        if self._connection_string:
            self._client = BlobServiceClient.from_connection_string(self._connection_string)
        elif self._sas_token:
            url = f"https://{self.account}.blob.core.windows.net{self._sas_token if self._sas_token.startswith('?') else '?' + self._sas_token}"
            self._client = BlobServiceClient(account_url=url)
        elif self._account_key:
            url = f"https://{self.account}.blob.core.windows.net"
            self._client = BlobServiceClient(account_url=url, credential=self._account_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ArtifactUploadError("azure-identity required for default credential auth") from exc
            url = f"https://{self.account}.blob.core.windows.net"
            self._client = BlobServiceClient(account_url=url, credential=DefaultAzureCredential())
        return self._client

    def write(
        self,
        job_id: str,
        result: dict[str, Any],
        logs: str | None = None,
    ) -> list[dict[str, Any]]:
        base = self.prefix or f"controlcentre/jobs/{job_id}"
        client = self._client_lazy()
        container_client = client.get_container_client(self.container)
        keys = {
            "summary": f"{base}/summary.txt",
            "result": f"{base}/result.json",
            "logs": f"{base}/logs.jsonl",
        }
        pointers: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        for kind, body, content_type in (
            ("summary", self._serialize_summary(result), "text/plain"),
            ("result", self._serialize_result(result), "application/json"),
        ):
            try:
                container_client.upload_blob(name=keys[kind], data=body, overwrite=True)
                pointers.append(
                    build_blob_pointer(self.account, self.container, keys[kind], {"role": kind, "contentType": content_type})
                )
            except Exception as exc:  # noqa: BLE001
                errors[kind] = str(exc)

        log_bytes = self._serialize_logs(logs)
        if log_bytes is not None:
            try:
                container_client.upload_blob(name=keys["logs"], data=log_bytes, overwrite=True)
                pointers.append(
                    build_blob_pointer(self.account, self.container, keys["logs"], {"role": "logs"})
                )
            except Exception as exc:  # noqa: BLE001
                errors["logs"] = str(exc)

        if errors:
            raise ArtifactUploadError(
                f"Blob artifact upload failed: {', '.join(errors.keys())}",
                partial_failures={**errors, "uploaded_pointers": pointers},
            )
        return pointers
