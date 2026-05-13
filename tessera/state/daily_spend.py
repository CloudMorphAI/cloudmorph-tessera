"""SQLite-backed daily spend tracking for cumulative_spend_today condition."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path


class DailySpendState:
    """Thread-safe SQLite store for per-scope daily USD spend totals.

    Schema:
        CREATE TABLE daily_spend (
            scope TEXT,
            day   TEXT,          -- ISO date YYYY-MM-DD, UTC
            cumulative_usd REAL,
            PRIMARY KEY(scope, day)
        )
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS daily_spend (
        scope          TEXT    NOT NULL,
        day            TEXT    NOT NULL,
        cumulative_usd REAL    NOT NULL DEFAULT 0.0,
        PRIMARY KEY (scope, day)
    )
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        if state_dir is None:
            state_dir = Path.home() / ".tessera" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / "daily_spend.db"
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute(self._SCHEMA)
            self._conn.commit()

    @staticmethod
    def _today_str(occurred_at: datetime | None = None) -> str:
        dt = occurred_at or datetime.now(UTC)
        # Normalise to UTC
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC)
        return dt.strftime("%Y-%m-%d")

    def add_spend(self, scope: str, usd: float, occurred_at: datetime | None = None) -> None:
        """Add usd to the daily total for scope on the given day (UTC)."""
        day = self._today_str(occurred_at)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO daily_spend (scope, day, cumulative_usd)
                VALUES (?, ?, ?)
                ON CONFLICT(scope, day) DO UPDATE
                    SET cumulative_usd = cumulative_usd + excluded.cumulative_usd
                """,
                (scope, day, usd),
            )
            self._conn.commit()

    def get_today_spend(self, scope: str) -> float:
        """Return the cumulative USD spend for scope today (UTC). Returns 0.0 if no rows."""
        day = self._today_str()
        with self._lock:
            row = self._conn.execute(
                "SELECT cumulative_usd FROM daily_spend WHERE scope = ? AND day = ?",
                (scope, day),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
