"""Unit tests: condition cost-tier ordering after policy load (P1-8)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import (
    AnyOf,
    ArgEquals,
    ArgMatchesRegex,
    NoneOf,
    PredictedCost,
)


def _write_policy(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_basic_ordering_cheap_first(tmp_path: Path) -> None:
    """[predicted_cost, arg_equals, arg_matches_regex] sorts to [arg_equals, arg_matches_regex, predicted_cost]."""
    _write_policy(
        tmp_path,
        """\
        id: test-order
        name: Test ordering
        match:
          upstream: "*"
        when:
          - condition: predicted_cost
            usd_threshold: 1.0
          - condition: arg_equals
            arg: action
            value: delete
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
    conditions = policies[0].when
    assert len(conditions) == 3
    # Expected order: arg_equals (tier 0), arg_matches_regex (tier 2), predicted_cost (tier 3)
    assert isinstance(conditions[0], ArgEquals), f"Expected ArgEquals, got {type(conditions[0]).__name__}"
    assert isinstance(conditions[1], ArgMatchesRegex), f"Expected ArgMatchesRegex, got {type(conditions[1]).__name__}"
    assert isinstance(conditions[2], PredictedCost), f"Expected PredictedCost, got {type(conditions[2]).__name__}"


def test_any_of_inner_sorted(tmp_path: Path) -> None:
    """any_of inner conditions are also sorted by cost tier."""
    _write_policy(
        tmp_path,
        """\
        id: test-anyof-order
        name: Test any_of ordering
        match:
          upstream: "*"
        when:
          - condition: any_of
            conditions:
              - condition: predicted_cost
                usd_threshold: 0.5
              - condition: arg_equals
                arg: region
                value: us-east-1
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    any_of_cond = policies[0].when[0]
    assert isinstance(any_of_cond, AnyOf)
    inner = any_of_cond.conditions
    assert isinstance(inner[0], ArgEquals), f"Expected ArgEquals first, got {type(inner[0]).__name__}"
    assert isinstance(inner[1], PredictedCost), f"Expected PredictedCost second, got {type(inner[1]).__name__}"


def test_none_of_inner_sorted(tmp_path: Path) -> None:
    """none_of inner conditions are also sorted by cost tier."""
    _write_policy(
        tmp_path,
        """\
        id: test-noneof-order
        name: Test none_of ordering
        match:
          upstream: "*"
        when:
          - condition: none_of
            conditions:
              - condition: arg_matches_regex
                arg: key
                pattern: "safe-.*"
              - condition: arg_equals
                arg: env
                value: prod
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    none_of_cond = policies[0].when[0]
    assert isinstance(none_of_cond, NoneOf)
    inner = none_of_cond.conditions
    assert isinstance(inner[0], ArgEquals), f"Expected ArgEquals first, got {type(inner[0]).__name__}"
    assert isinstance(inner[1], ArgMatchesRegex), f"Expected ArgMatchesRegex second, got {type(inner[1]).__name__}"


def test_already_sorted_unchanged(tmp_path: Path) -> None:
    """A policy already in cost order remains stable (sort is stable)."""
    _write_policy(
        tmp_path,
        """\
        id: test-stable
        name: Test stable sort
        match:
          upstream: "*"
        when:
          - condition: arg_equals
            arg: action
            value: delete
          - condition: arg_matches_regex
            arg: resource
            pattern: "arn:aws:.*"
          - condition: predicted_cost
            usd_threshold: 2.0
        action: block
        reason: blocked
        """,
    )
    loader = FilesystemPolicyLoader(tmp_path)
    policies = loader.load_all()
    conditions = policies[0].when
    # Order should be the same — arg_equals → arg_matches_regex → predicted_cost
    assert isinstance(conditions[0], ArgEquals)
    assert isinstance(conditions[1], ArgMatchesRegex)
    assert isinstance(conditions[2], PredictedCost)
