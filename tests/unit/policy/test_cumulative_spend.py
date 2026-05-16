"""Unit tests for cumulative_spend_today condition + DailySpendState."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import CumulativeSpendToday
from tessera.state.daily_spend import DailySpendState

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(state_backend=None, scope: str = "alice") -> dict:
    return {
        "tool_call": {"name": "aws_ec2_RunInstances", "arguments": {}, "_meta": None},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "state_backend": state_backend,
        "scope": scope,
    }


# ── DailySpendState tests ─────────────────────────────────────────────────────


def test_daily_spend_state_add_and_get():
    """add_spend then get_today_spend returns accumulated value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = DailySpendState(state_dir=Path(tmpdir))
        state.add_spend("alice", 1.50)
        state.add_spend("alice", 2.75)
        total = state.get_today_spend("alice")
        state.close()
    assert total == pytest.approx(4.25)


def test_daily_spend_state_scopes_are_isolated():
    """Spend in scope 'alice' does not appear in scope 'bob'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = DailySpendState(state_dir=Path(tmpdir))
        state.add_spend("alice", 10.00)
        state.add_spend("bob", 5.00)
        alice_total = state.get_today_spend("alice")
        bob_total = state.get_today_spend("bob")
        state.close()
    assert alice_total == pytest.approx(10.00)
    assert bob_total == pytest.approx(5.00)


def test_daily_spend_state_utc_midnight_rollover():
    """Spend recorded on a previous UTC day does NOT appear in today's total."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = DailySpendState(state_dir=Path(tmpdir))
        yesterday = datetime(2026, 5, 12, 23, 59, 0, tzinfo=UTC)
        state.add_spend("alice", 50.00, occurred_at=yesterday)
        # get_today_spend uses current UTC date; yesterday's spend should not count
        today_total = state.get_today_spend("alice")
        state.close()
    # Today is not 2026-05-12 (it's 2026-05-13 per MEMORY), so total should be 0
    assert today_total == pytest.approx(0.00)


# ── CumulativeSpendToday condition tests ──────────────────────────────────────


def test_cumulative_spend_over_threshold_blocks():
    """Cumulative spend > threshold → True."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=50.00,
        operator="greater_than",
    )
    state = MagicMock()
    state.get_today_spend.return_value = 75.00
    assert evaluate_condition(cond, _ctx(state_backend=state)) is True


def test_cumulative_spend_under_threshold_allows():
    """Cumulative spend < threshold → False."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=100.00,
        operator="greater_than",
    )
    state = MagicMock()
    state.get_today_spend.return_value = 10.00
    assert evaluate_condition(cond, _ctx(state_backend=state)) is False


def test_missing_state_backend_fails_closed_allow():
    """Missing state_backend → fail-closed (False = don't block)."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=1.00,
        operator="greater_than",
    )
    assert evaluate_condition(cond, _ctx(state_backend=None)) is False


def test_operator_less_than():
    """operator=less_than: spend 5 < threshold 100 → True."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=100.00,
        operator="less_than",
    )
    state = MagicMock()
    state.get_today_spend.return_value = 5.00
    assert evaluate_condition(cond, _ctx(state_backend=state)) is True


def test_scope_passed_to_state_backend():
    """The scope from context is passed to get_today_spend."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=10.00,
        operator="greater_than",
    )
    state = MagicMock()
    state.get_today_spend.return_value = 20.00
    ctx = _ctx(state_backend=state, scope="org_acme")
    evaluate_condition(cond, ctx)
    state.get_today_spend.assert_called_once_with("org_acme")


def test_state_backend_exception_fails_closed():
    """Exception from state_backend → fail-closed (False)."""
    cond = CumulativeSpendToday(
        condition="cumulative_spend_today",
        usd_threshold=1.00,
        operator="greater_than",
    )
    state = MagicMock()
    state.get_today_spend.side_effect = RuntimeError("db locked")
    assert evaluate_condition(cond, _ctx(state_backend=state)) is False
