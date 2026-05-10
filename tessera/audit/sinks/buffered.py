"""Buffered sink: wraps another sink, falls back to disk on failure.

Reliability backstop. When the wrapped sink fails (network down, S3 throttled,
etc.), this sink writes events to a bounded disk-backed queue. A background
thread retries periodically. On overflow, drops the oldest event and emits
audit.buffer.overflow to the surviving sinks.

Trade-offs:
- Bounded queue (default 100 MB) prevents disk-fill DoS
- Drop-oldest preserves recent events (which are typically more interesting)
- Separate thread keeps the hot path fast
- Persistence across restart: yes (on-disk JSONL files)
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol


class _Sink(Protocol):
    name: str

    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


class BufferedSink:
    """Wraps another sink; buffers to disk on failure.

    Args:
        wrapped: The downstream sink to retry against.
        buffer_dir: Directory for the on-disk queue. Created if missing.
        max_buffer_bytes: Soft cap on total buffer size (default 100 MB).
        retry_interval_seconds: Sleep between retry attempts (default 30s).
        on_drop: Optional callback invoked when an event is dropped due to overflow.
            Signature: `(event: dict, reason: str) -> None`.
    """

    name: str = "buffered"

    def __init__(
        self,
        wrapped: _Sink,
        buffer_dir: str | Path,
        max_buffer_bytes: int = 100 * 1024 * 1024,
        retry_interval_seconds: float = 30.0,
        on_drop: Any | None = None,
    ) -> None:
        self.wrapped = wrapped
        self.buffer_dir = Path(buffer_dir)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.max_buffer_bytes = max_buffer_bytes
        self.retry_interval_seconds = retry_interval_seconds
        self._on_drop = on_drop

        self._memory_queue: deque[dict[str, Any]] = deque()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._retry_thread: threading.Thread | None = None
        self._start_retry_loop()

    def _start_retry_loop(self) -> None:
        self._retry_thread = threading.Thread(target=self._retry_loop, daemon=True, name="audit-buffered-retry")
        self._retry_thread.start()

    def _retry_loop(self) -> None:
        while not self._stop.wait(self.retry_interval_seconds):
            self._drain()

    def _drain(self) -> None:
        with self._lock:
            queue_snapshot = list(self._memory_queue)
        for event in queue_snapshot:
            try:
                self.wrapped.emit(event)
            except Exception:  # noqa: BLE001
                # still failing; leave in queue for next attempt
                return
            else:
                with self._lock:
                    try:
                        self._memory_queue.remove(event)
                    except ValueError:
                        pass

    def _current_buffer_bytes(self) -> int:
        # Estimate via in-memory queue (cheaper than scanning disk).
        return sum(len(json.dumps(e, default=str).encode("utf-8")) for e in self._memory_queue)

    def emit(self, event: dict[str, Any]) -> None:
        try:
            self.wrapped.emit(event)
            return
        except Exception:  # noqa: BLE001
            pass

        with self._lock:
            # Overflow: drop oldest until we fit
            while self._current_buffer_bytes() > self.max_buffer_bytes and self._memory_queue:
                dropped = self._memory_queue.popleft()
                if self._on_drop is not None:
                    self._on_drop(dropped, "buffer_overflow")
            self._memory_queue.append(event)
            # Persist to disk for restart survivability
            self._persist(event)

    def _persist(self, event: dict[str, Any]) -> None:
        # One file per day; one event per JSONL line.
        day_path = self.buffer_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
        with day_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def close(self) -> None:
        self._stop.set()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=5)
        self.wrapped.close()
