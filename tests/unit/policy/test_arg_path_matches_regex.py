"""Unit tests for the arg_path_matches_regex condition (v0.5.0)."""

from __future__ import annotations

from tessera.policy.conditions import evaluate_condition
from tessera.policy.schema import ArgPathMatchesRegex


def _ctx(name: str = "test_tool", arguments: dict | None = None, meta: dict | None = None) -> dict:
    return {
        "tool_call": {
            "name": name,
            "arguments": arguments or {},
            "_meta": meta,
        },
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "policy_id": None,
    }


# ── Basic path navigation ─────────────────────────────────────────────────────


def test_dot_path_single_level_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={"HttpTokens": "optional"})
    assert evaluate_condition(cond, ctx) is True


def test_dot_path_nested_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="MetadataOptions.HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={"MetadataOptions": {"HttpTokens": "optional", "HttpEndpoint": "enabled"}})
    assert evaluate_condition(cond, ctx) is True


def test_dot_path_nested_no_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="MetadataOptions.HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={"MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"}})
    assert evaluate_condition(cond, ctx) is False


def test_dot_path_missing_top_level_returns_false() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="MetadataOptions.HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={"ImageId": "ami-12345"})
    assert evaluate_condition(cond, ctx) is False


def test_dot_path_missing_nested_key_returns_false() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="MetadataOptions.HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={"MetadataOptions": {"HttpEndpoint": "enabled"}})
    assert evaluate_condition(cond, ctx) is False


def test_dot_path_empty_arguments_returns_false() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="MetadataOptions.HttpTokens",
        pattern="optional",
    )
    ctx = _ctx(arguments={})
    assert evaluate_condition(cond, ctx) is False


# ── Pattern matching ──────────────────────────────────────────────────────────


def test_case_insensitive_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="Config.Mode",
        pattern="(?i)optional",
    )
    ctx = _ctx(arguments={"Config": {"Mode": "OPTIONAL"}})
    assert evaluate_condition(cond, ctx) is True


def test_anchored_pattern_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="HttpTokens",
        pattern="^optional$",
    )
    ctx = _ctx(arguments={"HttpTokens": "optional"})
    assert evaluate_condition(cond, ctx) is True


def test_anchored_pattern_no_partial_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="HttpTokens",
        pattern="^optional$",
    )
    ctx = _ctx(arguments={"HttpTokens": "optional-extra"})
    assert evaluate_condition(cond, ctx) is False


# ── Precompiled regex support ─────────────────────────────────────────────────


def test_precompiled_regex_is_used() -> None:
    import regex as _regex

    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="Nested.Key",
        pattern="^test$",
    )
    cond.compiled_regex = _regex.compile("^test$", _regex.VERSION1)
    ctx = _ctx(arguments={"Nested": {"Key": "test"}})
    assert evaluate_condition(cond, ctx) is True


# ── Deep path ─────────────────────────────────────────────────────────────────


def test_three_level_path_match() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="A.B.C",
        pattern="value",
    )
    ctx = _ctx(arguments={"A": {"B": {"C": "some value here"}}})
    assert evaluate_condition(cond, ctx) is True


def test_three_level_path_missing_returns_false() -> None:
    cond = ArgPathMatchesRegex(
        condition="arg_path_matches_regex",
        arg_path="A.B.C",
        pattern="value",
    )
    ctx = _ctx(arguments={"A": {"B": {}}})
    assert evaluate_condition(cond, ctx) is False
