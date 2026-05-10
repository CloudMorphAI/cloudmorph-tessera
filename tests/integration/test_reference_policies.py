"""Integration test: load each reference policy, run paired fixtures, assert engine output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action

POLICIES_DIR = Path(__file__).parent.parent.parent / "policies"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "policies"


def load_single_policy(policy_id: str) -> PolicyEngine:
    """Load only the named policy from policies/ dir and return an engine with allow default."""
    loader = FilesystemPolicyLoader(POLICIES_DIR)
    all_policies = loader.load_all("default")
    matching = [p for p in all_policies if p.id == policy_id]
    return PolicyEngine(matching, default_action=Action.allow)


def load_fixture(fixture_path: Path) -> dict:
    return json.loads(fixture_path.read_text(encoding="utf-8"))


# Parametrize over all fixture files
def _collect_fixtures():
    cases = []
    for policy_dir in sorted(FIXTURES_DIR.iterdir()):
        if not policy_dir.is_dir():
            continue
        policy_id = policy_dir.name
        for outcome in ("pass", "fail"):
            outcome_dir = policy_dir / outcome
            if not outcome_dir.exists():
                continue
            for fixture_file in sorted(outcome_dir.glob("*.json")):
                cases.append((policy_id, fixture_file))
    return cases


@pytest.mark.parametrize("policy_id,fixture_path", _collect_fixtures())
def test_reference_policy_fixture(policy_id: str, fixture_path: Path) -> None:
    fixture = load_fixture(fixture_path)
    engine = load_single_policy(policy_id)
    context = fixture["context"]
    context["policy_id"] = policy_id
    decision = engine.evaluate(context)
    expected = fixture["expected_action"]
    assert decision.action.value == expected, (
        f"Policy {policy_id}: expected {expected!r}, got {decision.action.value!r} "
        f"(reason: {decision.reason!r})"
    )
