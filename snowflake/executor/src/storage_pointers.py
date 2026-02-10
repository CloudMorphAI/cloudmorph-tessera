from typing import Any, Dict, Optional


def build_pointer(kind: str, uri: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "kind": kind,
        "uri": uri,
        "metadata": metadata or {},
    }
