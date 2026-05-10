"""Unit tests for tessera/integrations/cursor_hooks.py."""

from __future__ import annotations

from unittest.mock import patch

from tessera.integrations.cursor_hooks import (
    handle_after,
    handle_before,
    handle_deny,
)

_BEFORE_PAYLOAD = {
    "type": "beforeMCPExecution",
    "tool_name": "aws.s3.delete_bucket",
    "tool_input": {"bucket": "my-bucket"},
    "command": "delete bucket",
    "conversation_id": "conv_123",
    "generation_id": "gen_456",
    "workspace_roots": ["/workspace"],
}

_AFTER_PAYLOAD = {
    "type": "afterMCPExecution",
    "tool_name": "aws.s3.delete_bucket",
    "result": {"content": [{"type": "text", "text": "deleted"}]},
}

_DENY_PAYLOAD = {
    "type": "deny",
    "policy_reason": "prod-protection: write.delete blocked",
}


class MockResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "_meta": {
                "tessera_intent": {
                    "verbs": ["write.delete"],
                    "purpose": "delete bucket; bucket=my-bucket",
                }
            }
        }


def test_before_happy_path() -> None:
    with patch("tessera.integrations.cursor_hooks.httpx.post", return_value=MockResponse()):
        result = handle_before(_BEFORE_PAYLOAD)
    assert result["action"] == "allow"
    assert "_meta" in result
    assert "tessera_intent" in result["_meta"]


def test_before_tessera_unreachable_fails_open() -> None:
    import httpx as _httpx

    with patch("tessera.integrations.cursor_hooks.httpx.post", side_effect=_httpx.ConnectError("refused")):
        result = handle_before(_BEFORE_PAYLOAD)
    assert result["action"] == "allow"


def test_after_returns_allow() -> None:
    result = handle_after(_AFTER_PAYLOAD)
    assert result["action"] == "allow"


def test_deny_returns_deny_shape() -> None:
    result = handle_deny(_DENY_PAYLOAD)
    assert result["action"] == "deny"
    assert "prod-protection" in result["message"]


def test_deny_default_reason() -> None:
    result = handle_deny({})
    assert result["action"] == "deny"
    assert isinstance(result["message"], str)
    assert len(result["message"]) > 0
