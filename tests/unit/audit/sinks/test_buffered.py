"""Tests for BufferedSink."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tessera.audit.sinks._buffered import BufferedSink


class _AlwaysOkSink:
    """Fake sink that always succeeds."""

    name: str = "always_ok"

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.received.append(event)

    def close(self) -> None:
        pass


class _FailOnceSink:
    """Fake sink that raises on the first N emits then succeeds."""

    name: str = "fail_once"

    def __init__(self, fail_count: int = 1) -> None:
        self.received: list[dict[str, Any]] = []
        self._remaining_failures = fail_count

    def emit(self, event: dict[str, Any]) -> None:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("injected failure")
        self.received.append(event)

    def close(self) -> None:
        pass


class _AlwaysFailSink:
    """Fake sink that always raises."""

    name: str = "always_fail"

    def __init__(self) -> None:
        self.dropped: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        raise RuntimeError("always fails")

    def close(self) -> None:
        pass


def test_emit_success_passes_through(tmp_path: Path) -> None:
    inner = _AlwaysOkSink()
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=60)
    try:
        event = {"action": "test", "id": "1"}
        sink.emit(event)
        assert inner.received == [event]
        assert len(sink._memory_queue) == 0
    finally:
        sink.close()


def test_emit_failure_buffers_event(tmp_path: Path) -> None:
    inner = _AlwaysFailSink()
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=60)
    try:
        event = {"action": "buffered", "id": "2"}
        sink.emit(event)
        assert len(sink._memory_queue) == 1
        assert sink._memory_queue[0] == event
    finally:
        sink.close()


def test_retry_drains_queue(tmp_path: Path) -> None:
    inner = _FailOnceSink(fail_count=1)
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=0.05)
    try:
        event = {"action": "retry", "id": "3"}
        sink.emit(event)
        # Event should be in the queue because the first emit failed
        assert len(sink._memory_queue) == 1
        # Wait for the retry thread to drain the queue
        time.sleep(0.2)
        assert len(sink._memory_queue) == 0
        assert inner.received == [event]
    finally:
        sink.close()


def test_overflow_drops_oldest(tmp_path: Path) -> None:
    dropped: list[dict[str, Any]] = []
    inner = _AlwaysFailSink()
    # max_buffer_bytes=100 forces overflow quickly
    sink = BufferedSink(
        inner,
        buffer_dir=tmp_path,
        max_buffer_bytes=100,
        retry_interval_seconds=60,
        on_drop=lambda event, reason: dropped.append(event),
    )
    try:
        # Each event is well over 50 bytes serialized, so two events exceed the cap
        event_a = {"action": "first", "payload": "a" * 60}
        event_b = {"action": "second", "payload": "b" * 60}
        event_c = {"action": "third", "payload": "c" * 60}
        sink.emit(event_a)
        sink.emit(event_b)
        sink.emit(event_c)
        # Oldest event(s) should have been dropped to make room
        assert len(dropped) >= 1
        assert dropped[0] == event_a
    finally:
        sink.close()


def test_close_stops_retry_thread(tmp_path: Path) -> None:
    inner = _AlwaysOkSink()
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=60)
    thread = sink._retry_thread
    assert thread is not None
    assert thread.is_alive()
    sink.close()
    # Give join a moment to complete (it has a 5s timeout internally)
    assert not thread.is_alive()


def test_name_attribute_is_buffered(tmp_path: Path) -> None:
    inner = _AlwaysOkSink()
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=60)
    try:
        assert sink.name == "buffered"
    finally:
        sink.close()


def test_persist_writes_jsonl_file(tmp_path: Path) -> None:
    inner = _AlwaysFailSink()
    sink = BufferedSink(inner, buffer_dir=tmp_path, retry_interval_seconds=60)
    try:
        event = {"action": "persist_check", "id": "99"}
        sink.emit(event)
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["action"] == "persist_check"
        assert parsed["id"] == "99"
    finally:
        sink.close()
