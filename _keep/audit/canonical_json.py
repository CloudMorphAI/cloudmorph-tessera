"""Canonical JSON serialization (RFC 8785 JCS).

Produces a byte-stable JSON representation across Python and TypeScript
implementations. Used as the input to the audit hash chain so that
auditors using either language compute identical event hashes.

Key invariants:
- UTF-8 encoded
- No whitespace
- Object keys sorted lexicographically (UTF-16 code units per spec)
- Numbers: integers as integers; floats round-trip per JSON.stringify semantics
- No NaN, no +/-Infinity (spec rejects these — we raise ValueError)

Reference: https://www.rfc-editor.org/rfc/rfc8785
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["canonical_json", "canonical_json_str"]


def _normalize(value: Any) -> Any:
    """Recursively normalize a value into something json.dumps can handle deterministically."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("canonical_json: NaN/Infinity not permitted by RFC 8785")
        # Per JCS §3.2.2: integers must serialize as integers even if they came in as floats.
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        # Sort keys lexicographically. Reject non-string keys (would be ambiguous).
        normalized: dict[str, Any] = {}
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise ValueError(f"canonical_json: non-string key not permitted: {key!r}")
            normalized[key] = _normalize(value[key])
        return normalized
    if hasattr(value, "model_dump"):  # pydantic v2 model
        return _normalize(value.model_dump())
    raise TypeError(f"canonical_json: unsupported type {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """Return RFC 8785 canonical JSON encoding of value as UTF-8 bytes.

    Raises:
        ValueError: on NaN/Infinity or non-string dict keys.
        TypeError: on unsupported types.
    """
    normalized = _normalize(value)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def canonical_json_str(value: Any) -> str:
    """Same as canonical_json but returns a str (for use cases that need it)."""
    return canonical_json(value).decode("utf-8")
