"""Unit tests for tessera.auth.bearer."""

from __future__ import annotations

import timeit
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tessera.auth.bearer import BearerTokenAuthenticator, Token, build_token_list
from tessera.errors import ConfigError, UnauthorizedError

# ---------------------------------------------------------------------------
# build_token_list — inline format
# ---------------------------------------------------------------------------


def test_build_token_list_inline_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_BEARER_TOKENS", "alice:tk_abc123_xxxxxxxxxx")
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    result = build_token_list()
    assert result == [Token("alice", "tk_abc123_xxxxxxxxxx", "alice")]


def test_build_token_list_inline_scope_defaults_to_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_BEARER_TOKENS", "bob:tk_bobtoken_longerthan16")
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    result = build_token_list()
    assert len(result) == 1
    assert result[0].name == "bob"
    assert result[0].scope == "bob"


def test_build_token_list_inline_malformed_no_colon_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_BEARER_TOKENS", "no-colon-here")
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="no ':' separator"):
        build_token_list()


def test_build_token_list_inline_token_too_short_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_BEARER_TOKENS", "alice:short")
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="at least 16 characters"):
        build_token_list()


# ---------------------------------------------------------------------------
# build_token_list — file format
# ---------------------------------------------------------------------------


def test_build_token_list_file_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "tokens.yaml"
    token_file.write_text(
        "tokens:\n  - name: alice\n    token: tk_alice_longerthan16chars\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TESSERA_BEARER_TOKENS", raising=False)
    monkeypatch.setenv("TESSERA_BEARER_TOKENS_FILE", str(token_file))
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    result = build_token_list()
    assert len(result) == 1
    assert result[0].name == "alice"
    assert result[0].token == "tk_alice_longerthan16chars"
    assert result[0].scope == "alice"


def test_build_token_list_file_custom_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "tokens.yaml"
    token_file.write_text(
        "tokens:\n  - name: alice\n    token: tk_alice_longerthan16chars\n    scope: team-alpha\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TESSERA_BEARER_TOKENS", raising=False)
    monkeypatch.setenv("TESSERA_BEARER_TOKENS_FILE", str(token_file))
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    result = build_token_list()
    assert result[0].scope == "team-alpha"


def test_build_token_list_file_duplicate_names_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "tokens.yaml"
    token_file.write_text(
        "tokens:\n"
        "  - name: alice\n"
        "    token: tk_alice_longerthan16chars\n"
        "  - name: alice\n"
        "    token: tk_alice2_longerthan16chars\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TESSERA_BEARER_TOKENS", raising=False)
    monkeypatch.setenv("TESSERA_BEARER_TOKENS_FILE", str(token_file))
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="duplicate token name"):
        build_token_list()


# ---------------------------------------------------------------------------
# build_token_list — legacy single token
# ---------------------------------------------------------------------------


def test_build_token_list_legacy_single(monkeypatch: pytest.MonkeyPatch) -> None:
    tok = "tk_legacy_token_longerthan16"
    monkeypatch.delenv("TESSERA_BEARER_TOKENS", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.setenv("TESSERA_BEARER_TOKEN", tok)
    result = build_token_list()
    assert result == [Token("default", tok, "default")]


# ---------------------------------------------------------------------------
# build_token_list — dev mode
# ---------------------------------------------------------------------------


def test_build_token_list_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERA_BEARER_TOKENS", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.delenv("TESSERA_BEARER_TOKEN", raising=False)
    result = build_token_list()
    assert result == []


# ---------------------------------------------------------------------------
# build_token_list — precedence
# ---------------------------------------------------------------------------


def test_build_token_list_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """TESSERA_BEARER_TOKENS wins over TESSERA_BEARER_TOKEN."""
    monkeypatch.setenv("TESSERA_BEARER_TOKENS", "alice:tk_abc123_xxxxxxxxxx")
    monkeypatch.delenv("TESSERA_BEARER_TOKENS_FILE", raising=False)
    monkeypatch.setenv("TESSERA_BEARER_TOKEN", "tk_legacy_token_longerthan16")
    result = build_token_list()
    assert len(result) == 1
    assert result[0].name == "alice"


# ---------------------------------------------------------------------------
# BearerTokenAuthenticator.authenticate
# ---------------------------------------------------------------------------


def _make_request(token: str | None = None) -> MagicMock:
    request = MagicMock()
    if token is not None:
        request.headers.get.return_value = f"Bearer {token}"
    else:
        request.headers.get.return_value = None
    return request


def test_authenticate_valid_token() -> None:
    tokens = [Token("alice", "tk_valid_token_longerthan16", "alice")]
    auth = BearerTokenAuthenticator(tokens=tokens)
    request = _make_request("tk_valid_token_longerthan16")
    ctx = auth.authenticate(request)
    assert ctx.principal_id == "alice"
    assert ctx.scope == "alice"
    assert ctx.metadata == {}


def test_authenticate_wrong_token_raises_unauthorized() -> None:
    tokens = [Token("alice", "tk_valid_token_longerthan16", "alice")]
    auth = BearerTokenAuthenticator(tokens=tokens)
    request = _make_request("tk_wrong_token_longerthan16")
    with pytest.raises(UnauthorizedError):
        auth.authenticate(request)


def test_authenticate_dev_mode_bypass() -> None:
    auth = BearerTokenAuthenticator(tokens=[])
    request = _make_request(None)
    ctx = auth.authenticate(request)
    assert ctx.principal_id == "anonymous"
    assert ctx.metadata.get("warning") == "auth_disabled"


def test_authenticate_per_token_scope_reaches_auth_context() -> None:
    tokens = [Token("alice", "tk_valid_token_longerthan16", "team-alpha")]
    auth = BearerTokenAuthenticator(tokens=tokens)
    request = _make_request("tk_valid_token_longerthan16")
    ctx = auth.authenticate(request)
    assert ctx.scope == "team-alpha"


def test_authenticate_missing_header_raises() -> None:
    tokens = [Token("alice", "tk_valid_token_longerthan16", "alice")]
    auth = BearerTokenAuthenticator(tokens=tokens)
    request = _make_request(None)
    with pytest.raises(UnauthorizedError):
        auth.authenticate(request)


# ---------------------------------------------------------------------------
# Constant-time compare (soft timing check)
# ---------------------------------------------------------------------------


def test_constant_time_compare_property() -> None:
    """Timing delta between valid and invalid token comparisons should be < 10x.

    This is a soft check — constant-time compare means there should be no
    significant timing difference between a match and a non-match.
    """
    valid_token = "tk_valid_token_1234567890"
    wrong_token = "tk_wrong_token_1234567890"

    tokens = [Token("alice", valid_token, "alice")]
    auth = BearerTokenAuthenticator(tokens=tokens)

    request_valid = _make_request(valid_token)
    request_wrong = _make_request(wrong_token)

    n = 500

    t_valid = timeit.timeit(lambda: auth.authenticate(request_valid), number=n)

    def _try_wrong() -> None:
        try:
            auth.authenticate(request_wrong)
        except UnauthorizedError:
            pass

    t_wrong = timeit.timeit(_try_wrong, number=n)

    # Neither should be more than 10x the other — constant-time property
    if t_valid > 0 and t_wrong > 0:
        ratio = max(t_valid, t_wrong) / min(t_valid, t_wrong)
        assert ratio < 10, f"Timing ratio {ratio:.2f} exceeds 10x — possible timing leak"
