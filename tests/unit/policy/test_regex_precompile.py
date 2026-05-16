"""Unit tests: regex pre-compile stored on Policy/MatchSpec after load (P1-7)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import ArgContainsPattern, ArgMatchesRegex, IntentPurposeMatches


def _write_policy(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_arg_matches_regex_compiled_stored(tmp_path: Path) -> None:
    """After load, ArgMatchesRegex condition has compiled_regex != None."""
    _write_policy(
        tmp_path,
        """\
        id: test-regex
        name: Test regex
        match:
          upstream: "*"
        when:
          - condition: arg_matches_regex
            arg: bucket
            pattern: "^prod-"
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    assert len(policies) == 1
    cond = policies[0].when[0]
    assert isinstance(cond, ArgMatchesRegex)
    assert cond.compiled_regex is not None


def test_arg_contains_pattern_compiled_stored(tmp_path: Path) -> None:
    """After load, ArgContainsPattern condition has compiled_regex != None."""
    _write_policy(
        tmp_path,
        """\
        id: test-contains
        name: Test contains
        match:
          upstream: "*"
        when:
          - condition: arg_contains_pattern
            arg: query
            pattern: "DROP TABLE"
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    cond = policies[0].when[0]
    assert isinstance(cond, ArgContainsPattern)
    assert cond.compiled_regex is not None


def test_intent_purpose_matches_compiled_stored(tmp_path: Path) -> None:
    """After load, IntentPurposeMatches condition has compiled_regex != None."""
    _write_policy(
        tmp_path,
        """\
        id: test-purpose
        name: Test purpose
        match:
          upstream: "*"
        when:
          - condition: intent_purpose_matches
            pattern: "delete.*"
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    cond = policies[0].when[0]
    assert isinstance(cond, IntentPurposeMatches)
    assert cond.compiled_regex is not None


def test_compiled_regex_identity_stable_across_load(tmp_path: Path) -> None:
    """The compiled_regex object is the same type as a regex.Pattern."""
    _write_policy(
        tmp_path,
        """\
        id: test-identity
        name: Test identity
        match:
          upstream: "*"
        when:
          - condition: arg_matches_regex
            arg: key
            pattern: "^secret-"
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    cond = policies[0].when[0]
    assert isinstance(cond, ArgMatchesRegex)
    # Confirm it is a compiled regex pattern object
    assert hasattr(cond.compiled_regex, "search"), "compiled_regex must be a Pattern with .search()"
    # Confirm it matches correctly
    assert cond.compiled_regex.search("secret-key") is not None
    assert cond.compiled_regex.search("public-key") is None


def test_compiled_regex_used_in_evaluation(tmp_path: Path) -> None:
    """evaluate_condition uses precompiled regex — result is the same as runtime compile."""
    from tessera.policy.conditions import evaluate_condition

    _write_policy(
        tmp_path,
        """\
        id: test-eval
        name: Test eval
        match:
          upstream: "*"
        when:
          - condition: arg_matches_regex
            arg: name
            pattern: "^admin"
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    cond = policies[0].when[0]

    ctx_match = {
        "tool_call": {"name": "some_tool", "arguments": {"name": "admin123"}, "_meta": None},
        "policy_id": "test-eval",
    }
    ctx_no_match = {
        "tool_call": {"name": "some_tool", "arguments": {"name": "user456"}, "_meta": None},
        "policy_id": "test-eval",
    }

    assert evaluate_condition(cond, ctx_match) is True
    assert evaluate_condition(cond, ctx_no_match) is False
