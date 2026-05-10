"""Bearer token authenticator with multi-token support."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from starlette.requests import Request

import yaml

from tessera.auth.base import NAME_RE, SCOPE_RE, AuthContext
from tessera.errors import ConfigError, UnauthorizedError

logger = logging.getLogger(__name__)

_TOKEN_MIN_LEN = 16


class Token(NamedTuple):
    name: str
    token: str
    scope: str


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str, context: str) -> None:
    if not NAME_RE.match(name):
        raise ConfigError(f"{context}: name {name!r} must match [a-z0-9_-]{{1,64}}")


def _validate_scope(scope: str, context: str) -> None:
    if not SCOPE_RE.match(scope):
        raise ConfigError(f"{context}: scope {scope!r} must match [a-z0-9_-]{{1,64}}")


def _validate_token_length(token: str, context: str) -> None:
    if len(token.strip()) < _TOKEN_MIN_LEN:
        raise ConfigError(f"{context}: token must be at least {_TOKEN_MIN_LEN} characters")


# ---------------------------------------------------------------------------
# Token list builder
# ---------------------------------------------------------------------------


def build_token_list() -> list[Token]:
    """Build token list from environment variables.

    Precedence:
    1. TESSERA_BEARER_TOKENS  — inline comma-separated name:token pairs
    2. TESSERA_BEARER_TOKENS_FILE — YAML file with tokens list
    3. TESSERA_BEARER_TOKEN   — single legacy token
    4. (none set)             — dev mode, empty list
    """
    inline = os.environ.get("TESSERA_BEARER_TOKENS")
    if inline is not None:
        return _parse_inline(inline)

    tokens_file = os.environ.get("TESSERA_BEARER_TOKENS_FILE")
    if tokens_file is not None:
        return _parse_file(tokens_file)

    single = os.environ.get("TESSERA_BEARER_TOKEN")
    if single is not None:
        _validate_token_length(single, "TESSERA_BEARER_TOKEN")
        return [Token("default", single, "default")]

    # Dev mode — no auth
    return []


def _parse_inline(raw: str) -> list[Token]:
    """Parse 'name1:tk_xxx,name2:tk_yyy' format."""
    tokens: list[Token] = []
    entries = raw.split(",")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ConfigError(f"TESSERA_BEARER_TOKENS: entry {entry!r} has no ':' separator")
        # Split on FIRST ':' only
        name, token = entry.split(":", 1)
        name = name.strip()
        token = token.strip()
        if not name:
            raise ConfigError(f"TESSERA_BEARER_TOKENS: entry has empty name in {entry!r}")
        if not token:
            raise ConfigError(f"TESSERA_BEARER_TOKENS: entry has empty token for name {name!r}")
        _validate_name(name, "TESSERA_BEARER_TOKENS")
        _validate_token_length(token, f"TESSERA_BEARER_TOKENS[{name}]")
        tokens.append(Token(name, token, name))
    return tokens


def _parse_file(path_str: str) -> list[Token]:
    """Parse YAML file with tokens: [{name, token, scope?}] structure."""
    path = Path(path_str)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: cannot read {path_str!r}: {exc}") from exc

    if not isinstance(data, dict) or "tokens" not in data:
        raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: {path_str!r} must contain a 'tokens' list")

    raw_tokens = data["tokens"]
    if not isinstance(raw_tokens, list):
        raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: 'tokens' must be a list in {path_str!r}")

    tokens: list[Token] = []
    seen_names: set[str] = set()

    for i, entry in enumerate(raw_tokens):
        if not isinstance(entry, dict):
            raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: tokens[{i}] must be a mapping")
        if "name" not in entry:
            raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: tokens[{i}] missing 'name' field")
        if "token" not in entry:
            raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: tokens[{i}] missing 'token' field")

        name = str(entry["name"]).strip()
        token = str(entry["token"]).strip()
        scope = str(entry.get("scope", name)).strip()

        _validate_name(name, f"TESSERA_BEARER_TOKENS_FILE tokens[{i}]")
        _validate_scope(scope, f"TESSERA_BEARER_TOKENS_FILE tokens[{i}]")
        _validate_token_length(token, f"TESSERA_BEARER_TOKENS_FILE tokens[{i}]({name})")

        if name in seen_names:
            raise ConfigError(f"TESSERA_BEARER_TOKENS_FILE: duplicate token name {name!r}")
        seen_names.add(name)
        tokens.append(Token(name, token, scope))

    return tokens


# ---------------------------------------------------------------------------
# Dev-mode warning
# ---------------------------------------------------------------------------


def _run_dev_warning_loop(stop_event: threading.Event) -> None:
    logger.warning("auth_disabled: no bearer tokens configured — running in unauthenticated dev mode")
    while not stop_event.wait(60):
        logger.warning("auth_disabled: no bearer tokens configured — running in unauthenticated dev mode")


# ---------------------------------------------------------------------------
# Authenticator
# ---------------------------------------------------------------------------


class BearerTokenAuthenticator:
    def __init__(
        self,
        tokens: list[Token] | None = None,
        deployment_id: str = "default",
    ) -> None:
        if tokens is None:
            tokens = build_token_list()
        self._tokens = tokens
        self.deployment_id = deployment_id
        self._stop_event: threading.Event | None = None

        if not self._tokens:
            self._start_dev_warning()

    def _start_dev_warning(self) -> None:
        self._stop_event = threading.Event()
        t = threading.Thread(
            target=_run_dev_warning_loop,
            args=(self._stop_event,),
            daemon=True,
            name="tessera-auth-dev-warning",
        )
        t.start()

    def authenticate(self, request: Request) -> AuthContext:

        # Dev mode — no tokens configured
        if not self._tokens:
            return AuthContext(
                principal_id="anonymous",
                scope=self.deployment_id,
                metadata={"warning": "auth_disabled"},
            )

        auth_header: str | None = request.headers.get("Authorization")
        if not auth_header:
            raise UnauthorizedError("Missing Authorization header")

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise UnauthorizedError("Authorization header must use Bearer scheme")

        incoming_token = parts[1]

        for candidate in self._tokens:
            if secrets.compare_digest(candidate.token, incoming_token):
                return AuthContext(
                    principal_id=candidate.name,
                    scope=candidate.scope,
                    metadata={},
                )

        raise UnauthorizedError("Invalid bearer token")
