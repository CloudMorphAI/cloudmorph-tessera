"""Unit tests for tessera.policy.schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tessera.policy.schema import (
    Action,
    ActionClassIn,
    AnyOf,
    ArgContainsPattern,
    ArgEquals,
    ArgGreaterThan,
    ArgInSet,
    ArgLessThan,
    ArgMatchesRegex,
    ArgSizeGreaterThan,
    Decision,
    IntentClassIn,
    IntentPurposeMatches,
    MetaFieldEquals,
    NoneOf,
    Policy,
    RegionIn,
    TimeOfDayOutside,
    ToolNameIn,
)

# ── Action enum ──────────────────────────────────────────────────────────────


def test_action_enum_values() -> None:
    assert Action.allow == "allow"
    assert Action.block == "block"
    assert Action.log_only == "log_only"
    assert Action.require_approval == "require_approval"


# ── Minimal policy ───────────────────────────────────────────────────────────


def test_policy_valid_minimal() -> None:
    p = Policy.model_validate({"id": "my-policy", "name": "My Policy", "action": "allow"})
    assert p.id == "my-policy"
    assert p.name == "My Policy"
    assert p.action == Action.allow
    assert p.when == []
    assert p.priority == 0
    assert p.description == ""
    assert p.reason == ""


# ── Full fields ──────────────────────────────────────────────────────────────


def test_policy_full_fields() -> None:
    data = {
        "id": "full-policy",
        "name": "Full Policy",
        "description": "A comprehensive policy",
        "match": {
            "upstream": "aws",
            "tool": "aws_s3_*",
            "require_intent": True,
        },
        "when": [{"condition": "arg_equals", "arg": "bucket", "value": "prod"}],
        "action": "block",
        "reason": "blocked in prod",
        "priority": 10,
    }
    p = Policy.model_validate(data)
    assert p.description == "A comprehensive policy"
    assert p.match.upstream == "aws"
    assert p.match.tool == "aws_s3_*"
    assert p.match.require_intent is True
    assert len(p.when) == 1
    assert p.priority == 10
    assert p.reason == "blocked in prod"


# ── ID validation ────────────────────────────────────────────────────────────


def test_policy_invalid_id_pattern_uppercase() -> None:
    with pytest.raises(ValidationError, match="policy id"):
        Policy.model_validate({"id": "My-Policy", "name": "test", "action": "allow"})


def test_policy_invalid_id_too_long() -> None:
    with pytest.raises(ValidationError, match="policy id"):
        Policy.model_validate({"id": "a" * 65, "name": "test", "action": "allow"})


def test_policy_invalid_id_spaces() -> None:
    with pytest.raises(ValidationError, match="policy id"):
        Policy.model_validate({"id": "my policy", "name": "test", "action": "allow"})


# ── Mutual exclusion of tool / tool_pattern ──────────────────────────────────


def test_policy_tool_and_tool_pattern_exclusive() -> None:
    data = {
        "id": "exc-test",
        "name": "Test",
        "action": "allow",
        "match": {"tool": "aws_s3_*", "tool_pattern": r"aws_s3_.*"},
    }
    with pytest.raises(ValidationError, match="mutually exclusive"):
        Policy.model_validate(data)


# ── All 16 conditions parse ──────────────────────────────────────────────────


def _make_policy(condition: dict) -> Policy:
    return Policy.model_validate({"id": "test", "name": "t", "action": "allow", "when": [condition]})


def test_condition_arg_equals() -> None:
    p = _make_policy({"condition": "arg_equals", "arg": "bucket", "value": "prod"})
    cond = p.when[0]
    assert isinstance(cond, ArgEquals)
    assert cond.arg == "bucket"
    assert cond.value == "prod"


def test_condition_arg_greater_than() -> None:
    p = _make_policy({"condition": "arg_greater_than", "arg": "size", "value": 100})
    assert isinstance(p.when[0], ArgGreaterThan)


def test_condition_arg_less_than() -> None:
    p = _make_policy({"condition": "arg_less_than", "arg": "cost", "value": 50})
    assert isinstance(p.when[0], ArgLessThan)


def test_condition_arg_matches_regex() -> None:
    p = _make_policy({"condition": "arg_matches_regex", "arg": "name", "pattern": r"\d+"})
    assert isinstance(p.when[0], ArgMatchesRegex)
    assert p.when[0].pattern == r"\d+"


def test_condition_arg_in_set() -> None:
    p = _make_policy({"condition": "arg_in_set", "arg": "region", "values": ["us-east-1", "eu-west-1"]})
    cond = p.when[0]
    assert isinstance(cond, ArgInSet)
    assert "us-east-1" in cond.values


def test_condition_arg_contains_pattern() -> None:
    p = _make_policy({"condition": "arg_contains_pattern", "arg": "query", "pattern": r"DROP"})
    assert isinstance(p.when[0], ArgContainsPattern)


def test_condition_arg_size_greater_than() -> None:
    p = _make_policy({"condition": "arg_size_greater_than", "arg": "data", "bytes": 1024})
    cond = p.when[0]
    assert isinstance(cond, ArgSizeGreaterThan)
    assert cond.bytes == 1024


def test_condition_tool_name_in() -> None:
    p = _make_policy({"condition": "tool_name_in", "values": ["aws_s3_delete_object"]})
    cond = p.when[0]
    assert isinstance(cond, ToolNameIn)
    assert "aws_s3_delete_object" in cond.values


def test_condition_action_class_in() -> None:
    p = _make_policy({"condition": "action_class_in", "values": ["write.delete"]})
    assert isinstance(p.when[0], ActionClassIn)


def test_condition_intent_class_in() -> None:
    p = _make_policy({"condition": "intent_class_in", "values": ["read.list"]})
    assert isinstance(p.when[0], IntentClassIn)


def test_condition_intent_purpose_matches() -> None:
    p = _make_policy({"condition": "intent_purpose_matches", "pattern": r"cost.*report"})
    assert isinstance(p.when[0], IntentPurposeMatches)


def test_condition_region_in() -> None:
    p = _make_policy({"condition": "region_in", "arg": "region", "regions": ["eu-"]})
    cond = p.when[0]
    assert isinstance(cond, RegionIn)
    assert cond.regions == ["eu-"]


def test_condition_time_of_day_outside() -> None:
    p = _make_policy({"condition": "time_of_day_outside", "start": "09:00", "end": "17:00", "tz": "UTC"})
    cond = p.when[0]
    assert isinstance(cond, TimeOfDayOutside)
    assert cond.tz == "UTC"


def test_condition_meta_field_equals() -> None:
    p = _make_policy({"condition": "meta_field_equals", "key": "environment", "value": "prod"})
    cond = p.when[0]
    assert isinstance(cond, MetaFieldEquals)
    assert cond.key == "environment"


def test_condition_any_of() -> None:
    p = _make_policy(
        {
            "condition": "any_of",
            "conditions": [
                {"condition": "arg_equals", "arg": "env", "value": "prod"},
                {"condition": "tool_name_in", "values": ["dangerous_tool"]},
            ],
        }
    )
    cond = p.when[0]
    assert isinstance(cond, AnyOf)
    assert len(cond.conditions) == 2


def test_condition_none_of() -> None:
    p = _make_policy(
        {
            "condition": "none_of",
            "conditions": [
                {"condition": "arg_equals", "arg": "env", "value": "dev"},
            ],
        }
    )
    cond = p.when[0]
    assert isinstance(cond, NoneOf)
    assert len(cond.conditions) == 1


# ── Decision dataclass ───────────────────────────────────────────────────────


def test_decision_dataclass() -> None:
    d = Decision(action=Action.block, reason="test reason", policy_id="my-policy")
    assert d.action == Action.block
    assert d.reason == "test reason"
    assert d.policy_id == "my-policy"
    assert d.decision_error is None


def test_decision_dataclass_with_error() -> None:
    d = Decision(
        action=Action.allow,
        reason="",
        policy_id=None,
        decision_error="regex_timeout",
    )
    assert d.decision_error == "regex_timeout"
    assert d.policy_id is None
