"""Build pointers to artifacts written to remote storage.

Extracted from 4 byte-identical copies (aws/azure/databricks/snowflake) and
the 19-LoC GCP variant which keeps its `build_gcs_pointer` extension in-place.
"""

from __future__ import annotations

from typing import Any


def build_pointer(kind: str, uri: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a generic artifact pointer.

    Args:
        kind: Logical artifact type (e.g., "summary", "result", "logs").
        uri: Resource locator (e.g., "s3://bucket/key", "gs://bucket/object").
        metadata: Optional free-form metadata; persisted with the pointer.

    Returns:
        A dict with keys: kind, uri, metadata.
    """
    return {
        "kind": kind,
        "uri": uri,
        "metadata": metadata or {},
    }


def build_s3_pointer(
    bucket: str,
    key: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an S3 pointer (s3://bucket/key)."""
    return build_pointer("s3", f"s3://{bucket}/{key.lstrip('/')}", metadata)


def build_gcs_pointer(
    bucket: str,
    object_path: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Google Cloud Storage pointer (gs://bucket/object)."""
    return build_pointer("gcs", f"gs://{bucket}/{object_path.lstrip('/')}", metadata)


def build_blob_pointer(
    account: str,
    container: str,
    blob_path: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an Azure Blob pointer (https://<account>.blob.core.windows.net/<container>/<blob>)."""
    uri = f"https://{account}.blob.core.windows.net/{container}/{blob_path.lstrip('/')}"
    return build_pointer("blob", uri, metadata)
