"""Integration test: Cursor Hooks flow — three in-process scenarios + one subprocess smoke test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tessera.integrations.cursor_hooks import handle_after, handle_before

# ── Payloads ──────────────────────────────────────────────────────────────────

_BEFORE_ALLOW_PAYLOAD: dict[str, Any] = {
    "type": "beforeMCPExecution",
    "tool_name": "aws.s3.list_buckets",
    "tool_input": {},
    "command": "list buckets",
    "conversation_id": "conv_test",
    "generation_id": "gen_test",
    "workspace_roots": [],
}

_BEFORE_DELETE_PAYLOAD: dict[str, Any] = {
    "type": "beforeMCPExecution",
    "tool_name": "aws.s3.delete_bucket",
    "tool_input": {"bucket": "prod-data"},
    "command": "delete bucket",
    "conversation_id": "conv_test",
    "generation_id": "gen_test",
    "workspace_roots": [],
}

_AFTER_PAYLOAD: dict[str, Any] = {
    "type": "afterMCPExecution",
    "tool_name": "aws.s3.list_buckets",
    "result": {"content": [{"type": "text", "text": "ok"}]},
}

_INTENT_ALLOW_RESP: dict[str, Any] = {
    "_meta": {
        "tessera_intent": {
            "verbs": ["read.list"],
            "purpose": "list buckets",
        }
    }
}

_INTENT_DELETE_RESP: dict[str, Any] = {
    "_meta": {
        "tessera_intent": {
            "verbs": ["write.delete"],
            "purpose": "delete bucket; bucket=prod-data",
        }
    }
}

HOOK_SCRIPT = Path(__file__).parent.parent.parent / "tessera" / "integrations" / "cursor_hooks.py"

# ── In-process tests (respx mocks httpx calls inside the current process) ─────


@pytest.mark.integration
@respx.mock
def test_before_allow_passes_through() -> None:
    """beforeMCPExecution for a read tool returns action:allow with tessera _meta."""
    respx.post("http://localhost:8080/intent").mock(
        return_value=httpx.Response(200, json=_INTENT_ALLOW_RESP)
    )

    result = handle_before(_BEFORE_ALLOW_PAYLOAD)

    assert result["action"] == "allow"
    assert "_meta" in result
    assert result["_meta"].get("tessera_intent", {}).get("verbs") == ["read.list"]


@pytest.mark.integration
@respx.mock
def test_before_delete_passes_through() -> None:
    """beforeMCPExecution for a delete tool still returns action:allow.

    Enforcement happens at the proxy layer when the real MCP request arrives.
    The Cursor v1.7 beta hook is telemetry + intent annotation only.
    """
    respx.post("http://localhost:8080/intent").mock(
        return_value=httpx.Response(200, json=_INTENT_DELETE_RESP)
    )

    result = handle_before(_BEFORE_DELETE_PAYLOAD)

    assert result["action"] == "allow"
    assert "_meta" in result
    assert "write.delete" in result["_meta"].get("tessera_intent", {}).get("verbs", [])


@pytest.mark.integration
def test_after_hook_telemetry_only() -> None:
    """afterMCPExecution always returns action:allow without calling Tessera."""
    # No respx mock — confirms no outbound httpx call is made for after-hooks.
    result = handle_after(_AFTER_PAYLOAD)

    assert result["action"] == "allow"
    assert "_meta" not in result


# ── Subprocess smoke test (no httpx mocking — relies on connection refusal) ───


@pytest.mark.integration
def test_hook_subprocess_unreachable_fails_open() -> None:
    """Hook fails open when Tessera is unreachable (subprocess, no server on port 19999)."""
    import os

    env = dict(os.environ)
    env["TESSERA_URL"] = "http://localhost:19999"
    env.pop("TESSERA_BEARER_TOKEN", None)

    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(_BEFORE_ALLOW_PAYLOAD),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode == 0, f"hook script exited non-zero: {result.stderr}"
    parsed = json.loads(result.stdout)
    assert parsed["action"] == "allow"
