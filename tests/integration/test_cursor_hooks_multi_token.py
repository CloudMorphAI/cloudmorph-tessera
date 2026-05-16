"""Integration tests for multi-token Cursor hook propagation (A-4-4) and
fail_closed mode (A-4-5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _before_payload(tool_name: str = "aws_s3_list_buckets") -> dict[str, Any]:
    return {
        "type": "beforeMCPExecution",
        "tool_name": tool_name,
        "tool_input": {},
        "command": "list buckets",
        "conversation_id": "conv-123",
        "generation_id": "gen-456",
        "workspace_roots": [],
    }


# ---------------------------------------------------------------------------
# A-4-4: _resolve_bearer_token()
# ---------------------------------------------------------------------------


def test_resolve_bearer_token_inline() -> None:
    """TESSERA_BEARER_TOKENS inline list is parsed and first token returned."""
    from tessera.integrations.cursor_hooks import _resolve_bearer_token

    with patch.dict(
        "os.environ",
        {"TESSERA_BEARER_TOKENS": "alice:tk_aaaaaaaaaaaaaaaa,bob:tk_bbbbbbbbbbbbbbbb"},
        clear=False,
    ):
        token = _resolve_bearer_token()
        # Default: no TESSERA_CURSOR_TOKEN_NAME → first token
        assert token == "tk_aaaaaaaaaaaaaaaa"


def test_resolve_bearer_token_inline_named() -> None:
    """TESSERA_CURSOR_TOKEN_NAME selects a specific token from the inline list."""
    from tessera.integrations.cursor_hooks import _resolve_bearer_token

    with patch.dict(
        "os.environ",
        {
            "TESSERA_BEARER_TOKENS": "alice:tk_aaaaaaaaaaaaaaaa,bob:tk_bbbbbbbbbbbbbbbb",
            "TESSERA_CURSOR_TOKEN_NAME": "bob",
        },
        clear=False,
    ):
        token = _resolve_bearer_token()
        assert token == "tk_bbbbbbbbbbbbbbbb"


def test_resolve_bearer_token_file(tmp_path: Any) -> None:
    """TESSERA_BEARER_TOKENS_FILE: selected token is returned."""
    tokens_yaml = tmp_path / "tokens.yaml"
    tokens_yaml.write_text(
        "tokens:\n"
        "  - name: ci\n"
        "    token: tk_cccccccccccccccc\n"
        "  - name: prod\n"
        "    token: tk_dddddddddddddddd\n",
        encoding="utf-8",
    )

    from tessera.integrations.cursor_hooks import _resolve_bearer_token

    with patch.dict(
        "os.environ",
        {
            "TESSERA_BEARER_TOKENS_FILE": str(tokens_yaml),
            "TESSERA_CURSOR_TOKEN_NAME": "prod",
        },
        clear=False,
    ):
        # Remove any inline override
        import os
        os.environ.pop("TESSERA_BEARER_TOKENS", None)
        token = _resolve_bearer_token()
        assert token == "tk_dddddddddddddddd"


def test_resolve_bearer_token_legacy_fallback() -> None:
    """TESSERA_BEARER_TOKEN single-token path still works."""
    import os

    from tessera.integrations.cursor_hooks import _resolve_bearer_token

    env_patch = {
        "TESSERA_BEARER_TOKEN": "tk_eeeeeeeeeeeeeeee",
    }
    # Remove multi-token env vars if present
    with patch.dict("os.environ", env_patch, clear=False):
        os.environ.pop("TESSERA_BEARER_TOKENS", None)
        os.environ.pop("TESSERA_BEARER_TOKENS_FILE", None)
        token = _resolve_bearer_token()
        assert token == "tk_eeeeeeeeeeeeeeee"


# ---------------------------------------------------------------------------
# A-4-5: fail_closed mode
# ---------------------------------------------------------------------------


def test_fail_closed_true_denies_on_unreachable() -> None:
    """When fail_closed=true and Tessera unreachable, handle_before returns deny."""
    from tessera.integrations.cursor_hooks import handle_before

    with (
        patch.dict("os.environ", {"TESSERA_CURSOR_FAIL_CLOSED": "true"}, clear=False),
        patch("tessera.integrations.cursor_hooks._post_intent", return_value=None),
    ):
        result = handle_before(_before_payload())

    assert result["action"] == "deny"
    assert "fail_closed" in result["message"].lower()


def test_fail_open_default_allows_on_unreachable() -> None:
    """When fail_closed is not set, handle_before fails open on unreachable Tessera."""
    import os

    from tessera.integrations.cursor_hooks import handle_before

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("TESSERA_CURSOR_FAIL_CLOSED", None)
        with patch("tessera.integrations.cursor_hooks._post_intent", return_value=None):
            result = handle_before(_before_payload())

    assert result["action"] == "allow"
