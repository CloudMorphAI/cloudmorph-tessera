"""Unit tests for tessera.policy.regex_safety."""

from __future__ import annotations

import pytest

from tessera.errors import PolicyError
from tessera.policy.regex_safety import validate_pattern


def test_benign_pattern_passes() -> None:
    """Simple digit pattern should pass all corpus strings quickly."""
    validate_pattern(r"\d+")  # should not raise


def test_alphanumeric_pattern_passes() -> None:
    """Word-character class should pass corpus safely."""
    validate_pattern(r"[a-zA-Z0-9_\-\.]+")  # should not raise


def test_long_but_fast_pattern_passes() -> None:
    """Realistic policy pattern should pass all corpus strings."""
    validate_pattern(r"^(arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:[0-9]{12}:.+)$")


def test_invalid_syntax_raises_policy_error() -> None:
    """Pattern with unbalanced bracket raises PolicyError with reason=regex_invalid."""
    with pytest.raises(PolicyError) as exc_info:
        validate_pattern(r"[unclosed")
    assert exc_info.value.reason == "regex_invalid"


def test_invalid_syntax_reason() -> None:
    """Ensure the reason string is set correctly for unbalanced parenthesis."""
    with pytest.raises(PolicyError) as exc_info:
        validate_pattern(r"(unclosed")
    assert exc_info.value.reason == "regex_invalid"


def test_known_redos_pattern_rejected() -> None:
    """Catastrophic-backtracking pattern must be rejected at load time.

    ([a-z0-9]+\\s*)+ on a long string without a matching end reliably triggers
    exponential backtracking in VERSION1 mode, exceeding the 100ms hard timeout
    on typical hardware.
    """
    with pytest.raises(PolicyError) as exc_info:
        validate_pattern(r"([a-z0-9]+\s*)+$")
    assert exc_info.value.reason == "regex_potential_redos"


def test_known_redos_pattern_message_mentions_corpus() -> None:
    """Error message should reference either timeout or elapsed time."""
    with pytest.raises(PolicyError) as exc_info:
        validate_pattern(r"([a-z0-9]+\s*)+$")
    msg = str(exc_info.value)
    # Either timed out or exceeded soft cap
    assert "timed out" in msg or "ms" in msg
