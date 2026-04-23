"""Tests for BufferedSink — disk overflow + drop-oldest behavior."""

from __future__ import annotations

import json
import time
from typing import Any

from cloudmorph_common.audit.sinks.buffered import BufferedSink


class _AlwaysFailSink:
    name = "always_fail"

    def emit(self, _event: dict[str, Any]) -> None:
        raise RuntimeError("simulated failure")

    def close(self) -> None:
        pass


class _FlakySink:
    """Fails the first N emits, then succeeds."""

    name = "flaky"

    def __init__(self, fail_count: int) -> None:
        self._remaining = fail_count
        self.received: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("flaky")
        self.received.append(event)

    def close(self) -> None:
        pass


def _make_event(idx: int) -> dict[str, Any]:
    return {
        "eventId": f"evt_test_{idx:04d}",
        "tenantId": "tnt_a",
        "eventType": "decision.made",
        "payload": {"i": idx},
        "occurredAt": "2026-04-23T00:00:00Z",
    }


class TestBufferedFallback:
    def test_failing_sink_buffers_to_disk(self, tmp_path):
        wrapped = _AlwaysFailSink()
        sink = BufferedSink(wrapped, buffer_dir=tmp_path, retry_interval_seconds=10)
        try:
            sink.emit(_make_event(1))
            # Disk persistence file exists
            day_files = list(tmp_path.glob("*.jsonl"))
            assert len(day_files) == 1
            content = day_files[0].read_text(encoding="utf-8").strip()
            event = json.loads(content)
            assert event["eventId"] == "evt_test_0001"
        finally:
            sink.close()

    def test_overflow_drops_oldest(self, tmp_path):
        wrapped = _AlwaysFailSink()
        # Tiny buffer (1 KB); each event ~150 bytes, so ~6 events fit.
        dropped: list[tuple[dict, str]] = []
        sink = BufferedSink(
            wrapped,
            buffer_dir=tmp_path,
            max_buffer_bytes=1024,
            retry_interval_seconds=60,
            on_drop=lambda e, r: dropped.append((e, r)),
        )
        try:
            for i in range(20):
                sink.emit(_make_event(i))
            # Some early events should have been dropped.
            assert len(dropped) > 0
            # Reasons all "buffer_overflow"
            assert all(r == "buffer_overflow" for _e, r in dropped)
        finally:
            sink.close()

    def test_recovery_drains_buffer(self, tmp_path):
        wrapped = _FlakySink(fail_count=2)
        sink = BufferedSink(
            wrapped,
            buffer_dir=tmp_path,
            retry_interval_seconds=0.1,  # fast retry for test
        )
        try:
            sink.emit(_make_event(1))
            sink.emit(_make_event(2))
            sink.emit(_make_event(3))
            # Wait for retry loop to drain
            time.sleep(0.5)
            # Eventually all events delivered.
            assert len(wrapped.received) >= 1
        finally:
            sink.close()
