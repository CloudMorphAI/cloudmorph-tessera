"""Digest-only opaque client credentials; canonical bearer identity mapping."""
from __future__ import annotations

import hashlib
import re
import time

from starlette.requests import Request

from tessera.auth.bearer import BearerTokenAuthenticator, Token
from tessera.errors import ConfigError, UnauthorizedError

_BINDING = None


def configure(*, tenant, clients, expires_at):
    global _BINDING
    if (_BINDING is not None or not re.fullmatch(r"[a-z0-9_-]{1,64}", tenant)
            or set(clients) != {"opencode", "codex"}
            or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in clients.values())
            or len(set(clients.values())) != 2 or type(expires_at) is not int):
        raise ConfigError("EXTERNAL_IDENTITY_BINDING_INVALID")
    _BINDING = (tenant, dict(clients), expires_at)


class DigestBearerAuthenticator(BearerTokenAuthenticator):
    """Hash in-flight tokens, then reuse canonical constant-time matching.

    Server trust configuration contains only SHA256 digests of separately
    delivered 256-bit opaque tokens. Raw tokens never enter env/config/files.
    Distinct authenticated principals deliberately share one tenant audit scope.
    """

    def __init__(self, deployment_id="default"):
        if _BINDING is None or _BINDING[0] != deployment_id:
            raise ConfigError("EXTERNAL_IDENTITY_BINDING_REQUIRED")
        tenant, clients, self.expires_at = _BINDING
        super().__init__(tokens=[Token(name, digest, tenant) for name, digest in sorted(clients.items())],
                         deployment_id=tenant)

    def authenticate(self, request):
        values = request.headers.getlist("authorization")
        if len(values) != 1 or time.time() >= self.expires_at:
            raise UnauthorizedError("External client authorization unavailable")
        scheme, separator, raw = values[0].partition(" ")
        if scheme.lower() != "bearer" or not separator or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", raw):
            raise UnauthorizedError("External client authorization unavailable")
        hashed = hashlib.sha256(raw.encode("ascii")).hexdigest()
        headers = [(name, value) for name, value in request.scope["headers"] if name.lower() != b"authorization"]
        headers.append((b"authorization", ("Bearer " + hashed).encode("ascii")))
        # This is a Request view for the canonical matcher, not an outbound call.
        return super().authenticate(Request({**request.scope, "headers": headers}))
