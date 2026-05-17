"""tests/fixtures/null_sink.py — no-op audit sink for benchmark comparisons only.

Used by bench-03-audit-overhead.sh to isolate the cost of the SQLite audit
write from the rest of the proxy hot path. NEVER ship this in production —
all decisions go unrecorded, defeating the hash-chain integrity guarantee.

Wire it via env var:
    TESSERA_AUDIT_SINK=tests.fixtures.null_sink:NullSink tessera serve ...
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class NullSink:
    """Drops every event on the floor. Returns empty chain head, no events."""

    name: str = "null"

    def __init__(self, path: str | None = None) -> None:  # noqa: ARG002
        # Match the SqliteSink(path=...) signature; ignore the value.
        pass

    def emit(self, event: dict[str, Any]) -> None:  # noqa: ARG002
        # Black hole. No-op.
        return

    def close(self) -> None:
        return

    def head_hash(self, scope: str) -> str:  # noqa: ARG002
        return ""

    def iter_events(self, scope: str | None = None) -> Iterator[dict[str, Any]]:  # noqa: ARG002
        return iter(())

    def iter_scopes(self) -> Iterator[str]:
        return iter(())
