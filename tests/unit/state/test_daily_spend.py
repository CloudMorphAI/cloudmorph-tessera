"""Regression tests for DailySpendState persistence (P0-18).

These tests confirm that:
  1. add_spend() actually persists to disk (scan → record → re-scan → +N rows).
  2. Reopening the SQLite handle in a new DailySpendState instance still sees
     the accumulated total (restart-survivability).

The original P0-18 audit found zero production callers of `add_spend()` — the
proxy now wires it in `_record_daily_spend` post-allow, but the API itself must
also behave correctly under repeated use. These tests pin the persistence
contract so future refactors of `daily_spend.py` cannot silently break it.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from tessera.state.daily_spend import DailySpendState


def _row_count(db_path: Path) -> int:
    """Return the row count in daily_spend for a fresh SQLite connection.

    Uses a direct sqlite3 read (not DailySpendState) so the test isn't measuring
    only in-memory state — it asserts the data is actually on disk.
    """
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT COUNT(*) FROM daily_spend")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def test_add_spend_persists_to_disk():
    """Scan spend → record event → re-scan: row count + total grow as expected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        state = DailySpendState(state_dir=state_dir)
        db_path = state_dir / "daily_spend.db"

        # Step 1: initial scan — backend just created, no rows yet.
        before = _row_count(db_path)
        assert before == 0
        assert state.get_today_spend("alice") == pytest.approx(0.0)

        # Step 2: record a new spend event.
        state.add_spend("alice", 7.50)

        # Step 3: re-scan and confirm the row landed on disk + the in-process
        # backend exposes the accumulated value via get_today_spend.
        after = _row_count(db_path)
        assert after == 1
        assert state.get_today_spend("alice") == pytest.approx(7.50)

        # Add a second spend for the same (scope, day) — must still be 1 row
        # (PRIMARY KEY upsert), with cumulative_usd = 7.50 + 2.25.
        state.add_spend("alice", 2.25)
        assert _row_count(db_path) == 1
        assert state.get_today_spend("alice") == pytest.approx(9.75)

        state.close()


def test_add_spend_survives_reopen():
    """Reopen the SQLite handle in a fresh DailySpendState — totals must persist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)

        state1 = DailySpendState(state_dir=state_dir)
        state1.add_spend("alice", 12.34)
        state1.add_spend("bob", 0.05)
        state1.close()

        # Reopen — simulates a process restart.
        state2 = DailySpendState(state_dir=state_dir)
        assert state2.get_today_spend("alice") == pytest.approx(12.34)
        assert state2.get_today_spend("bob") == pytest.approx(0.05)
        state2.close()


def test_add_spend_multiple_scopes_isolated():
    """Concurrent scopes do not bleed into each other."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = DailySpendState(state_dir=Path(tmpdir))
        for scope, amount in [
            ("tenant_a", 1.0),
            ("tenant_b", 2.0),
            ("tenant_a", 0.5),
            ("tenant_c", 99.99),
        ]:
            state.add_spend(scope, amount)
        assert state.get_today_spend("tenant_a") == pytest.approx(1.5)
        assert state.get_today_spend("tenant_b") == pytest.approx(2.0)
        assert state.get_today_spend("tenant_c") == pytest.approx(99.99)
        state.close()


def test_add_spend_zero_is_idempotent():
    """add_spend(0) does not skip the INSERT (regression guard for upserts)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        state = DailySpendState(state_dir=state_dir)
        db_path = state_dir / "daily_spend.db"

        state.add_spend("alice", 0.0)
        assert _row_count(db_path) == 1
        assert state.get_today_spend("alice") == pytest.approx(0.0)

        state.add_spend("alice", 5.0)
        assert _row_count(db_path) == 1
        assert state.get_today_spend("alice") == pytest.approx(5.0)

        state.close()
