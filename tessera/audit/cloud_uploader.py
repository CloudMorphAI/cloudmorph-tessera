"""Batched async upload of audit events to tessera.cloudmorph.ai.

A producer (typically :class:`tessera.audit.emitter.AuditEmitter` via a sink
or a side-channel callback) enqueues events on this uploader's deque. A
background task flushes every ``flush_interval`` seconds OR whenever the
queue reaches ``batch_size`` events, whichever comes first. Each flush
POSTs to ``/api/tessera/audit/ingest`` (Tessera-Bearer-authed, scope
``tessera:audit:write``).

Failure isolation: if the cloud is unreachable, the queue grows and a backoff
delay is inserted. The local hash chain stays intact regardless — this
uploader is an at-least-once transport, not a source of truth.

Order preservation: failures put events back at the FRONT of the deque (so a
later success uploads them in original order). Note: if the queue overflows
``max_queue`` we drop the OLDEST events (head of deque) — chain head is
preserved at the expense of mid-chain gaps which the server-side verifier
detects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# v0.7.2: auth.tessera.cloudmorph.ai is the ApiMapping for tessera-api-prod.
# Older versions defaulted to https://tessera.cloudmorph.ai (ECS ALB — wrong
# service entirely). See CHANGELOG.md.
_DEFAULT_ENDPOINT = "https://auth.tessera.cloudmorph.ai"
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_FLUSH_INTERVAL = 10
_DEFAULT_MAX_QUEUE = 5000
_DEFAULT_REQUEST_TIMEOUT = 30.0
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 60.0


class AuditCloudUploader:
    """Batched, retried POST of audit events to /api/tessera/audit/ingest."""

    REMOTE_PATH = "/api/tessera/audit/ingest"

    def __init__(
        self,
        endpoint: str | None = None,
        oauth_token: str | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval: int = _DEFAULT_FLUSH_INTERVAL,
        max_queue: int = _DEFAULT_MAX_QUEUE,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        upload_scope: str = "",
    ) -> None:
        self._endpoint = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")
        self._oauth_token = oauth_token or ""
        self._batch_size = max(1, int(batch_size))
        self._flush_interval = max(1, int(flush_interval))
        self._max_queue = max(self._batch_size * 4, int(max_queue))
        self._timeout = float(request_timeout)
        self._upload_scope = upload_scope
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = asyncio.Lock()
        self._backoff_s = _BACKOFF_INITIAL_S
        self._stopped = False

    # ── Producer surface ────────────────────────────────────────────────────

    def enqueue(self, event: dict[str, Any]) -> None:
        """Push an audit event onto the upload queue.

        Drops the OLDEST entries when the queue is over ``max_queue``. The
        chain head is preserved at the cost of mid-chain gaps that the
        server-side verifier will detect.
        """
        if not isinstance(event, dict):
            return
        self._queue.append(event)
        # Single-step cap; do not drain more than necessary.
        overflow = len(self._queue) - self._max_queue
        if overflow > 0:
            for _ in range(overflow):
                self._queue.popleft()
            logger.warning(
                "event=audit_cloud_uploader_overflow dropped=%d depth=%d",
                overflow,
                len(self._queue),
            )

    # ── Background loop ─────────────────────────────────────────────────────

    async def background_flush_loop(self) -> None:
        """Run forever; flush on interval. Stop via :meth:`stop`."""
        while not self._stopped:
            await asyncio.sleep(self._flush_interval)
            await self._flush_once_safe()

    def stop(self) -> None:
        self._stopped = True

    # ── One-shot flush (CLI + smoke-test entry point) ───────────────────────

    async def flush_once(self) -> int:
        """Drain the queue in one or more :meth:`_flush_once` batches.

        Returns the number of events successfully uploaded. Raises if any
        batch fails (caller can re-invoke after fixing connectivity — failed
        events are restored to the front of the queue).
        """
        uploaded = 0
        while self._queue:
            depth_before = len(self._queue)
            await self._flush_once()
            depth_after = len(self._queue)
            sent = depth_before - depth_after
            if sent <= 0:
                # Defensive: nothing moved (failure restored the batch).
                # _flush_once would have raised already; this guards against
                # zero-progress livelocks if anyone subclasses it.
                break
            uploaded += sent
        return uploaded

    async def upload_sqlite_once(
        self, path: str | Path, *, tenant_id: str, stream_id: str,
        run_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload actual canonical sink records; advance only exact cloud acknowledgements.

        Call after proxy shutdown/drain for a final complete run. No token is
        persisted. The cursor is nonsecret and endpoint/tenant/stream bound.
        A failed/unknown HTTP outcome leaves the cursor unchanged for dedup retry.
        """
        import hashlib

        from tessera.audit.canonical_json import canonical_json
        from tessera.audit.chain import HashChain

        if not self._oauth_token or not tenant_id or not stream_id:
            raise ValueError("canonical upload requires session identity and token")
        if run_evidence is not None and (
                not isinstance(run_evidence, dict) or len(canonical_json(run_evidence)) > 128_000):
            raise ValueError("run evidence exceeds the bounded projection contract")
        async with self._lock:
            with sqlite3.connect(path) as conn:
                rows = conn.execute(
                    "SELECT seq,payload_json FROM audit_events WHERE scope=? ORDER BY seq", (tenant_id,),
                ).fetchall()
                events = [(int(seq), json.loads(raw)) for seq, raw in rows]
                if [seq for seq, _event in events] != list(range(1, len(events) + 1)):
                    raise ValueError("SQLite canonical stream must start at sequence one without gaps")
                previous, ids = "", set()
                for _seq, event in events:
                    eid = event.get("eventId")
                    if (event.get("tenantId") != tenant_id or not eid or eid in ids
                            or not HashChain.verify_event_hash(event)
                            or event.get("prevEventHash", "") != previous):
                        raise ValueError("SQLite canonical audit chain is invalid")
                    ids.add(eid)
                    previous = event["eventHash"]
                conn.execute("""CREATE TABLE IF NOT EXISTS cloud_upload_checkpoint (
                    endpoint TEXT, tenant_id TEXT, stream_id TEXT, seq INTEGER NOT NULL,
                    event_hash TEXT NOT NULL, PRIMARY KEY(endpoint,tenant_id,stream_id))""")
                key = (self._endpoint, tenant_id, stream_id)
                cursor = conn.execute(
                    "SELECT seq,event_hash FROM cloud_upload_checkpoint WHERE endpoint=? AND tenant_id=? AND stream_id=?", key,
                ).fetchone()
                start = int(cursor[0]) if cursor else 0
                if cursor and {seq: event["eventHash"] for seq, event in events}.get(start) != cursor[1]:
                    raise ValueError("SQLite upload checkpoint no longer matches its source")
                pending = [(seq, event) for seq, event in events if seq > start]
                # Re-send the last acknowledged batch when attaching final evidence;
                # canonical cloud dedup prevents duplicate event writes.
                if not pending and run_evidence and events:
                    pending = events[-self._batch_size:]
                if not pending:
                    return {"written": 0, "duplicates": 0, "chain_head": previous,
                            "verification": "no_new_events", "stream_id": stream_id}
                written = duplicates = 0
                last_ack: dict[str, Any] = {}
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    for offset in range(0, len(pending), self._batch_size):
                        batch = pending[offset:offset + self._batch_size]
                        head = batch[-1][1]["eventHash"]
                        body: dict[str, Any] = {
                            "format": "tessera.audit.canonical.v1", "stream_id": stream_id,
                            "scope": tenant_id, "events": [{"seq": seq, "event": event} for seq, event in batch],
                            "chain_head": head,
                        }
                        if run_evidence and offset + self._batch_size >= len(pending):
                            body["run_evidence"] = run_evidence
                        response = await client.post(
                            f"{self._endpoint}{self.REMOTE_PATH}", json=body,
                            headers={"Authorization": f"Bearer {self._oauth_token}"},
                        )
                        response.raise_for_status()
                        ack = response.json()
                        submitted = [event["eventId"] for _, event in batch]
                        accepted = ack.get("accepted_event_ids") if isinstance(ack, dict) else None
                        if (not isinstance(accepted, list) or len(accepted) != len(submitted)
                                or set(accepted) != set(submitted) or ack.get("skipped", 0) != 0
                                or ack.get("tenant_id") != tenant_id or ack.get("stream_id") != stream_id
                                or ack.get("chain_head") != head or ack.get("verification") != "canonical_verified"
                                or type(ack.get("written")) is not int or type(ack.get("duplicates")) is not int
                                or ack["written"] < 0 or ack["duplicates"] < 0
                                or ack["written"] + ack["duplicates"] != len(batch)):
                            raise ValueError("cloud did not acknowledge the exact canonical audit batch")
                        if "run_evidence" in body and (
                                ack.get("run_evidence_stored") is not True
                                or ack.get("run_evidence_digest") != hashlib.sha256(canonical_json(run_evidence)).hexdigest()):
                            raise ValueError("cloud did not acknowledge the exact run evidence projection")
                        new_seq = max(start, batch[-1][0])
                        new_head = {seq: event["eventHash"] for seq, event in events}[new_seq]
                        conn.execute("INSERT OR REPLACE INTO cloud_upload_checkpoint VALUES (?,?,?,?,?)", (*key, new_seq, new_head))
                        conn.commit()
                        start = new_seq
                        written += ack["written"]
                        duplicates += ack["duplicates"]
                        last_ack = ack
                return {**last_ack, "written": written, "duplicates": duplicates}

    # ── Flush + retry ───────────────────────────────────────────────────────

    async def _flush_once_safe(self) -> None:
        """Single flush attempt — caps backoff, never raises."""
        try:
            await self._flush_once()
            self._backoff_s = _BACKOFF_INITIAL_S
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "event=audit_cloud_uploader_flush_failed reason=%s depth=%d backoff_s=%.1f",
                type(exc).__name__,
                len(self._queue),
                self._backoff_s,
            )
            await asyncio.sleep(self._backoff_s)
            self._backoff_s = min(_BACKOFF_MAX_S, self._backoff_s * 2)

    async def _flush_once(self) -> None:
        async with self._lock:
            if not self._queue:
                return
            if not self._oauth_token:
                raise RuntimeError(
                    "AuditCloudUploader.flush requires an oauth_token; "
                    "run `tessera login` first"
                )

            batch: list[dict[str, Any]] = []
            while self._queue and len(batch) < self._batch_size:
                batch.append(self._queue.popleft())

            chain_head = ""
            for evt in reversed(batch):
                head = evt.get("head_hash") or evt.get("this_hash") or evt.get("eventHash")
                if head:
                    chain_head = str(head)
                    break

            url = f"{self._endpoint}{self.REMOTE_PATH}"
            headers = {
                "Authorization": f"Bearer {self._oauth_token}",
                "Content-Type": "application/json",
            }
            body = {
                "events": batch,
                "chain_head": chain_head,
                "scope": self._upload_scope,
            }

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    ack = resp.json()
                    if not isinstance(ack, dict) or ack.get("written") != len(batch) or ack.get("skipped", 0):
                        raise ValueError("cloud skipped or did not acknowledge legacy audit events")
            except Exception:
                # Restore order: put batch back at the FRONT of the deque
                for evt in reversed(batch):
                    self._queue.appendleft(evt)
                raise

            logger.info(
                "event=audit_cloud_uploader_flush_ok written=%d depth=%d",
                len(batch),
                len(self._queue),
            )
