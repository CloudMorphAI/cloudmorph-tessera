from typing import Any, Dict, Optional


def build_pointer(kind: str, uri: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "kind": kind,
        "uri": uri,
        "metadata": metadata or {},
    }


def build_gcs_pointer(
    bucket: str,
    object_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    object_path = object_path.lstrip("/")
    uri = f"gs://{bucket}/{object_path}"
    return build_pointer("gcs", uri, metadata)
