"""Helper functions for audit log inspection CLI subcommands."""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Iterator
from typing import Any

from tessera.audit.sinks.sqlite import SqliteSink


def tail_events(
    sink: SqliteSink,
    scope: str | None = None,
    limit: int = 20,
    follow: bool = False,
    poll_interval: float = 1.0,
) -> Iterator[dict[str, Any]]:
    """Yield the most recent N events; if follow=True, poll for new ones continuously.

    When follow is False, yields exactly up to `limit` events and returns.
    When follow is True, yields the initial window then polls every poll_interval
    seconds for new events, yielding any that appear after the last seen seq.
    """
    seen_event_ids: set[str] = set()

    # Initial window
    for event in sink.iter_recent(scope=scope, limit=limit):
        seen_event_ids.add(event.get("eventId", ""))
        yield event

    if not follow:
        return

    # Poll loop
    while True:
        time.sleep(poll_interval)
        for event in sink.iter_recent(scope=scope, limit=limit):
            eid = event.get("eventId", "")
            if eid and eid not in seen_event_ids:
                seen_event_ids.add(eid)
                yield event


def fetch_event_by_id(sink: SqliteSink, event_id: str) -> dict[str, Any] | None:
    """Return a single event by eventId, or None if not found."""
    return sink.fetch_by_id(event_id)


def export_jsonl(
    sink: SqliteSink,
    scope: str | None = None,
) -> Iterator[str]:
    """Yield one JSON line per event (full payload), in sequence order."""
    for event in sink.iter_events(scope=scope):
        yield json.dumps(event, ensure_ascii=False)


def export_csv(
    sink: SqliteSink,
    scope: str | None = None,
    max_cell_bytes: int = 4096,
) -> Iterator[str]:
    """Yield CSV rows (header first) for the audit log.

    Columns: event_id, scope, event_type, occurred_at, prev_event_hash,
    event_hash, payload.  The payload column contains JSON-stringified payload;
    string values exceeding max_cell_bytes are truncated to keep CSV consumers
    from choking on very large cells.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    # Header
    writer.writerow(
        ["event_id", "scope", "event_type", "occurred_at",
         "prev_event_hash", "event_hash", "payload"]
    )
    buf.seek(0)
    yield buf.getvalue()
    buf.truncate(0)
    buf.seek(0)

    for event in sink.iter_events(scope=scope):
        payload_str = json.dumps(event.get("payload", {}), ensure_ascii=False)
        if len(payload_str.encode("utf-8")) > max_cell_bytes:
            payload_str = payload_str[:max_cell_bytes] + "...<truncated>"

        writer.writerow([
            event.get("eventId", ""),
            event.get("tenantId", ""),
            event.get("eventType", ""),
            event.get("occurredAt", ""),
            event.get("prevEventHash", ""),
            event.get("eventHash", ""),
            payload_str,
        ])
        buf.seek(0)
        yield buf.getvalue()
        buf.truncate(0)
        buf.seek(0)
