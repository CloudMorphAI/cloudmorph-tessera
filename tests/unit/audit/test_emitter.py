"""Unit tests for tessera.audit.emitter.AuditEmitter."""
from __future__ import annotations

from typing import Any

import pytest

from tessera.audit.emitter import AuditEmitter
from tessera.audit.chain import HashChain
from tessera.errors import AuditSinkError


# ---------------------------------------------------------------------------
# Fake sink for isolation
# ---------------------------------------------------------------------------


class FakeSink:
    name: str = "fake"

    def __init__(self, name: str = "fake", raise_on_emit: Exception | None = None) -> None:
        self.name = name
        self.received: list[dict[str, Any]] = []
        self.closed = False
        self._raise_on_emit = raise_on_emit

    def emit(self, event: dict[str, Any]) -> None:
        if self._raise_on_emit is not None:
            raise self._raise_on_emit
        self.received.append(event)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_emit_returns_stamped_event() -> None:
    sink = FakeSink()
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink])
    result = emitter.emit("test.event", payload={"key": "value"})
    assert "eventHash" in result
    assert "prevEventHash" in result
    assert result["eventType"] == "test.event"
    assert result["tenantId"] == "tnt_a"
    assert result["payload"] == {"key": "value"}


def test_emit_fan_out_to_multiple_sinks() -> None:
    sink_a = FakeSink(name="a")
    sink_b = FakeSink(name="b")
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink_a, sink_b])
    emitter.emit("test.event")
    assert len(sink_a.received) == 1
    assert len(sink_b.received) == 1
    assert sink_a.received[0]["eventId"] == sink_b.received[0]["eventId"]


def test_emit_sink_failure_isolation() -> None:
    """First sink raises; second sink still receives the event."""
    bad_sink = FakeSink(name="bad", raise_on_emit=AuditSinkError("disk full"))
    good_sink = FakeSink(name="good")
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[bad_sink, good_sink])
    result = emitter.emit("test.event")
    assert len(good_sink.received) == 1
    assert good_sink.received[0]["eventId"] == result["eventId"]


def test_emit_on_sink_failure_callback_called() -> None:
    failures: list[tuple[str, Exception]] = []

    def on_failure(sink_name: str, exc: Exception) -> None:
        failures.append((sink_name, exc))

    bad_sink = FakeSink(name="bad", raise_on_emit=AuditSinkError("boom"))
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[bad_sink], on_sink_failure=on_failure)
    emitter.emit("test.event")
    assert len(failures) == 1
    assert failures[0][0] == "bad"
    assert isinstance(failures[0][1], AuditSinkError)


def test_emit_with_session_id_and_actor_id() -> None:
    sink = FakeSink()
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink])
    emitter.emit("test.event", session_id="sess_123", actor_id="user_456")
    event = sink.received[0]
    assert event["sessionId"] == "sess_123"
    assert event["actorId"] == "user_456"


def test_emit_tenant_id_override() -> None:
    sink = FakeSink()
    emitter = AuditEmitter(tenant_id="tnt_default", sinks=[sink])
    emitter.emit("test.event", tenant_id="tnt_override")
    assert sink.received[0]["tenantId"] == "tnt_override"


def test_close_closes_all_sinks() -> None:
    sink_a = FakeSink(name="a")
    sink_b = FakeSink(name="b")
    emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink_a, sink_b])
    emitter.close()
    assert sink_a.closed is True
    assert sink_b.closed is True


def test_emitter_requires_nonempty_tenant_id() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        AuditEmitter(tenant_id="", sinks=[FakeSink()])


def test_emitter_requires_at_least_one_sink() -> None:
    with pytest.raises(ValueError, match="sink"):
        AuditEmitter(tenant_id="tnt_a", sinks=[])
