"""Tests for AuditEmitter — event construction, fan-out, sink isolation."""

from __future__ import annotations

from typing import Any

import pytest

from cloudmorph_common.audit.chain import HashChain
from cloudmorph_common.audit.emitter import AuditEmitter
from cloudmorph_common.errors import AuditSinkError


class _RecordingSink:
    name = "recording"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass


class _FailingSink:
    name = "failing"

    def __init__(self, fail_with: type[Exception] = AuditSinkError) -> None:
        self.fail_with = fail_with
        self.attempts = 0

    def emit(self, _event: dict[str, Any]) -> None:
        self.attempts += 1
        if self.fail_with is AuditSinkError:
            raise AuditSinkError("failing", "boom")
        raise self.fail_with("boom")

    def close(self) -> None:
        pass


class TestEmitterConstruction:
    def test_requires_tenant_id(self):
        with pytest.raises(ValueError, match="tenant_id"):
            AuditEmitter(tenant_id="", sinks=[_RecordingSink()])

    def test_requires_at_least_one_sink(self):
        with pytest.raises(ValueError, match="at least one sink"):
            AuditEmitter(tenant_id="tnt_a", sinks=[])


class TestEmitterEmission:
    def test_emit_sets_required_fields(self):
        sink = _RecordingSink()
        emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink])
        stamped = emitter.emit("decision.made", payload={"outcome": "allow"})
        assert stamped["schemaVersion"] == "v0.1"
        assert stamped["eventId"].startswith("evt_")
        assert stamped["tenantId"] == "tnt_a"
        assert stamped["eventType"] == "decision.made"
        assert stamped["payload"] == {"outcome": "allow"}
        assert stamped["eventHash"]
        assert "occurredAt" in stamped

    def test_emit_routes_to_all_sinks(self):
        s1 = _RecordingSink()
        s2 = _RecordingSink()
        emitter = AuditEmitter(tenant_id="tnt_a", sinks=[s1, s2])
        emitter.emit("session.started")
        assert len(s1.events) == 1
        assert len(s2.events) == 1
        assert s1.events[0]["eventHash"] == s2.events[0]["eventHash"]

    def test_chain_advances_across_emits(self):
        sink = _RecordingSink()
        emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink])
        emitter.emit("session.started")
        emitter.emit("decision.made")
        assert sink.events[1]["prevEventHash"] == sink.events[0]["eventHash"]

    def test_sink_failure_does_not_block_others(self):
        good = _RecordingSink()
        bad = _FailingSink()
        emitter = AuditEmitter(tenant_id="tnt_a", sinks=[bad, good])
        emitter.emit("decision.made")
        assert bad.attempts == 1
        assert len(good.events) == 1

    def test_sink_failure_callback_invoked(self):
        bad = _FailingSink()
        captured: list[tuple[str, Exception]] = []
        emitter = AuditEmitter(
            tenant_id="tnt_a",
            sinks=[bad],
            on_sink_failure=lambda name, exc: captured.append((name, exc)),
        )
        emitter.emit("decision.made")
        assert len(captured) == 1
        assert captured[0][0] == "failing"

    def test_per_event_session_and_actor(self):
        sink = _RecordingSink()
        emitter = AuditEmitter(tenant_id="tnt_a", sinks=[sink])
        emitter.emit("decision.made", session_id="ses_x", actor_id="alice@acme.com")
        assert sink.events[0]["sessionId"] == "ses_x"
        assert sink.events[0]["actorId"] == "alice@acme.com"

    def test_per_event_tenant_id_override(self):
        sink = _RecordingSink()
        emitter = AuditEmitter(tenant_id="tnt_default", sinks=[sink])
        emitter.emit("decision.made", tenant_id="tnt_other")
        assert sink.events[0]["tenantId"] == "tnt_other"


class TestEmitterChainSharing:
    def test_chain_can_be_external(self):
        sink = _RecordingSink()
        chain = HashChain()
        emitter1 = AuditEmitter(tenant_id="tnt_a", sinks=[sink], hash_chain=chain)
        emitter2 = AuditEmitter(tenant_id="tnt_a", sinks=[sink], hash_chain=chain)
        # Two emitters sharing one chain: chain should advance across both.
        emitter1.emit("session.started")
        emitter2.emit("decision.made")
        assert sink.events[1]["prevEventHash"] == sink.events[0]["eventHash"]
