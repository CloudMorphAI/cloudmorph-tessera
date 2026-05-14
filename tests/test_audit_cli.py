"""Minimal tests for audit inspect helper functions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tessera.audit.inspect import (
    export_csv,
    export_jsonl,
    fetch_event_by_id,
    tail_events,
)
from tessera.audit.sinks.sqlite import SqliteSink


def _make_event(event_id: str, scope: str = "default", seq: int = 1) -> dict:
    # Derive a unique eventHash per event so fixtures with multiple events
    # don't collide on the UNIQUE constraint in audit_events.event_hash.
    event_hash = hashlib.sha256(event_id.encode()).hexdigest()
    return {
        "schemaVersion": "v0.1",
        "eventId": event_id,
        "tenantId": scope,
        "eventType": "decision",
        "occurredAt": "2026-05-14T00:00:00Z",
        "prevEventHash": "",
        "eventHash": event_hash,
        "payload": {"decision": "allow"},
    }


def _populated_sink(tmp_path: Path) -> SqliteSink:
    db = str(tmp_path / "audit.db")
    sink = SqliteSink(db)
    e1 = _make_event("evt_001", seq=1)
    e2 = _make_event("evt_002", seq=2)
    # Emit directly so we don't need the full hash chain
    import sqlite3

    conn = sqlite3.connect(db)
    for i, ev in enumerate([e1, e2], start=1):
        conn.execute(
            """INSERT INTO audit_events
               (event_id, scope, seq, event_type, occurred_at, payload_json,
                prev_event_hash, event_hash, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev["eventId"],
                ev["tenantId"],
                i,
                ev["eventType"],
                ev["occurredAt"],
                json.dumps(ev),
                ev["prevEventHash"],
                ev["eventHash"],
                ev["schemaVersion"],
            ),
        )
    conn.commit()
    conn.close()
    return sink


def test_imports() -> None:
    assert callable(tail_events)
    assert callable(export_jsonl)
    assert callable(export_csv)
    assert callable(fetch_event_by_id)


def test_tail_events(tmp_path: Path) -> None:
    sink = _populated_sink(tmp_path)
    events = list(tail_events(sink, scope="default", limit=10, follow=False))
    assert len(events) == 2
    sink.close()


def test_fetch_event_by_id(tmp_path: Path) -> None:
    sink = _populated_sink(tmp_path)
    ev = fetch_event_by_id(sink, "evt_001")
    assert ev is not None
    assert ev["eventId"] == "evt_001"
    missing = fetch_event_by_id(sink, "evt_999")
    assert missing is None
    sink.close()


def test_export_jsonl(tmp_path: Path) -> None:
    sink = _populated_sink(tmp_path)
    lines = list(export_jsonl(sink, scope="default"))
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert "eventId" in parsed
    sink.close()


def test_export_csv(tmp_path: Path) -> None:
    sink = _populated_sink(tmp_path)
    rows = list(export_csv(sink, scope="default"))
    # First row is header, then one per event
    assert len(rows) == 3
    assert "event_id" in rows[0]
    sink.close()
