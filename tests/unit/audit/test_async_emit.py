"""Tests for AsyncAuditQueue (P0-13).

The async queue is the single-consumer worker that drains audit emits off the
FastAPI hot path. Tests pin:
  - enqueue returns immediately (no inline sink work).
  - drain() flushes all queued events to the underlying emitter.
  - chain ordering survives concurrent enqueue (single consumer FIFO).
  - emit_with_id on AuditEmitter produces a valid, chain-linked event.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tessera.audit.async_emit import AsyncAuditQueue
from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter


class _MemorySink:
    name = "memory"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(event)

    def close(self) -> None:
        pass

    def head_hash(self, scope: str) -> str:
        with self._lock:
            for ev in reversed(self.events):
                if ev.get("tenantId") == scope:
                    return str(ev.get("eventHash", ""))
        return ""

    def iter_events(self, scope: str | None = None) -> Any:
        with self._lock:
            return iter(list(self.events))


def _make_emitter() -> tuple[AuditEmitter, _MemorySink]:
    sink = _MemorySink()
    emitter = AuditEmitter(
        tenant_id="tnt_test",
        sinks=[sink],
        hash_chain=HashChain(),
    )
    return emitter, sink


def test_emit_with_id_uses_supplied_event_id():
    """emit_with_id preserves the caller-supplied event_id end-to-end."""
    emitter, sink = _make_emitter()
    stamped = emitter.emit_with_id(
        event_id="evt_known_id",
        event_type="decision",
        payload={"k": "v"},
    )
    assert stamped["eventId"] == "evt_known_id"
    assert sink.events[0]["eventId"] == "evt_known_id"


def test_emit_with_id_advances_chain():
    """Two emits with caller-supplied IDs produce a valid chain."""
    emitter, sink = _make_emitter()
    a = emitter.emit_with_id(event_id="evt_a", event_type="decision", payload={})
    b = emitter.emit_with_id(event_id="evt_b", event_type="decision", payload={})
    assert b["prevEventHash"] == a["eventHash"]
    assert a["prevEventHash"] == ""


@pytest.mark.asyncio
async def test_async_queue_drains_to_emitter():
    """enqueue() + drain() yields all events on the sink, in enqueue order."""
    emitter, sink = _make_emitter()
    queue = AsyncAuditQueue()
    await queue.start()

    for i in range(5):
        queue.enqueue(
            emitter=emitter,
            event_id=f"evt_{i}",
            event_type="decision",
            payload={"i": i},
            pricing_snapshot_id=None,
        )

    await queue.drain(timeout=5.0)

    assert len(sink.events) == 5
    ids = [ev["eventId"] for ev in sink.events]
    assert ids == [f"evt_{i}" for i in range(5)]
    # Chain links — single consumer guarantees ordering.
    for prev, curr in zip(sink.events, sink.events[1:], strict=False):
        assert curr["prevEventHash"] == prev["eventHash"]


@pytest.mark.asyncio
async def test_async_queue_drain_idempotent():
    """Calling drain() twice does not raise / hang."""
    emitter, sink = _make_emitter()
    queue = AsyncAuditQueue()
    await queue.start()
    queue.enqueue(
        emitter=emitter,
        event_id="evt_only",
        event_type="decision",
        payload={},
        pricing_snapshot_id=None,
    )
    await queue.drain(timeout=2.0)
    await queue.drain(timeout=2.0)  # second call must be a no-op
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_async_queue_sync_fallback_when_unstarted():
    """enqueue() before start() falls back to synchronous emit (never silent drop)."""
    emitter, sink = _make_emitter()
    queue = AsyncAuditQueue()  # NOT started
    queue.enqueue(
        emitter=emitter,
        event_id="evt_fallback",
        event_type="decision",
        payload={"x": 1},
        pricing_snapshot_id=None,
    )
    assert len(sink.events) == 1
    assert sink.events[0]["eventId"] == "evt_fallback"


@pytest.mark.asyncio
async def test_async_queue_handles_emitter_failure():
    """A sink failure mid-consumer bumps on_failure but doesn't stop the queue."""
    failures: list[BaseException] = []
    emitter, sink = _make_emitter()
    # Patch the sink to raise on emit
    original_emit = sink.emit
    call_count = {"n": 0}

    def _bad_emit(event: dict[str, Any]) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first emit boom")
        original_emit(event)

    sink.emit = _bad_emit  # type: ignore[method-assign]

    queue = AsyncAuditQueue(on_failure=lambda exc: failures.append(exc))
    await queue.start()
    queue.enqueue(
        emitter=emitter, event_id="evt_1", event_type="decision",
        payload={}, pricing_snapshot_id=None,
    )
    queue.enqueue(
        emitter=emitter, event_id="evt_2", event_type="decision",
        payload={}, pricing_snapshot_id=None,
    )
    await queue.drain(timeout=5.0)

    # Even though emit raised on the first event, the queue kept going and the
    # second event landed on the sink.
    assert any(ev.get("eventId") == "evt_2" for ev in sink.events)
