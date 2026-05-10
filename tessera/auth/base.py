from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

NAME_RE = re.compile(r'^[a-z0-9_-]{1,64}$')
SCOPE_RE = re.compile(r'^[a-z0-9_-]{1,64}$')


@dataclass
class AuthContext:
    principal_id: str  # token name (or "anonymous" in dev mode)
    scope: str         # audit chain stream (from per-token scope or deployment_id in dev mode)
    metadata: dict[str, Any] = field(default_factory=dict)


class Authenticator(Protocol):
    def authenticate(self, request: "Request") -> AuthContext: ...
