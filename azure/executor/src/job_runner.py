from typing import Any, Dict, List

from storage_pointers import build_pointer


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def run(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    uris = _as_list(payload.get("artifactUri") or payload.get("artifactUris"))
    artifacts = [
        build_pointer("azure:blob", uri, {"source": "payload"})
        for uri in uris
        if uri
    ]
    return {
        "status": "completed",
        "artifacts": artifacts,
        "summary": "Completed.",
        "result": payload,
        "logs": None,
    }
