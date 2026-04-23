"""Token redaction + safe-payload-key allowlists for log emission.

Don't log the full payload — log only known-safe keys (allowlist), with
sensitive values masked. Redaction is best-effort defense in depth; the
real protection is never accepting raw secrets in env (use SecretResolver).
"""

from __future__ import annotations

from typing import Any

# Keys whose values are safe to log (no PII, no secrets, no costly to debug).
# Per-cloud executors may extend this list.
DEFAULT_SAFE_PAYLOAD_KEYS: tuple[str, ...] = (
    "bucket",
    "bucketName",
    "container",
    "containerName",
    "prefix",
    "keyPrefix",
    "region",
    "awsRegion",
    "azureRegion",
    "gcpRegion",
    "location",
    "maxKeys",
    "max_keys",
    "maxResults",
    "max_results",
    "limit",
    "pageSize",
    "accountId",
    "subscriptionId",
    "projectId",
    "workspaceUrl",
    "host",
    "warehouse",
    "database",
    "schema",
    "catalog",
    "clusterId",
    "instanceId",
    "vmId",
)


def redact_token(value: str | None, prefix_chars: int = 4, suffix_chars: int = 4) -> str | None:
    """Mask a token-like value: keep prefix + suffix, mask middle.

    Returns None if input is None. Returns "***" for short values.
    """
    if value is None:
        return None
    if len(value) <= (prefix_chars + suffix_chars):
        return "***"
    return f"{value[:prefix_chars]}...{value[-suffix_chars:]}"


def safe_payload_summary(
    payload: dict[str, Any] | None,
    safe_keys: tuple[str, ...] = DEFAULT_SAFE_PAYLOAD_KEYS,
    max_key_count: int = 20,
) -> dict[str, Any]:
    """Return a redacted summary of a payload safe to log.

    Includes:
    - count of keys
    - first N keys (for debug visibility into shape)
    - values for keys in `safe_keys` allowlist (truncated to scalar primitives)

    Excludes everything else.
    """
    if not isinstance(payload, dict) or not payload:
        return {"keyCount": 0}

    keys = [str(k) for k in payload.keys()]
    summary: dict[str, Any] = {
        "keyCount": len(keys),
        "keys": keys[:max_key_count],
    }
    safe_values: dict[str, Any] = {}
    for key in safe_keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            safe_values[key] = value
    if safe_values:
        summary["values"] = safe_values
    return summary
