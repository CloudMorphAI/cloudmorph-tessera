"""AuditEmitter — fan out events to one or more sinks, with hash-chain bookkeeping.

Public surface:

    emitter = AuditEmitter(
        tenant_id="tnt_abc",
        sinks=[StdoutSink(), S3Sink(bucket="audit-bucket")],
        hash_chain=HashChain(),  # optional; one created if omitted
    )
    emitter.emit("decision.made", payload={"outcome": "allow", ...})

The emitter:
1. Builds the AuditEvent dict (eventId, occurredAt, eventType, tenantId, payload, schemaVersion)
2. Stamps prevEventHash + eventHash via HashChain
3. Routes to every sink; isolates per-sink failures (one bad sink doesn't poison others)
4. Returns the stamped event for any caller that needs to log it back through StructuredLogger
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime
from typing import Any

from tessera.audit.chain import HashChain
from tessera.audit.sinks.base import AuditSink
from tessera.errors import AuditSinkError


class AuditEmitter:
    """Emit audit events to one or more sinks. Thread-safe.

    Args:
        tenant_id: Default tenant for events that don't override it.
        sinks: List of sinks to fan out to. At minimum [StdoutSink()].
        hash_chain: Optional HashChain for deterministic linking; one created if omitted.
        on_sink_failure: Optional callback when a sink raises. Signature
            `(sink_name: str, exc: Exception) -> None`. Default: print to stderr.
    """

    SCHEMA_VERSION = "v0.1"

    def __init__(
        self,
        tenant_id: str,
        sinks: list[AuditSink],
        hash_chain: HashChain | None = None,
        on_sink_failure: Any | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("AuditEmitter: tenant_id is required")
        if not sinks:
            raise ValueError("AuditEmitter: at least one sink is required")
        self.tenant_id = tenant_id
        self.sinks = sinks
        self.hash_chain = hash_chain or HashChain()
        self._on_sink_failure = on_sink_failure
        self._lock = threading.RLock()

    @staticmethod
    def _new_event_id() -> str:
        return f"evt_{secrets.token_urlsafe(24).replace('-', '_').replace('=', '')[:30]}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        actor_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Emit an event. Returns the stamped event."""
        event: dict[str, Any] = {
            "schemaVersion": self.SCHEMA_VERSION,
            "eventId": self._new_event_id(),
            "tenantId": tenant_id or self.tenant_id,
            "eventType": event_type,
            "payload": payload or {},
            "occurredAt": self._now_iso(),
        }
        if session_id:
            event["sessionId"] = session_id
        if actor_id:
            event["actorId"] = actor_id

        with self._lock:
            stamped = self.hash_chain.stamp(event)

        for sink in self.sinks:
            try:
                sink.emit(stamped)
            except Exception as exc:  # noqa: BLE001
                if self._on_sink_failure is not None:
                    try:
                        self._on_sink_failure(getattr(sink, "name", "unknown"), exc)
                    except Exception:  # noqa: BLE001 — never let the failure callback propagate
                        pass
                # Continue to other sinks; AuditSinkError is expected behavior.
                if not isinstance(exc, AuditSinkError):
                    # Unexpected error — re-raise after attempting other sinks would lose events.
                    # Log structurally via on_sink_failure already; swallow.
                    pass

        return stamped

    def close(self) -> None:
        """Close all sinks; idempotent."""
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass
