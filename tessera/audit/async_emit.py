"""Async audit emit queue (P0-13).

Wraps `AuditEmitter` with a per-process `asyncio.Queue` + single-consumer
background task. The proxy hot path calls `enqueue(...)` (cheap — just an
`asyncio.Queue.put_nowait`) and the consumer drains in the background via
`asyncio.to_thread`, so the SHA-256 stamp + SQLite WAL fsync no longer block
the FastAPI event loop.

Why single consumer? The hash chain is per-scope and `HashChain.stamp` uses
an internal `RLock`, so the chain remains valid even under concurrent stamping.
But a single consumer is simpler and gives deterministic per-scope event
ordering (FIFO from enqueue order — that matches request-arrival order).

Failure mode:
- Queue.put_nowait raises QueueFull → drop the event, bump counter, never block.
- Consumer raises while stamping/persisting → log, bump counter, keep draining.
- Shutdown: `await drain()` waits for the consumer to flush, with a timeout.

Test sync bypass: set TESSERA_AUDIT_SYNC=1 in env to make `enqueue()` run the
emit inline (the pre-P0-13 behaviour) for deterministic unit tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from tessera.audit.emitter import AuditEmitter

logger = logging.getLogger(__name__)

# Soft bound on the in-flight queue. If the consumer ever falls behind, this
# bounds memory growth. On overflow we drop the event and bump the metric.
_DEFAULT_QUEUE_SIZE = 10_000


@dataclass(slots=True)
class _Job:
    emitter: AuditEmitter
    event_id: str
    event_type: str
    payload: dict[str, Any]
    pricing_snapshot_id: str | None


class AsyncAuditQueue:
    """Single-consumer asyncio queue that drains audit emits off the hot path.

    Lifecycle:
        queue = AsyncAuditQueue()
        await queue.start()       # spawn the consumer task
        queue.enqueue(...)        # cheap, called from request handlers
        await queue.drain()       # called from lifespan shutdown
    """

    def __init__(
        self,
        *,
        maxsize: int = _DEFAULT_QUEUE_SIZE,
        on_dropped: Any | None = None,
        on_failure: Any | None = None,
    ) -> None:
        self._maxsize = maxsize
        self._queue: asyncio.Queue[_Job | None] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._on_dropped = on_dropped
        self._on_failure = on_failure
        self._stopped = False

    async def start(self) -> None:
        """Spawn the consumer task. Must be called inside a running event loop."""
        if self._consumer is not None:
            return
        # Bind the queue to the current loop. asyncio.Queue takes its loop from
        # the surrounding event loop when first awaited.
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._consumer = asyncio.create_task(
            self._run(), name="tessera-audit-consumer"
        )

    def enqueue(
        self,
        *,
        emitter: AuditEmitter,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        pricing_snapshot_id: str | None,
    ) -> None:
        """Push a job onto the queue. Non-blocking; drops on QueueFull."""
        if self._queue is None:
            # Not started — fall back to a synchronous emit so we never lose
            # the event silently. This path fires during pytest setups that
            # bypass the lifespan.
            try:
                emitter.emit_with_id(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    pricing_snapshot_id=pricing_snapshot_id,
                )
            except Exception as exc:  # noqa: BLE001
                if self._on_failure is not None:
                    try:
                        self._on_failure(exc)
                    except Exception:  # noqa: BLE001
                        pass
                logger.error("event=audit_emit_failed_sync_fallback error=%s", exc)
            return

        job = _Job(
            emitter=emitter,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            pricing_snapshot_id=pricing_snapshot_id,
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            if self._on_dropped is not None:
                try:
                    self._on_dropped(job)
                except Exception:  # noqa: BLE001
                    pass
            logger.warning(
                "event=audit_queue_overflow event_id=%s event_type=%s",
                event_id,
                event_type,
            )

    async def drain(self, timeout: float = 10.0) -> None:  # noqa: ASYNC109 — timeout is part of public API
        """Wait for the consumer to flush every queued job, then stop it."""
        if self._queue is None or self._consumer is None:
            return
        if self._stopped:
            return
        self._stopped = True
        # Send the shutdown sentinel
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # Queue full → wait for drain then push the sentinel
            await self._queue.put(None)
        try:
            await asyncio.wait_for(self._consumer, timeout=timeout)
        except TimeoutError:
            logger.warning(
                "event=audit_drain_timeout queued=%d", self._queue.qsize()
            )
            self._consumer.cancel()
            try:
                await self._consumer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            if job is None:
                # Shutdown sentinel — exit after marking task_done.
                self._queue.task_done()
                return
            try:
                await asyncio.to_thread(self._emit_blocking, job)
            except Exception as exc:  # noqa: BLE001
                if self._on_failure is not None:
                    try:
                        self._on_failure(exc)
                    except Exception:  # noqa: BLE001
                        pass
                logger.error(
                    "event=audit_emit_failed_async event_id=%s error=%s",
                    job.event_id,
                    exc,
                )
            finally:
                self._queue.task_done()

    @staticmethod
    def _emit_blocking(job: _Job) -> None:
        """Body of audit emit; runs in a worker thread via asyncio.to_thread."""
        job.emitter.emit_with_id(
            event_id=job.event_id,
            event_type=job.event_type,
            payload=job.payload,
            pricing_snapshot_id=job.pricing_snapshot_id,
        )


def sync_mode_enabled() -> bool:
    """Return True if TESSERA_AUDIT_SYNC is set (tests bypass the async path)."""
    return os.environ.get("TESSERA_AUDIT_SYNC", "").lower() in ("1", "true", "yes")
