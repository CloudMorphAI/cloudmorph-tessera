"""Cursor Hooks integration for Tessera.

Cursor v1.7 beta fires this script via beforeMCPExecution and afterMCPExecution hooks.
Hook returns a JSON object to stdout; Cursor reads it to decide allow/deny.

Known Cursor v1.7 beta bug: allow/ask paths are unreliable; deny is the only reliable
enforcement path. allow/ask use telemetry-only (audit written, hook returns allow).

Multi-token support (v0.2.0): the hook resolves a bearer token using the same
3-source precedence as tessera/auth/bearer.py:build_token_list(). The optional
TESSERA_CURSOR_TOKEN_NAME env var selects a specific named token from the list.

Fail-closed mode (v0.2.0): when TESSERA_CURSOR_FAIL_CLOSED=true, unreachable
Tessera denies the MCP call instead of failing open.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

TESSERA_URL = os.environ.get("TESSERA_URL", "http://localhost:8080")
_TIMEOUT = 5.0  # seconds


# ---------------------------------------------------------------------------
# Multi-token resolution
# ---------------------------------------------------------------------------


def _resolve_bearer_token() -> str:
    """Resolve the bearer token to use for this hook invocation.

    Precedence mirrors tessera/auth/bearer.py:build_token_list():
    1. TESSERA_BEARER_TOKENS  — parse name:token,... inline list
    2. TESSERA_BEARER_TOKENS_FILE — parse YAML file
    3. TESSERA_BEARER_TOKEN  — single legacy token

    When TESSERA_CURSOR_TOKEN_NAME is set, that named token is selected from
    the list (sources 1 and 2).  If the named token is not found, falls back to
    the first token in the list.
    """
    token_name = os.environ.get("TESSERA_CURSOR_TOKEN_NAME", "")

    inline = os.environ.get("TESSERA_BEARER_TOKENS")
    if inline is not None:
        tokens = _parse_inline_tokens(inline)
        return _pick_token(tokens, token_name)

    tokens_file = os.environ.get("TESSERA_BEARER_TOKENS_FILE")
    if tokens_file is not None:
        tokens = _parse_token_file(tokens_file)
        return _pick_token(tokens, token_name)

    return os.environ.get("TESSERA_BEARER_TOKEN", "")


def _parse_inline_tokens(raw: str) -> dict[str, str]:
    """Parse 'name1:tk_xxx,name2:tk_yyy' → {name: token}."""
    result: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, token = entry.split(":", 1)
        result[name.strip()] = token.strip()
    return result


def _parse_token_file(path_str: str) -> dict[str, str]:
    """Parse YAML tokens file → {name: token}."""
    try:
        with open(path_str, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.error("tessera cursor_hooks: cannot read tokens file %s: %s", path_str, exc)
        return {}

    tokens_list = data.get("tokens", [])
    if not isinstance(tokens_list, list):
        return {}

    result: dict[str, str] = {}
    for entry in tokens_list:
        if isinstance(entry, dict) and "name" in entry and "token" in entry:
            result[str(entry["name"])] = str(entry["token"])
    return result


def _pick_token(tokens: dict[str, str], name: str) -> str:
    """Return the named token if found, otherwise the first token value."""
    if name and name in tokens:
        return tokens[name]
    # Fall back to first entry (deterministic by dict insertion order, Python 3.7+)
    return next(iter(tokens.values()), "")


def _auth_headers() -> dict[str, str]:
    token = _resolve_bearer_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# ---------------------------------------------------------------------------
# Fail-closed mode
# ---------------------------------------------------------------------------


def _is_fail_closed() -> bool:
    return os.environ.get("TESSERA_CURSOR_FAIL_CLOSED", "false").lower() == "true"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post_intent(payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to /intent endpoint. Returns response body or None on error."""
    try:
        resp = httpx.post(
            f"{TESSERA_URL}/intent",
            json={
                "tool_name": payload.get("tool_name", ""),
                "tool_input": payload.get("tool_input", {}),
                "command": payload.get("command", ""),
                "conversation_id": payload.get("conversation_id", ""),
                "generation_id": payload.get("generation_id", ""),
                "workspace_roots": payload.get("workspace_roots", []),
            },
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()  # type: ignore[no-any-return]
        logger.warning("tessera /intent returned %d", resp.status_code)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("tessera /intent unreachable: %s", exc)
        return None


def _cursor_deny(reason: str) -> dict[str, Any]:
    """Cursor deny response shape."""
    return {"action": "deny", "message": reason}


def _cursor_allow(tessera_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cursor allow response shape, optionally with tessera envelope."""
    resp: dict[str, Any] = {"action": "allow"}
    if tessera_meta:
        resp["_meta"] = tessera_meta
    return resp


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------


def handle_before(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle beforeMCPExecution hook."""
    intent_resp = _post_intent(payload)

    if intent_resp is None:
        if _is_fail_closed():
            logger.error("tessera unreachable in beforeMCPExecution — fail_closed mode active, denying")
            return _cursor_deny("Tessera proxy unreachable; fail_closed mode active.")
        # Fail open (default behaviour)
        logger.error("tessera unreachable in beforeMCPExecution — failing open")
        return _cursor_allow()

    meta = intent_resp.get("_meta", {})
    tessera_intent = meta.get("tessera_intent", {})

    # Cursor v1.7 beta: deny is the only reliable path.
    # allow/ask are telemetry-only (bug: cursor does not act on them).
    # Decision is made by tessera based on the verbs; for now we pass the envelope
    # through and let the proxy enforce on the actual MCP request.
    _ = tessera_intent  # consumed by proxy; kept here for future deny escalation
    return _cursor_allow(tessera_meta=meta)


def handle_after(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle afterMCPExecution hook — telemetry only."""
    # Audit is handled by the proxy when the MCP request passes through.
    # After-hook is a belt-and-suspenders telemetry path.
    _ = payload
    return _cursor_allow()


def handle_deny(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle an explicit deny signal from Tessera (for future use)."""
    reason = payload.get("policy_reason", "Blocked by Tessera policy")
    return _cursor_deny(reason)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({"action": "allow", "error": f"parse error: {exc}"}))
        sys.stdout.flush()
        return

    hook_type = payload.get("type", "")

    if hook_type == "beforeMCPExecution":
        result = handle_before(payload)
    elif hook_type == "afterMCPExecution":
        result = handle_after(payload)
    elif hook_type == "deny":
        result = handle_deny(payload)
    else:
        # Unknown hook type — fail open
        result = _cursor_allow()

    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
