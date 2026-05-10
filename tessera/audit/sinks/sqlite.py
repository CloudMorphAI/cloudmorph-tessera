"""SQLite audit sink — default persistence layer."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from tessera.audit.sinks.base import AuditSink  # noqa: F401 (for isinstance checks)
from tessera.errors import AuditSinkError

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
  event_id        TEXT    PRIMARY KEY,
  scope           TEXT    NOT NULL,
  seq             INTEGER NOT NULL,
  event_type      TEXT    NOT NULL,
  occurred_at     TEXT    NOT NULL,
  payload_json    TEXT    NOT NULL,
  prev_event_hash TEXT    NOT NULL,
  event_hash      TEXT    NOT NULL UNIQUE,
  schema_version  TEXT    NOT NULL
);
"""

_CREATE_IDX_SCOPE_SEQ = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_scope_seq
  ON audit_events (scope, seq);
"""

_CREATE_IDX_SCOPE_OCCURRED_AT = """
CREATE INDEX IF NOT EXISTS idx_audit_scope_occurred_at
  ON audit_events (scope, occurred_at);
"""

_SEQ_QUERY = "SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events WHERE scope = ?"

_INSERT = """
INSERT INTO audit_events
  (event_id, scope, seq, event_type, occurred_at, payload_json,
   prev_event_hash, event_hash, schema_version)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_HEAD_HASH = (
    "SELECT event_hash FROM audit_events WHERE scope=? ORDER BY seq DESC LIMIT 1"
)

_ITER_ALL = (
    "SELECT payload_json FROM audit_events ORDER BY scope, seq"
)

_ITER_SCOPED = (
    "SELECT payload_json FROM audit_events WHERE scope=? ORDER BY scope, seq"
)


class SqliteSink:
    name: str = "sqlite"

    def __init__(self, path: str | Path = "/var/lib/tessera/audit.db") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_IDX_SCOPE_SEQ)
        self._conn.execute(_CREATE_IDX_SCOPE_OCCURRED_AT)
        self._conn.commit()

    def emit(self, event: dict[str, Any]) -> None:
        """Store event. seq = MAX(seq WHERE scope=?)+1 per scope."""
        event_id = event["eventId"]
        scope = event.get("tenantId", "default")
        event_type = event.get("eventType", "unknown")
        occurred_at = event.get("occurredAt", "")
        prev_event_hash = event.get("prevEventHash", "")
        event_hash = event["eventHash"]
        schema_version = event.get("schemaVersion", "v0.1")
        payload_json = json.dumps(event)

        try:
            with self._lock:
                with self._conn:
                    row = self._conn.execute(_SEQ_QUERY, (scope,)).fetchone()
                    seq = row[0]
                    self._conn.execute(
                        _INSERT,
                        (
                            event_id,
                            scope,
                            seq,
                            event_type,
                            occurred_at,
                            payload_json,
                            prev_event_hash,
                            event_hash,
                            schema_version,
                        ),
                    )
        except sqlite3.Error as exc:
            raise AuditSinkError(str(exc)) from exc

    def close(self) -> None:
        """Close the connection."""
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def head_hash(self, scope: str) -> str:
        """Return the latest event_hash for the given scope, or '' if none."""
        row = self._conn.execute(_HEAD_HASH, (scope,)).fetchone()
        return row["event_hash"] if row else ""

    def iter_events(self, scope: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield events ordered by (scope, seq). If scope given, filter to that scope."""
        if scope is None:
            cursor = self._conn.execute(_ITER_ALL)
        else:
            cursor = self._conn.execute(_ITER_SCOPED, (scope,))
        for row in cursor:
            yield json.loads(row["payload_json"])
