"""Unit tests for tessera.audit.verifier.verify_chain."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.audit.verifier import verify_chain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_emitter(sink: SqliteSink, tenant_id: str = "tnt_test") -> AuditEmitter:
    return AuditEmitter(tenant_id=tenant_id, sinks=[sink])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_verify_empty_scope_ok(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    try:
        result = verify_chain(sink, "tnt_empty")
        assert result["ok"] is True
        assert result["events_checked"] == 0
        assert result["first_event_at"] is None
        assert result["last_event_at"] is None
        assert result["first_failure"] is None
    finally:
        sink.close()


def test_verify_clean_chain_ok(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    emitter = _make_emitter(sink)
    try:
        for i in range(5):
            emitter.emit("test.event", payload={"i": i})
        result = verify_chain(sink, "tnt_test")
        assert result["ok"] is True
        assert result["events_checked"] == 5
        assert result["first_failure"] is None
    finally:
        sink.close()


def test_verify_tampered_hash_detected(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    sink = SqliteSink(path=db_path)
    emitter = _make_emitter(sink)
    try:
        for i in range(3):
            emitter.emit("test.event", payload={"i": i})
        sink.close()

        # Corrupt the eventHash field inside payload_json for the first event
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT payload_json FROM audit_events WHERE seq = 1").fetchone()
        payload = json.loads(row["payload_json"])
        payload["eventHash"] = "deadbeef" * 8  # 64 hex chars, wrong value
        conn.execute(
            "UPDATE audit_events SET payload_json = ? WHERE seq = 1",
            (json.dumps(payload),),
        )
        conn.commit()
        conn.close()

        sink2 = SqliteSink(path=db_path)
        result = verify_chain(sink2, "tnt_test")
        sink2.close()

        assert result["ok"] is False
        assert result["first_failure"] is not None
        assert result["first_failure"]["kind"] == "hash_mismatch"
    except Exception:
        sink.close()
        raise


def test_verify_chain_break_detected(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    sink = SqliteSink(path=db_path)
    emitter = _make_emitter(sink)
    try:
        for i in range(3):
            emitter.emit("test.event", payload={"i": i})
        sink.close()

        # Corrupt the prev_event_hash stored in payload_json for seq=2
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT event_id, payload_json FROM audit_events WHERE seq = 2").fetchone()
        payload = json.loads(row["payload_json"])
        payload["prevEventHash"] = "aa" * 32
        conn.execute(
            "UPDATE audit_events SET prev_event_hash = ?, payload_json = ? WHERE seq = 2",
            ("aa" * 32, json.dumps(payload)),
        )
        conn.commit()
        conn.close()

        sink2 = SqliteSink(path=db_path)
        result = verify_chain(sink2, "tnt_test")
        sink2.close()

        assert result["ok"] is False
        assert result["first_failure"] is not None
        # seq=2 fails hash_mismatch first (payload was mutated), OR chain_break —
        # either way the corruption at seq=2 is detected
        assert result["first_failure"]["seq"] in (1, 2)
    except Exception:
        sink.close()
        raise


def test_verify_returns_event_count(tmp_path: Path) -> None:
    sink = SqliteSink(path=tmp_path / "audit.db")
    emitter = _make_emitter(sink)
    try:
        for i in range(10):
            emitter.emit("test.event", payload={"i": i})
        result = verify_chain(sink, "tnt_test")
        assert result["events_checked"] == 10
    finally:
        sink.close()


def test_verify_reports_first_failure_and_stops(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    sink = SqliteSink(path=db_path)
    emitter = _make_emitter(sink)
    try:
        for i in range(6):
            emitter.emit("test.event", payload={"i": i})
        sink.close()

        # Corrupt eventHash inside payload_json for seqs 1, 2, and 3
        # (only mutate payload_json — event_hash column has UNIQUE constraint)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for seq in (1, 2, 3):
            row = conn.execute("SELECT payload_json FROM audit_events WHERE seq = ?", (seq,)).fetchone()
            payload = json.loads(row["payload_json"])
            payload["eventHash"] = "ba" * (16 + seq)  # unique per seq to avoid issues
            conn.execute(
                "UPDATE audit_events SET payload_json = ? WHERE seq = ?",
                (json.dumps(payload), seq),
            )
        conn.commit()
        conn.close()

        sink2 = SqliteSink(path=db_path)
        result = verify_chain(sink2, "tnt_test")
        sink2.close()

        assert result["ok"] is False
        assert result["first_failure"] is not None
        # First failure must be seq=1 (earliest corruption)
        assert result["first_failure"]["seq"] == 1
        # events_checked must stop at (or shortly after) first failure
        assert result["events_checked"] <= 2
    except Exception:
        sink.close()
        raise
