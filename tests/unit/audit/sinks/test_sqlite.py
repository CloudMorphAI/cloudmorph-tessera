"""Tests for SqliteSink."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tessera.audit.sinks.sqlite import SqliteSink
from tessera.errors import AuditSinkError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_id: str,
    *,
    scope: str = "tenant-a",
    event_hash: str | None = None,
    prev_event_hash: str = "",
) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "tenantId": scope,
        "eventType": "test.action",
        "occurredAt": "2026-05-10T00:00:00Z",
        "prevEventHash": prev_event_hash,
        "eventHash": event_hash if event_hash is not None else f"hash-{event_id}",
        "schemaVersion": "v0.1",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_schema_created(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    sink = SqliteSink(path=db_path)
    cur = sink._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
    )
    assert cur.fetchone() is not None
    sink.close()


def test_emit_and_retrieve_round_trip(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    event = _make_event("evt-1")
    sink.emit(event)
    events = list(sink.iter_events())
    assert len(events) == 1
    assert events[0]["eventId"] == "evt-1"
    sink.close()


def test_emit_seq_increments_per_scope(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    for i in range(1, 4):
        sink.emit(_make_event(f"evt-{i}", scope="s1", event_hash=f"h{i}"))
    rows = sink._conn.execute(
        "SELECT seq FROM audit_events WHERE scope='s1' ORDER BY seq"
    ).fetchall()
    assert [r["seq"] for r in rows] == [1, 2, 3]
    sink.close()


def test_concurrent_emits_no_collision(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    errors: list[Exception] = []

    def emit_batch(thread_id: int) -> None:
        for i in range(20):
            try:
                sink.emit(
                    _make_event(
                        f"t{thread_id}-e{i}",
                        scope="shared",
                        event_hash=f"h-{thread_id}-{i}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t1 = threading.Thread(target=emit_batch, args=(1,))
    t2 = threading.Thread(target=emit_batch, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    rows = sink._conn.execute(
        "SELECT seq FROM audit_events WHERE scope='shared'"
    ).fetchall()
    assert len(rows) == 40
    seqs = {r["seq"] for r in rows}
    assert len(seqs) == 40  # all unique
    sink.close()


def test_head_hash_returns_latest(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    for i in range(1, 4):
        sink.emit(_make_event(f"evt-{i}", scope="s1", event_hash=f"hash-{i}"))
    assert sink.head_hash("s1") == "hash-3"
    sink.close()


def test_head_hash_empty_for_unknown_scope(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    assert sink.head_hash("no-such") == ""
    sink.close()


def test_iter_events_ordering(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    sink.emit(_make_event("a1", scope="scope-a", event_hash="ha1"))
    sink.emit(_make_event("b1", scope="scope-b", event_hash="hb1"))
    sink.emit(_make_event("a2", scope="scope-a", event_hash="ha2"))
    sink.emit(_make_event("b2", scope="scope-b", event_hash="hb2"))

    events = list(sink.iter_events())
    assert len(events) == 4
    # scope-a rows first (alphabetical scope), then scope-b
    assert events[0]["tenantId"] == "scope-a"
    assert events[1]["tenantId"] == "scope-a"
    assert events[2]["tenantId"] == "scope-b"
    assert events[3]["tenantId"] == "scope-b"
    # within each scope, seq order preserved
    assert events[0]["eventId"] == "a1"
    assert events[1]["eventId"] == "a2"
    sink.close()


def test_iter_events_scope_filter(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    sink.emit(_make_event("a1", scope="scope-a", event_hash="ha1"))
    sink.emit(_make_event("b1", scope="scope-b", event_hash="hb1"))

    events = list(sink.iter_events("scope-a"))
    assert len(events) == 1
    assert events[0]["eventId"] == "a1"
    sink.close()


def test_idempotent_create(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    sink1 = SqliteSink(path=db_path)
    sink1.close()
    # Second open on same path should not raise
    sink2 = SqliteSink(path=db_path)
    sink2.close()


def test_emit_raises_audit_sink_error_on_duplicate_event_id(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    event = _make_event("dup-id", event_hash="hash-unique-1")
    sink.emit(event)
    duplicate = _make_event("dup-id", event_hash="hash-unique-2")
    with pytest.raises(AuditSinkError):
        sink.emit(duplicate)
    sink.close()


def test_close_idempotent(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    sink.close()
    sink.close()  # second call must not raise
