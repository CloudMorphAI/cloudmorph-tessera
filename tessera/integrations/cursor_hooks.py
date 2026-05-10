"""Cursor Hooks integration for Tessera.

Cursor v1.7 beta fires this script via beforeMCPExecution and afterMCPExecution hooks.
Hook returns a JSON object to stdout; Cursor reads it to decide allow/deny.

Known Cursor v1.7 beta bug: allow/ask paths are unreliable; deny is the only reliable
enforcement path. allow/ask use telemetry-only (audit written, hook returns allow).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TESSERA_URL = os.environ.get("TESSERA_URL", "http://localhost:8080")
TESSERA_TOKEN = os.environ.get("TESSERA_BEARER_TOKEN", "")
_TIMEOUT = 5.0  # seconds


def _auth_headers() -> dict[str, str]:
    if TESSERA_TOKEN:
        return {"Authorization": f"Bearer {TESSERA_TOKEN}"}
    return {}


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


def handle_before(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle beforeMCPExecution hook."""
    intent_resp = _post_intent(payload)

    if intent_resp is None:
        # Tessera unreachable: fail open with audit note
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
