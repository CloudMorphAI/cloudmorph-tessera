"""Unit tests for tessera.policy.conditions."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tessera.policy.conditions import (
    clear_decision_errors,
    evaluate_condition,
    evaluate_conditions,
    get_decision_errors,
)
from tessera.policy.schema import (
    ActionClassIn,
    AnyOf,
    ArgContainsPattern,
    ArgEquals,
    ArgGreaterThan,
    ArgInSet,
    ArgLessThan,
    ArgMatchesRegex,
    ArgSizeGreaterThan,
    IntentClassIn,
    IntentPurposeMatches,
    MetaFieldEquals,
    NoneOf,
    RegionIn,
    TimeOfDayOutside,
    ToolNameIn,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(
    *,
    name: str = "test_tool",
    arguments: dict | None = None,
    meta: dict | None = None,
    intent: dict | None = None,
    upstream: str = "aws",
    lockdown: bool = False,
    policy_id: str | None = None,
) -> dict:
    return {
        "tool_call": {
            "name": name,
            "arguments": arguments or {},
            "_meta": meta,
        },
        "intent": intent,
        "upstream": upstream,
        "runtime": {"lockdown": lockdown},
        "policy_id": policy_id,
    }


# ── ArgEquals ─────────────────────────────────────────────────────────────────


def test_arg_equals_true() -> None:
    cond = ArgEquals(condition="arg_equals", arg="region", value="us-east-1")
    assert evaluate_condition(cond, _ctx(arguments={"region": "us-east-1"})) is True


def test_arg_equals_false() -> None:
    cond = ArgEquals(condition="arg_equals", arg="region", value="us-east-1")
    assert evaluate_condition(cond, _ctx(arguments={"region": "eu-west-1"})) is False


def test_arg_equals_missing_arg() -> None:
    cond = ArgEquals(condition="arg_equals", arg="region", value="us-east-1")
    assert evaluate_condition(cond, _ctx(arguments={})) is False


# ── ArgGreaterThan ────────────────────────────────────────────────────────────


def test_arg_greater_than_true() -> None:
    cond = ArgGreaterThan(condition="arg_greater_than", arg="count", value=5)
    assert evaluate_condition(cond, _ctx(arguments={"count": 10})) is True


def test_arg_greater_than_false() -> None:
    cond = ArgGreaterThan(condition="arg_greater_than", arg="count", value=5)
    assert evaluate_condition(cond, _ctx(arguments={"count": 3})) is False


def test_arg_greater_than_non_numeric() -> None:
    cond = ArgGreaterThan(condition="arg_greater_than", arg="count", value=5)
    assert evaluate_condition(cond, _ctx(arguments={"count": "not-a-number"})) is False


# ── ArgLessThan ───────────────────────────────────────────────────────────────


def test_arg_less_than_true() -> None:
    cond = ArgLessThan(condition="arg_less_than", arg="count", value=10)
    assert evaluate_condition(cond, _ctx(arguments={"count": 3})) is True


def test_arg_less_than_false() -> None:
    cond = ArgLessThan(condition="arg_less_than", arg="count", value=10)
    assert evaluate_condition(cond, _ctx(arguments={"count": 15})) is False


# ── ArgMatchesRegex ───────────────────────────────────────────────────────────


def test_arg_matches_regex_match() -> None:
    cond = ArgMatchesRegex(condition="arg_matches_regex", arg="bucket", pattern=r"^prod-")
    assert evaluate_condition(cond, _ctx(arguments={"bucket": "prod-data"})) is True


def test_arg_matches_regex_no_match() -> None:
    cond = ArgMatchesRegex(condition="arg_matches_regex", arg="bucket", pattern=r"^prod-")
    assert evaluate_condition(cond, _ctx(arguments={"bucket": "dev-data"})) is False


def test_arg_matches_regex_missing_arg() -> None:
    cond = ArgMatchesRegex(condition="arg_matches_regex", arg="bucket", pattern=r"^prod-")
    assert evaluate_condition(cond, _ctx(arguments={})) is False


def test_arg_matches_regex_wildcard_arg() -> None:
    """arg="*": True if any argument value matches the pattern."""
    cond = ArgMatchesRegex(condition="arg_matches_regex", arg="*", pattern=r"^prod-")
    assert evaluate_condition(
        cond, _ctx(arguments={"bucket": "prod-data", "region": "us-east-1"})
    ) is True
    assert evaluate_condition(
        cond, _ctx(arguments={"bucket": "dev-data", "region": "us-east-1"})
    ) is False


# ── ArgInSet ──────────────────────────────────────────────────────────────────


def test_arg_in_set_match() -> None:
    cond = ArgInSet(condition="arg_in_set", arg="env", values=["prod", "staging"])
    assert evaluate_condition(cond, _ctx(arguments={"env": "prod"})) is True


def test_arg_in_set_no_match() -> None:
    cond = ArgInSet(condition="arg_in_set", arg="env", values=["prod", "staging"])
    assert evaluate_condition(cond, _ctx(arguments={"env": "dev"})) is False


def test_arg_in_set_wildcard() -> None:
    """arg="*": True if any argument value is in the set."""
    cond = ArgInSet(condition="arg_in_set", arg="*", values=["prod", "staging"])
    assert evaluate_condition(
        cond, _ctx(arguments={"env": "prod", "region": "us-east-1"})
    ) is True
    assert evaluate_condition(
        cond, _ctx(arguments={"env": "dev", "region": "us-east-1"})
    ) is False


# ── ArgContainsPattern (alias of arg_matches_regex) ───────────────────────────


def test_arg_contains_pattern_alias() -> None:
    cond_regex = ArgMatchesRegex(condition="arg_matches_regex", arg="key", pattern=r"secret")
    cond_contains = ArgContainsPattern(condition="arg_contains_pattern", arg="key", pattern=r"secret")
    ctx = _ctx(arguments={"key": "my-secret-value"})
    assert evaluate_condition(cond_regex, ctx) == evaluate_condition(cond_contains, ctx)
    assert evaluate_condition(cond_contains, ctx) is True


# ── ArgSizeGreaterThan ────────────────────────────────────────────────────────


def test_arg_size_greater_than_true() -> None:
    cond = ArgSizeGreaterThan(condition="arg_size_greater_than", arg="payload", bytes=10)
    # "a" * 20 → json.dumps gives "\"" + "a"*20 + "\"" = 22 chars
    assert evaluate_condition(cond, _ctx(arguments={"payload": "a" * 20})) is True


def test_arg_size_greater_than_false() -> None:
    cond = ArgSizeGreaterThan(condition="arg_size_greater_than", arg="payload", bytes=100)
    assert evaluate_condition(cond, _ctx(arguments={"payload": "tiny"})) is False


# ── ToolNameIn ────────────────────────────────────────────────────────────────


def test_tool_name_in_true() -> None:
    cond = ToolNameIn(condition="tool_name_in", values=["aws_s3_delete_object", "aws_s3_delete_bucket"])
    assert evaluate_condition(cond, _ctx(name="aws_s3_delete_object")) is True


def test_tool_name_in_false() -> None:
    cond = ToolNameIn(condition="tool_name_in", values=["aws_s3_delete_object"])
    assert evaluate_condition(cond, _ctx(name="aws_s3_list_buckets")) is False


# ── ActionClassIn ─────────────────────────────────────────────────────────────


def test_action_class_in_known_tool() -> None:
    cond = ActionClassIn(condition="action_class_in", values=["write.delete"])
    # aws.s3.delete_object maps to write.delete
    assert evaluate_condition(cond, _ctx(name="aws.s3.delete_object")) is True


def test_action_class_in_unknown_tool() -> None:
    cond = ActionClassIn(condition="action_class_in", values=["write.delete"])
    # unknown tool has empty verb set
    assert evaluate_condition(cond, _ctx(name="unknown_custom_tool")) is False


# ── IntentClassIn ─────────────────────────────────────────────────────────────


def test_intent_class_in_with_intent() -> None:
    cond = IntentClassIn(condition="intent_class_in", values=["read.list"])
    ctx = _ctx(intent={"verbs": ["read.list", "read.describe"]})
    assert evaluate_condition(cond, ctx) is True


def test_intent_class_in_without_intent() -> None:
    cond = IntentClassIn(condition="intent_class_in", values=["read.list"])
    assert evaluate_condition(cond, _ctx(intent=None)) is False


# ── IntentPurposeMatches ──────────────────────────────────────────────────────


def test_intent_purpose_matches_true() -> None:
    cond = IntentPurposeMatches(condition="intent_purpose_matches", pattern=r"cost.attribution")
    ctx = _ctx(intent={"verbs": ["read.list"], "purpose": "Counting objects for cost-attribution report."})
    assert evaluate_condition(cond, ctx) is True


def test_intent_purpose_matches_false() -> None:
    cond = IntentPurposeMatches(condition="intent_purpose_matches", pattern=r"delete all data")
    ctx = _ctx(intent={"verbs": ["read.list"], "purpose": "Count objects."})
    assert evaluate_condition(cond, ctx) is False


def test_intent_purpose_matches_no_intent() -> None:
    cond = IntentPurposeMatches(condition="intent_purpose_matches", pattern=r".*")
    assert evaluate_condition(cond, _ctx(intent=None)) is False


# ── RegionIn ──────────────────────────────────────────────────────────────────


def test_region_in_true() -> None:
    cond = RegionIn(condition="region_in", arg="region", regions=["us-east", "eu-west"])
    assert evaluate_condition(cond, _ctx(arguments={"region": "us-east-1"})) is True


def test_region_in_false() -> None:
    cond = RegionIn(condition="region_in", arg="region", regions=["us-east", "eu-west"])
    assert evaluate_condition(cond, _ctx(arguments={"region": "ap-southeast-1"})) is False


def test_region_in_missing() -> None:
    cond = RegionIn(condition="region_in", arg="region", regions=["us-east"])
    assert evaluate_condition(cond, _ctx(arguments={})) is False


# ── TimeOfDayOutside ──────────────────────────────────────────────────────────


def test_time_of_day_outside() -> None:
    """08:00-18:00 UTC window; a request at 23:00 UTC is outside."""
    cond = TimeOfDayOutside(condition="time_of_day_outside", start="08:00", end="18:00", tz="UTC")
    from zoneinfo import ZoneInfo

    fixed_dt = datetime(2026, 1, 1, 23, 0, 0, tzinfo=ZoneInfo("UTC"))

    with patch("tessera.policy.conditions._now_fn", return_value=fixed_dt):
        result = evaluate_condition(cond, _ctx())

    assert result is True


def test_time_of_day_inside_window() -> None:
    """Request at 12:00 UTC is inside the 08:00-18:00 window → not outside."""
    cond = TimeOfDayOutside(condition="time_of_day_outside", start="08:00", end="18:00", tz="UTC")
    from zoneinfo import ZoneInfo

    fixed_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC"))

    with patch("tessera.policy.conditions._now_fn", return_value=fixed_dt):
        result = evaluate_condition(cond, _ctx())

    assert result is False


# ── MetaFieldEquals ───────────────────────────────────────────────────────────


def test_meta_field_equals_true() -> None:
    cond = MetaFieldEquals(condition="meta_field_equals", key="env", value="prod")
    ctx = _ctx(meta={"env": "prod"})
    assert evaluate_condition(cond, ctx) is True


def test_meta_field_equals_false() -> None:
    cond = MetaFieldEquals(condition="meta_field_equals", key="env", value="prod")
    ctx = _ctx(meta={"env": "dev"})
    assert evaluate_condition(cond, ctx) is False


def test_meta_field_equals_missing() -> None:
    cond = MetaFieldEquals(condition="meta_field_equals", key="env", value="prod")
    assert evaluate_condition(cond, _ctx(meta=None)) is False


def test_meta_field_equals_dot_path() -> None:
    cond = MetaFieldEquals(condition="meta_field_equals", key="tessera_intent.agent", value="cursor")
    ctx = _ctx(meta={"tessera_intent": {"agent": "cursor"}})
    assert evaluate_condition(cond, ctx) is True


# ── AnyOf ─────────────────────────────────────────────────────────────────────


def test_any_of_one_true() -> None:
    cond = AnyOf(
        condition="any_of",
        conditions=[
            ArgEquals(condition="arg_equals", arg="env", value="dev"),
            ArgEquals(condition="arg_equals", arg="env", value="prod"),
        ],
    )
    assert evaluate_condition(cond, _ctx(arguments={"env": "prod"})) is True


def test_any_of_all_false() -> None:
    cond = AnyOf(
        condition="any_of",
        conditions=[
            ArgEquals(condition="arg_equals", arg="env", value="dev"),
            ArgEquals(condition="arg_equals", arg="env", value="staging"),
        ],
    )
    assert evaluate_condition(cond, _ctx(arguments={"env": "prod"})) is False


# ── NoneOf ────────────────────────────────────────────────────────────────────


def test_none_of_all_false_is_true() -> None:
    """NoneOf: True when none of the inner conditions match."""
    cond = NoneOf(
        condition="none_of",
        conditions=[
            ArgEquals(condition="arg_equals", arg="env", value="dev"),
            ArgEquals(condition="arg_equals", arg="env", value="staging"),
        ],
    )
    assert evaluate_condition(cond, _ctx(arguments={"env": "prod"})) is True


def test_none_of_one_true_is_false() -> None:
    """NoneOf: False when at least one inner condition matches."""
    cond = NoneOf(
        condition="none_of",
        conditions=[
            ArgEquals(condition="arg_equals", arg="env", value="prod"),
            ArgEquals(condition="arg_equals", arg="env", value="staging"),
        ],
    )
    assert evaluate_condition(cond, _ctx(arguments={"env": "prod"})) is False


# ── Regex timeout ─────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_regex_timeout_returns_false_and_tags_error() -> None:
    """A ReDoS-prone pattern on a long string should timeout and return False."""
    # Classic ReDoS pattern: catastrophic backtracking
    pattern = r"(x+x+)+y"
    long_input = "x" * 50_000  # no trailing 'y' → forces full backtrack
    cond = ArgMatchesRegex(condition="arg_matches_regex", arg="data", pattern=pattern)
    ctx = _ctx(arguments={"data": long_input}, policy_id="test-policy")

    clear_decision_errors()
    result = evaluate_condition(cond, ctx)

    assert result is False
    errors = get_decision_errors()
    assert any("regex_timeout" in e for e in errors)
